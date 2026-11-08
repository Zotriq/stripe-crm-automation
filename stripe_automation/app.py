"""Stripe webhook endpoint + outbox worker.

POST /stripe/webhook     verify signature -> record event + actions (idempotent) -> 200
                         then run due actions in the background
GET  /admin/status       counts of pending / done / dead actions
GET  /admin/dead         actions that failed every retry
POST /admin/retry/{id}   put a dead action back in the queue
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Protocol

import stripe
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

from .rules import actions_for
from .store import Store

log = logging.getLogger("stripe_automation")


class CRM(Protocol):
    def upsert_contact(self, email: str, properties: dict[str, Any]) -> None: ...


class Chat(Protocol):
    def post(self, text: str) -> None: ...


class Mail(Protocol):
    def send(self, to: str, template: str, **values: Any) -> None: ...


@dataclass
class Services:
    crm: CRM
    chat: Chat
    mail: Mail


def run_due_actions(store: Store, services: Services) -> dict[str, int]:
    done = failed = 0
    for row in store.due_actions():
        payload = json.loads(row["payload"])
        try:
            if row["kind"] == "crm_upsert":
                services.crm.upsert_contact(payload["email"], payload["properties"])
            elif row["kind"] == "slack":
                services.chat.post(payload["text"])
            elif row["kind"] == "email":
                services.mail.send(payload.pop("to"), payload.pop("template"), **payload)
            else:
                raise ValueError(f"unknown action {row['kind']}")
            store.mark_done(row["id"])
            done += 1
        except Exception as exc:  # retried later with backoff
            status = store.mark_failed(row["id"], f"{type(exc).__name__}: {exc}")
            log.warning("action %s (%s) failed -> %s: %s", row["id"], row["kind"], status, exc)
            failed += 1
    return {"done": done, "failed": failed}


def create_app(store: Store, services: Services, *, webhook_secret: str, admin_token: str) -> FastAPI:
    app = FastAPI(title="Stripe → CRM automation")

    def require_admin(token: str | None) -> None:
        if not admin_token or token != admin_token:
            raise HTTPException(403, "admin token required")

    @app.post("/stripe/webhook")
    async def webhook(request: Request, background: BackgroundTasks, stripe_signature: str | None = Header(default=None)) -> dict:
        payload = await request.body()
        try:
            event = stripe.Webhook.construct_event(payload, stripe_signature or "", webhook_secret)
        except (ValueError, stripe.SignatureVerificationError):
            raise HTTPException(400, "invalid signature")

        event = event.to_dict()
        actions = actions_for(event)
        is_new = store.record_event(event["id"], event["type"], actions)
        if is_new and actions:
            background.add_task(run_due_actions, store, services)
        return {"received": True, "duplicate": not is_new, "actions": [k for k, _ in actions] if is_new else []}

    @app.get("/admin/status")
    def status(x_admin_token: str | None = Header(default=None)) -> dict:
        require_admin(x_admin_token)
        return store.summary()

    @app.get("/admin/dead")
    def dead(x_admin_token: str | None = Header(default=None)) -> list[dict]:
        require_admin(x_admin_token)
        return store.dead_letters()

    @app.post("/admin/retry/{action_id}")
    def retry(action_id: int, background: BackgroundTasks, x_admin_token: str | None = Header(default=None)) -> dict:
        require_admin(x_admin_token)
        if not store.requeue(action_id):
            raise HTTPException(404, "no dead action with that id")
        background.add_task(run_due_actions, store, services)
        return {"requeued": action_id}

    return app


def app_from_env() -> FastAPI:
    """uvicorn stripe_automation.app:app_from_env --factory"""
    from dotenv import load_dotenv

    from .integrations import HubSpot, Mailer, Slack

    load_dotenv()
    services = Services(
        crm=HubSpot(os.environ["HUBSPOT_TOKEN"]),
        chat=Slack(os.environ["SLACK_WEBHOOK_URL"]),
        mail=Mailer(os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "587")), os.environ["SMTP_USER"],
                    os.environ["SMTP_PASSWORD"], os.environ["MAIL_FROM"]),
    )
    return create_app(Store(os.getenv("DATABASE_PATH", "automation.db")), services,
                      webhook_secret=os.environ["STRIPE_WEBHOOK_SECRET"], admin_token=os.getenv("ADMIN_TOKEN", ""))

"""Clients for HubSpot, Slack and email. Each raises on failure so the outbox can retry."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Any

import httpx

TEMPLATES = {
    "welcome": (
        "Welcome aboard, {name}!",
        "Hi {name},\n\nThanks for your purchase of {amount}. Your account is ready.\n"
        "Reply to this email if you need anything.\n\nThe Team",
    ),
    "payment_failed": (
        "Action needed: your payment didn't go through",
        "Hi,\n\nWe couldn't process your payment of {amount}. "
        "You can update your card and pay here: {url}\n\nThe Team",
    ),
}


class HubSpot:
    def __init__(self, token: str, client: httpx.Client | None = None):
        self.client = client or httpx.Client(base_url="https://api.hubapi.com", timeout=15)
        self.headers = {"Authorization": f"Bearer {token}"}

    def upsert_contact(self, email: str, properties: dict[str, Any]) -> None:
        props = {k: v for k, v in properties.items() if v not in (None, "")}
        response = self.client.post(
            "/crm/v3/objects/contacts/batch/upsert",
            headers=self.headers,
            json={"inputs": [{"id": email, "idProperty": "email", "properties": props}]},
        )
        response.raise_for_status()


class Slack:
    def __init__(self, webhook_url: str, client: httpx.Client | None = None):
        self.url = webhook_url
        self.client = client or httpx.Client(timeout=10)

    def post(self, text: str) -> None:
        self.client.post(self.url, json={"text": text}).raise_for_status()


class Mailer:
    def __init__(self, host: str, port: int, user: str, password: str, sender: str):
        self.host, self.port, self.user, self.password, self.sender = host, port, user, password, sender

    def send(self, to: str, template: str, **values: Any) -> None:
        subject, body = TEMPLATES[template]
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, to, subject.format(**values)
        msg.set_content(body.format(**values))
        with smtplib.SMTP(self.host, self.port, timeout=15) as smtp:
            smtp.starttls()
            smtp.login(self.user, self.password)
            smtp.send_message(msg)

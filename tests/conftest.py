import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from stripe_automation.app import Services, create_app
from stripe_automation.store import Store

SECRET = "whsec_test_secret"


class FakeCRM:
    def __init__(self):
        self.contacts, self.fail_times = {}, 0

    def upsert_contact(self, email, properties):
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError("HubSpot 502 Bad Gateway")
        self.contacts.setdefault(email, {}).update(properties)


class FakeChat:
    def __init__(self):
        self.messages = []

    def post(self, text):
        self.messages.append(text)


class FakeMail:
    def __init__(self):
        self.sent = []

    def send(self, to, template, **values):
        self.sent.append({"to": to, "template": template, **values})


@pytest.fixture
def services():
    return Services(crm=FakeCRM(), chat=FakeChat(), mail=FakeMail())


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "test.db")


@pytest.fixture
def client(store, services):
    return TestClient(create_app(store, services, webhook_secret=SECRET, admin_token="admin"))


def signed(event: dict, secret: str = SECRET) -> tuple[bytes, dict]:
    """Build a request body and Stripe-Signature header exactly like Stripe does."""
    body = json.dumps(event).encode()
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return body, {"Stripe-Signature": f"t={ts},v1={sig}", "Content-Type": "application/json"}


def send(client, event, secret=SECRET):
    body, headers = signed(event, secret)
    return client.post("/stripe/webhook", content=body, headers=headers)


def event(event_type: str, obj: dict, event_id: str = "evt_1") -> dict:
    return {"id": event_id, "object": "event", "type": event_type, "data": {"object": obj}}


CHECKOUT = {
    "id": "cs_test_1", "object": "checkout.session", "payment_status": "paid",
    "amount_total": 4900, "currency": "usd",
    "customer_details": {"email": "sara@brightlane.co", "name": "Sara Malik", "phone": "+15550001111"},
}

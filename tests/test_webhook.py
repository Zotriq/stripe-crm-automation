import datetime as dt

from stripe_automation.app import run_due_actions
from stripe_automation.store import MAX_ATTEMPTS
from tests.conftest import CHECKOUT, event, send


def test_rejects_bad_signature(client, services):
    assert send(client, event("checkout.session.completed", CHECKOUT), secret="whsec_wrong").status_code == 400
    assert services.chat.messages == []


def test_new_customer_flow(client, services):
    res = send(client, event("checkout.session.completed", CHECKOUT))
    assert res.status_code == 200
    assert res.json()["actions"] == ["crm_upsert", "email", "slack"]

    assert services.crm.contacts["sara@brightlane.co"] == {
        "firstname": "Sara", "lastname": "Malik", "phone": "+15550001111", "lifecyclestage": "customer"}
    assert services.mail.sent == [{"to": "sara@brightlane.co", "template": "welcome", "name": "Sara", "amount": "$49.00"}]
    assert services.chat.messages == [":moneybag: New customer: Sara Malik paid $49.00"]


def test_stripe_retries_are_ignored(client, services):
    send(client, event("checkout.session.completed", CHECKOUT))
    again = send(client, event("checkout.session.completed", CHECKOUT)).json()
    assert again == {"received": True, "duplicate": True, "actions": []}
    assert len(services.mail.sent) == 1  # the customer gets exactly one welcome email


def test_crm_outage_is_retried_without_repeating_other_actions(client, services, store):
    services.crm.fail_times = 2
    send(client, event("checkout.session.completed", CHECKOUT))
    assert services.crm.contacts == {}
    assert len(services.mail.sent) == 1 and len(services.chat.messages) == 1

    # The failed upsert is scheduled with backoff, not retried immediately.
    assert store.due_actions() == []
    # Simulate time passing until the retry is due.
    store.conn.execute("UPDATE actions SET next_attempt_at = '2000-01-01' WHERE status = 'pending'")
    run_due_actions(store, services)   # fails again (2nd outage)
    store.conn.execute("UPDATE actions SET next_attempt_at = '2000-01-01' WHERE status = 'pending'")
    run_due_actions(store, services)   # succeeds

    assert "sara@brightlane.co" in services.crm.contacts
    assert len(services.mail.sent) == 1  # email was NOT re-sent during CRM retries
    assert store.summary() == {"done": 3}


def test_permanent_failure_goes_to_dead_letter_and_can_be_requeued(client, services, store):
    services.crm.fail_times = 99
    send(client, event("checkout.session.completed", CHECKOUT))
    for _ in range(MAX_ATTEMPTS):
        store.conn.execute("UPDATE actions SET next_attempt_at = '2000-01-01' WHERE status = 'pending'")
        run_due_actions(store, services)

    dead = client.get("/admin/dead", headers={"X-Admin-Token": "admin"}).json()
    assert [(d["kind"], d["attempts"]) for d in dead] == [("crm_upsert", MAX_ATTEMPTS)]
    assert "HubSpot 502" in dead[0]["last_error"]

    services.crm.fail_times = 0
    assert client.post(f"/admin/retry/{dead[0]['id']}", headers={"X-Admin-Token": "admin"}).status_code == 200
    assert "sara@brightlane.co" in services.crm.contacts
    assert client.get("/admin/dead", headers={"X-Admin-Token": "admin"}).json() == []


def test_backoff_doubles(store):
    store.record_event("evt_x", "t", [("slack", {"text": "x"})])
    [row] = store.due_actions()
    now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    delays = []
    for _ in range(3):
        store.mark_failed(row["id"], "err", now=now)
        nxt = store.conn.execute("SELECT next_attempt_at FROM actions").fetchone()[0]
        delays.append((dt.datetime.fromisoformat(nxt) - now).total_seconds())
    assert delays == [30, 60, 120]


def test_admin_endpoints_need_token(client):
    assert client.get("/admin/status").status_code == 403
    assert client.get("/admin/status", headers={"X-Admin-Token": "admin"}).status_code == 200

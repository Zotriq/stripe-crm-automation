from stripe_automation.rules import actions_for
from tests.conftest import CHECKOUT, event


def kinds(actions):
    return [k for k, _ in actions]


def test_unpaid_checkout_waits():
    assert actions_for(event("checkout.session.completed", {**CHECKOUT, "payment_status": "unpaid"})) == []


def test_checkout_without_email_alerts_team():
    actions = actions_for(event("checkout.session.completed", {**CHECKOUT, "customer_details": {}}))
    assert kinds(actions) == ["slack"] and "without an email" in actions[0][1]["text"]


def test_payment_failed_emails_customer_with_invoice_link():
    obj = {"customer_email": "tom@harbortech.io", "amount_due": 14700, "currency": "usd", "attempt_count": 2,
           "hosted_invoice_url": "https://invoice.stripe.com/i/abc"}
    actions = dict(actions_for(event("invoice.payment_failed", obj)))
    assert actions["slack"]["text"] == ":rotating_light: Payment failed: tom@harbortech.io owes $147.00 (attempt 2)"
    assert actions["email"]["url"] == "https://invoice.stripe.com/i/abc"


def test_renewal_but_not_first_invoice():
    obj = {"customer_email": "a@b.co", "amount_paid": 4900, "currency": "usd"}
    assert kinds(actions_for(event("invoice.paid", {**obj, "billing_reason": "subscription_cycle"}))) == ["slack"]
    assert actions_for(event("invoice.paid", {**obj, "billing_reason": "subscription_create"})) == []


def test_refund_and_churn_and_currency_formatting():
    refund = actions_for(event("charge.refunded", {"amount_refunded": 125050, "currency": "eur", "billing_details": {"email": "x@y.de"}}))
    assert refund[0][1]["text"] == ":leftwards_arrow_with_hook: Refund: €1,250.50 to x@y.de"
    churn = actions_for(event("customer.subscription.deleted", {"customer": "cus_9", "cancellation_details": {"reason": "too_expensive"}}))
    assert "too_expensive" in churn[0][1]["text"]
    pkr = actions_for(event("charge.refunded", {"amount_refunded": 500000, "currency": "pkr", "receipt_email": "z@z.pk"}))
    assert "5,000.00 PKR" in pkr[0][1]["text"]


def test_unhandled_events_do_nothing():
    assert actions_for(event("customer.created", {})) == []

"""Business rules: which Stripe events trigger which actions.

Pure functions (event dict in, list of actions out), so every rule is easy
to unit-test and to change for a new client.
"""

from __future__ import annotations

from typing import Any

Action = tuple[str, dict[str, Any]]


def _money(amount: int | None, currency: str | None) -> str:
    if amount is None:
        return "?"
    symbol = {"usd": "$", "eur": "€", "gbp": "£"}.get((currency or "").lower(), "")
    value = amount / 100
    return f"{symbol}{value:,.2f}" if symbol else f"{value:,.2f} {(currency or '').upper()}"


def _split_name(name: str | None) -> tuple[str, str]:
    parts = (name or "").strip().split(" ", 1)
    return parts[0], parts[1] if len(parts) > 1 else ""


def actions_for(event: dict[str, Any]) -> list[Action]:
    kind = event["type"]
    obj = event["data"]["object"]

    if kind == "checkout.session.completed":
        if obj.get("payment_status") not in (None, "paid", "no_payment_required"):
            return []  # e.g. delayed bank payments: wait for async_payment_succeeded
        details = obj.get("customer_details") or {}
        email = details.get("email")
        if not email:
            return [("slack", {"text": f":warning: Checkout {obj.get('id')} completed without an email address"})]
        first, last = _split_name(details.get("name"))
        amount = _money(obj.get("amount_total"), obj.get("currency"))
        return [
            ("crm_upsert", {"email": email, "properties": {"firstname": first, "lastname": last, "phone": details.get("phone") or "", "lifecyclestage": "customer"}}),
            ("email", {"to": email, "template": "welcome", "name": first or "there", "amount": amount}),
            ("slack", {"text": f":moneybag: New customer: {details.get('name') or email} paid {amount}"}),
        ]

    if kind == "invoice.paid" and obj.get("billing_reason") == "subscription_cycle":
        return [("slack", {"text": f":repeat: Renewal: {obj.get('customer_email')} paid {_money(obj.get('amount_paid'), obj.get('currency'))}"})]

    if kind == "invoice.payment_failed":
        email = obj.get("customer_email")
        amount = _money(obj.get("amount_due"), obj.get("currency"))
        actions: list[Action] = [("slack", {"text": f":rotating_light: Payment failed: {email} owes {amount} (attempt {obj.get('attempt_count', 1)})"})]
        if email:
            actions.append(("email", {"to": email, "template": "payment_failed", "amount": amount, "url": obj.get("hosted_invoice_url", "")}))
        return actions

    if kind == "charge.refunded":
        email = (obj.get("billing_details") or {}).get("email") or obj.get("receipt_email")
        return [("slack", {"text": f":leftwards_arrow_with_hook: Refund: {_money(obj.get('amount_refunded'), obj.get('currency'))} to {email or 'unknown customer'}"})]

    if kind == "customer.subscription.deleted":
        return [("slack", {"text": f":wave: Subscription cancelled: customer {obj.get('customer')} (reason: {(obj.get('cancellation_details') or {}).get('reason') or 'not given'})"})]

    return []

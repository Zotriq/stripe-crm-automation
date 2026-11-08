# Stripe → CRM Automation

![tests](https://github.com/Zotriq/stripe-crm-automation/actions/workflows/tests.yml/badge.svg)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)
![Stripe](https://img.shields.io/badge/Stripe-webhooks-635BFF?style=flat-square&logo=stripe&logoColor=white)
![HubSpot](https://img.shields.io/badge/HubSpot-CRM-FF7A59?style=flat-square&logo=hubspot&logoColor=white)
![Slack](https://img.shields.io/badge/Slack-alerts-4A154B?style=flat-square&logo=slack&logoColor=white)

When money moves in Stripe, the rest of the business finds out automatically:

| Stripe event | What happens |
|---|---|
| `checkout.session.completed` | Contact created/updated in **HubSpot** as a customer · **welcome email** · **Slack** "new customer" alert |
| `invoice.paid` (renewal) | Slack renewal alert |
| `invoice.payment_failed` | Slack alert + **email to the customer with a link to update their card** |
| `charge.refunded` | Slack refund alert |
| `customer.subscription.deleted` | Slack churn alert with the cancellation reason |

## Built so it never loses a sale or double-emails a customer

Simple Zapier-style webhooks break in three ways: forged requests, Stripe's automatic retries causing
duplicates, and a CRM outage silently dropping data. This service handles all three:

```
Stripe ──► POST /stripe/webhook
             │ 1. verify Stripe-Signature (HMAC)              → 400 if forged
             │ 2. event id already seen?                       → 200 "duplicate", do nothing
             │ 3. save event + its actions in ONE transaction  (outbox)
             │ 4. return 200 immediately, so Stripe never times out
             ▼
        outbox worker ── crm_upsert ──► HubSpot   ┐
                      ── email ───────► SMTP      ├─ each action succeeds or retries on its own
                      ── slack ───────► Slack     ┘  backoff 30s → 1m → 2m → 4m → 8m → dead letter
```

- **Idempotent.** Stripe retries webhooks for up to 3 days. The event ID is stored, so a customer never gets two welcome emails.
- **Independent retries.** If HubSpot is down, only the CRM update is retried. The email and Slack alert aren't sent again.
- **Dead-letter queue.** After 6 failed attempts an action is parked, visible at `/admin/dead` with the last error, and can be re-queued with one call once the problem is fixed.
- **Rules are plain functions.** [`rules.py`](stripe_automation/rules.py) maps events to actions, so changing what happens for a client is a small, tested change.

## Run it

```bash
git clone https://github.com/Zotriq/stripe-crm-automation.git
cd stripe-crm-automation
pip install -e .
cp .env.example .env      # Stripe webhook secret, HubSpot token, Slack webhook, SMTP

uvicorn stripe_automation.app:app_from_env --factory --port 8000
python -m stripe_automation worker --every 30     # keeps retrying failed actions
```

Test with real Stripe events locally using the [Stripe CLI](https://docs.stripe.com/stripe-cli):

```bash
stripe listen --forward-to localhost:8000/stripe/webhook
stripe trigger checkout.session.completed
```

### Admin endpoints (`X-Admin-Token` header)

| Endpoint | Purpose |
|---|---|
| `GET /admin/status` | Counts of pending / done / dead actions |
| `GET /admin/dead` | Actions that failed every retry, with the last error |
| `POST /admin/retry/{id}` | Re-queue a dead action |

## Tests

```bash
pip install -e ".[dev]"
pytest
```

15 tests with **real Stripe signatures** (HMAC-signed exactly as Stripe does) and fake HubSpot, Slack and email
services that can be made to fail. They cover forged requests, the full new-customer flow, duplicate events, a
CRM outage being retried **without re-sending the email**, exponential backoff timing, dead-lettering and
re-queueing, every business rule (including unpaid checkouts and currency formatting), and the exact HubSpot
API request format.

## Project structure

```
stripe_automation/
  app.py            webhook endpoint, outbox runner, admin endpoints
  rules.py          Stripe event → actions (the business logic)
  store.py          SQLite event log + outbox with retry/backoff/dead-letter
  integrations.py   HubSpot (batch upsert by email), Slack, SMTP email
  __main__.py       retry worker
tests/
```

## License

MIT © Zohaib Tariq

"""Retry worker. Run it from cron or as a sidecar so failed actions keep being retried:

    python -m stripe_automation worker --every 30
"""

import argparse
import logging
import time

from .app import Services, run_due_actions


def main() -> None:
    import os

    from dotenv import load_dotenv

    from .integrations import HubSpot, Mailer, Slack
    from .store import Store

    load_dotenv()
    parser = argparse.ArgumentParser(prog="stripe_automation")
    parser.add_argument("command", choices=["worker"])
    parser.add_argument("--every", type=int, default=30, help="seconds between passes")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    store = Store(os.getenv("DATABASE_PATH", "automation.db"))
    services = Services(
        crm=HubSpot(os.environ["HUBSPOT_TOKEN"]),
        chat=Slack(os.environ["SLACK_WEBHOOK_URL"]),
        mail=Mailer(os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "587")), os.environ["SMTP_USER"],
                    os.environ["SMTP_PASSWORD"], os.environ["MAIL_FROM"]),
    )
    while True:
        result = run_due_actions(store, services)
        if result["done"] or result["failed"]:
            logging.info("worker pass: %s", result)
        time.sleep(args.every)


main()

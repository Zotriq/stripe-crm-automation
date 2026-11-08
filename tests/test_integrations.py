import json

import httpx

from stripe_automation.integrations import HubSpot, Slack


def test_hubspot_upsert_request_shape():
    seen = {}

    def handler(request):
        seen["path"], seen["auth"], seen["body"] = request.url.path, request.headers["authorization"], json.loads(request.content)
        return httpx.Response(200, json={"status": "COMPLETE"})

    client = httpx.Client(base_url="https://api.hubapi.com", transport=httpx.MockTransport(handler))
    HubSpot("pat-123", client).upsert_contact("a@b.co", {"firstname": "A", "phone": "", "lifecyclestage": "customer"})
    assert seen["path"] == "/crm/v3/objects/contacts/batch/upsert"
    assert seen["auth"] == "Bearer pat-123"
    assert seen["body"] == {"inputs": [{"id": "a@b.co", "idProperty": "email", "properties": {"firstname": "A", "lifecyclestage": "customer"}}]}


def test_errors_raise_so_outbox_retries():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    try:
        Slack("https://hooks.slack.com/x", client).post("hi")
        raise AssertionError("expected an error")
    except httpx.HTTPStatusError:
        pass

"""Chalice routes: a thin HTTP layer over ``chalicelib.ingest``.

Responses:
  200  delivery stored (new, duplicate, or stored but processing failed -> retried later)
  400  delivery cannot be accepted (missing headers, unparseable body)
  5xx  the raw delivery could not be stored, so the sender should retry
"""

from __future__ import annotations

from dataclasses import asdict

from chalice import BadRequestError, Chalice

from chalicelib import db
from chalicelib.ingest import InvalidWebhook, ingest_recharge, ingest_shopify

app = Chalice(app_name="shipinsure-covered-orders")

_JSON = ["application/json"]


@app.route("/webhooks/shopify", methods=["POST"], content_types=_JSON)
def shopify_webhook() -> dict:
    request = app.current_request
    try:
        result = ingest_shopify(db.get_session_factory(), dict(request.headers), request.raw_body)
    except InvalidWebhook as exc:
        raise BadRequestError(str(exc)) from exc
    return asdict(result)


# Recharge does not document a topic header, so the topic is part of the route
# that each webhook is registered with, e.g. /webhooks/recharge/order/created.
@app.route("/webhooks/recharge/{resource}/{action}", methods=["POST"], content_types=_JSON)
def recharge_webhook(resource: str, action: str) -> dict:
    request = app.current_request
    try:
        result = ingest_recharge(
            db.get_session_factory(), f"{resource}/{action}", dict(request.headers), request.raw_body
        )
    except InvalidWebhook as exc:
        raise BadRequestError(str(exc)) from exc
    return asdict(result)

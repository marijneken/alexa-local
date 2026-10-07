"""Alexa Smart Home skill -> Home Assistant (Alexa Local) relay.

Environment variables:
  BASE_URL                 https://your-home-assistant (required)
  NOT_VERIFY_SSL           "True" to skip TLS verification (not recommended)
  DEBUG                    "True" for verbose logging
  LONG_LIVED_ACCESS_TOKEN  only for testing from the Lambda console
"""

import json
import logging
import os

import urllib3

_debug = os.environ.get("DEBUG", "").lower() == "true"
_logger = logging.getLogger("AlexaLocal")
_logger.setLevel(logging.DEBUG if _debug else logging.INFO)

BASE_URL = os.environ["BASE_URL"].rstrip("/")
ENDPOINT = f"{BASE_URL}/api/alexa_local/smart_home"
REGION = os.environ.get("AWS_REGION", "")
VERIFY = os.environ.get("NOT_VERIFY_SSL", "").lower() != "true"

http = urllib3.PoolManager(
    cert_reqs="CERT_REQUIRED" if VERIFY else "CERT_NONE",
    timeout=urllib3.Timeout(connect=2.0, read=8.0),
    retries=urllib3.Retry(total=1, connect=1, read=0, backoff_factor=0.2),
)


def _error(event, error_type, message):
    header = event.get("directive", {}).get("header", {})
    return {
        "event": {
            "header": {
                "namespace": "Alexa",
                "name": "ErrorResponse",
                "messageId": header.get("messageId", "error"),
                "correlationToken": header.get("correlationToken"),
                "payloadVersion": "3",
            },
            "payload": {"type": error_type, "message": message},
        }
    }


def lambda_handler(event, context):
    _logger.debug("Event: %s", json.dumps(event))
    directive = event.get("directive")
    if not directive or directive.get("header", {}).get("payloadVersion") != "3":
        return _error(event, "INVALID_DIRECTIVE", "Only Smart Home API v3 is supported")

    scope = directive.get("endpoint", {}).get("scope")
    if scope is None:
        payload = directive.get("payload", {})
        scope = payload.get("grantee") or payload.get("scope")
    token = (scope or {}).get("token") or os.environ.get("LONG_LIVED_ACCESS_TOKEN")
    if not token:
        return _error(event, "INVALID_AUTHORIZATION_CREDENTIAL", "No token")

    try:
        response = http.request(
            "POST",
            ENDPOINT,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "X-Alexa-Local-Region": REGION,
            },
            body=json.dumps(event).encode("utf-8"),
        )
    except Exception as err:  # noqa: BLE001
        _logger.error("Home Assistant unreachable: %s", err)
        return _error(event, "ENDPOINT_UNREACHABLE", "Home Assistant is unreachable")

    if response.status in (401, 403):
        return _error(event, "INVALID_AUTHORIZATION_CREDENTIAL", "Home Assistant rejected the token")
    if response.status >= 400:
        _logger.error("Home Assistant HTTP %s: %s", response.status, response.data[:500])
        return _error(event, "INTERNAL_ERROR", f"Home Assistant HTTP {response.status}")

    if not response.data:
        return None
    result = json.loads(response.data.decode("utf-8"))
    _logger.debug("Response: %s", json.dumps(result))
    return result

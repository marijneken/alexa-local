"""HTTP endpoint and directive handling for Alexa Local."""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from homeassistant.components.alexa.errors import AlexaError
from homeassistant.components.alexa.smart_home import async_handle_message
from homeassistant.components.alexa.state_report import AlexaDirective
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import Context, HomeAssistant

from .config import AlexaLocalConfig
from .const import DOMAIN, HTTP_ENDPOINT, REGION_HEADER

_LOGGER = logging.getLogger(__name__)


class AlexaLocalView(HomeAssistantView):
    """Receives Smart Home directives forwarded by the Lambda."""

    url = HTTP_ENDPOINT
    name = "api:alexa_local:smart_home"

    async def post(self, request: web.Request) -> web.Response | bytes:
        """Handle a directive."""
        hass: HomeAssistant = request.app[KEY_HASS]
        message: dict[str, Any] = await request.json()
        config: AlexaLocalConfig | None = hass.data.get(DOMAIN)
        if config is None:
            response = await async_handle_message(
                hass, None, message, enabled=False  # type: ignore[arg-type]
            )
            return self.json(response)

        config.note_aws_region(request.headers.get(REGION_HEADER))
        user = request["hass_user"]
        context = Context(user_id=user.id)
        _LOGGER.debug("Directive: %s", _summary(message))
        response = await async_handle_directive(hass, config, message, context)
        _LOGGER.debug("Response: %s", _summary(response))
        return b"" if response is None else self.json(response)


def _summary(message: dict[str, Any] | None) -> str:
    if not message:
        return "-"
    root = message.get("directive") or message.get("event") or {}
    header = root.get("header", {})
    endpoint = (root.get("endpoint") or {}).get("endpointId", "-")
    return f"{header.get('namespace')}/{header.get('name')} {header.get('instance', '')} {endpoint}"


async def async_handle_directive(
    hass: HomeAssistant,
    config: AlexaLocalConfig,
    message: dict[str, Any],
    context: Context,
) -> dict[str, Any]:
    """Handle a directive; mapped controls and AcceptGrant are ours, rest is core."""
    directive = AlexaDirective(message)
    header = message["directive"]["header"]
    instance = header.get("instance")

    if directive.namespace == "Alexa.Authorization" and directive.name == "AcceptGrant":
        return await _async_accept_grant(config, directive)

    if directive.namespace == "Alexa.RangeController" and instance in config.instances:
        return await _async_mapped_control(hass, config, directive, context)

    response = await async_handle_message(hass, config, message, context=context)

    if directive.namespace == "Alexa.Discovery" and directive.name == "Discover":
        endpoints = response.get("event", {}).get("payload", {}).get("endpoints", [])
        config.record_discovery(endpoints)
        _LOGGER.info("Alexa discovered %d endpoints", len(endpoints))
    return response


async def _async_accept_grant(
    config: AlexaLocalConfig, directive: AlexaDirective
) -> dict[str, Any]:
    """Store the LWA grant so we can send events (state reports)."""
    try:
        await config.set_authorized(True)
        if config.supports_auth:
            token = await config.async_accept_grant(directive.payload["grant"]["code"])
            if token is None:
                return directive.error(
                    namespace="Alexa.Authorization",
                    error_type="ACCEPT_GRANT_FAILED",
                    error_message="Could not exchange grant with Login with Amazon",
                ).serialize()
            await config.async_enable_proactive_mode()
            config.async_schedule_sync()
    except Exception:
        _LOGGER.exception("AcceptGrant failed")
        return directive.error(
            namespace="Alexa.Authorization",
            error_type="ACCEPT_GRANT_FAILED",
            error_message="AcceptGrant failed",
        ).serialize()
    return directive.response(
        name="AcceptGrant.Response", namespace="Alexa.Authorization", payload={}
    ).serialize()


async def _async_mapped_control(
    hass: HomeAssistant,
    config: AlexaLocalConfig,
    directive: AlexaDirective,
    context: Context,
) -> dict[str, Any]:
    control = config.instances[message_instance(directive)]
    try:
        await config.set_authorized(True)
        directive.load_entity(hass, config)
        source_state = hass.states.get(control.source)
        if directive.name == "SetRangeValue":
            source_value, alexa_value = control.resolve_set(
                float(directive.payload["rangeValue"])
            )
        elif directive.name == "AdjustRangeValue":
            source_value, alexa_value = control.resolve_adjust(
                source_state,
                float(directive.payload.get("rangeValueDelta", 0)),
                bool(directive.payload.get("rangeValueDeltaDefault", False)),
            )
        else:
            return directive.error(
                error_type="INVALID_DIRECTIVE",
                error_message=f"Unsupported {directive.name}",
            ).serialize()
        await control.async_apply(hass, source_value, context)
        response = directive.response()
        response.add_context_property(
            {
                "namespace": "Alexa.RangeController",
                "instance": control.instance,
                "name": "rangeValue",
                "value": alexa_value,
            }
        )
        response.merge_context_properties(directive.endpoint)
    except AlexaError as err:
        return directive.error(
            error_type=str(err.error_type),
            error_message=err.error_message,
            payload=err.payload,
        ).serialize()
    except Exception:
        _LOGGER.exception("Error handling mapped control %s", control.names)
        return directive.error(error_message="Unknown error").serialize()
    return response.serialize()


def message_instance(directive: AlexaDirective) -> str:
    """Return the header instance of a directive."""
    return directive._directive["header"]["instance"]  # noqa: SLF001

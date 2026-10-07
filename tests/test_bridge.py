"""Tests for Alexa Local."""

from typing import Any
from uuid import uuid4

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er, label_registry as lr
from homeassistant.setup import async_setup_component

from custom_components.alexa_local.const import DOMAIN
from custom_components.alexa_local.controls import instance_for
from custom_components.alexa_local.smart_home import async_handle_directive


def directive(namespace, name, endpoint=None, payload=None, instance=None):
    header = {
        "namespace": namespace,
        "name": name,
        "messageId": str(uuid4()),
        "correlationToken": "tok",
        "payloadVersion": "3",
    }
    if instance:
        header["instance"] = instance
    d: dict[str, Any] = {"header": header, "payload": payload or {}}
    if endpoint:
        d["endpoint"] = {
            "endpointId": endpoint.replace(".", "#"),
            "scope": {"type": "BearerToken", "token": "x"},
        }
    else:
        d["payload"].setdefault("scope", {"type": "BearerToken", "token": "x"})
    return {"directive": d}


SWING_SUB = "01SWINGSUBENTRY0000000000A"
COVER_SUB = "01COVERSUBENTRY0000000000B"


@pytest.fixture
async def setup(hass: HomeAssistant):
    await async_setup_component(hass, "http", {})
    labels = lr.async_get(hass)
    labels.async_create("Alexa")
    ent_reg = er.async_get(hass)

    def reg(domain, obj, labelled=True):
        e = ent_reg.async_get_or_create(domain, "test", obj, suggested_object_id=obj)
        if labelled:
            ent_reg.async_update_entity(e.entity_id, labels={"alexa"})
        return e.entity_id

    light = reg("light", "kitchen")
    reg("light", "hidden", labelled=False)
    curtain = reg("cover", "curtain")
    fan = reg("fan", "fan")
    swing = reg("select", "fan_swing", labelled=False)

    hass.states.async_set(light, "on", {"supported_color_modes": ["brightness"], "brightness": 255, "friendly_name": "Kitchen"})
    hass.states.async_set("light.hidden", "on", {"supported_color_modes": ["onoff"]})
    hass.states.async_set(curtain, "open", {"current_position": 30, "supported_features": 15, "device_class": "curtain", "friendly_name": "Curtain"})
    hass.states.async_set(fan, "on", {"supported_features": 0, "friendly_name": "Fan"})
    hass.states.async_set(swing, "medium", {"options": ["off", "narrow", "medium", "wide"], "friendly_name": "Fan swing"})

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"label": "alexa", "locale": "en-US", "region": "auto"},
        subentries_data=[
            ConfigSubentryData(
                subentry_id=SWING_SUB,
                subentry_type="mapped_control",
                title="Fan: Swing",
                unique_id=None,
                data={
                    "target": fan,
                    "names": ["Swing"],
                    "source": swing,
                    "kind": "options",
                    "option_map": {"off": 0, "narrow": 33, "medium": 67, "wide": 100},
                    "presets": True,
                    "percent": True,
                },
            ),
            ConfigSubentryData(
                subentry_id=COVER_SUB,
                subentry_type="inverted_cover",
                title="Curtain",
                unique_id=None,
                data={"source": curtain, "invert_position": True, "invert_tilt": True},
            ),
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_discovery(hass: HomeAssistant, setup) -> None:
    cfg = setup.runtime_data
    resp = await async_handle_directive(hass, cfg, directive("Alexa.Discovery", "Discover"), Context())
    eps = {e["endpointId"]: e for e in resp["event"]["payload"]["endpoints"]}
    assert set(eps) == {"light#kitchen", "cover#curtain_alexa", "fan#fan"}
    assert eps["cover#curtain_alexa"]["friendlyName"] == "Curtain"
    caps = eps["fan#fan"]["capabilities"]
    swing = [c for c in caps if c.get("instance") == instance_for(SWING_SUB)]
    assert len(swing) == 1
    cfgd = swing[0]["configuration"]
    assert cfgd["supportedRange"] == {"minimumValue": 0, "maximumValue": 100, "precision": 1}
    assert [p["presetResources"]["friendlyNames"][0]["value"]["text"] for p in cfgd["presets"]] == ["narrow", "medium", "wide"]
    assert swing[0]["capabilityResources"]["friendlyNames"][0]["value"]["text"] == "Swing"


async def test_mapped_control(hass: HomeAssistant, setup) -> None:
    cfg = setup.runtime_data
    calls = async_mock_service(hass, "select", "select_option")
    inst = instance_for(SWING_SUB)
    resp = await async_handle_directive(
        hass, cfg,
        directive("Alexa.RangeController", "SetRangeValue", "fan.fan", {"rangeValue": 40}, inst),
        Context(),
    )
    assert resp["event"]["header"]["name"] == "Response", resp
    await hass.async_block_till_done()
    assert calls[-1].data == {"entity_id": "select.fan_swing", "option": "narrow"}
    props = {(p["namespace"], p.get("instance")): p["value"] for p in resp["context"]["properties"]}
    assert props[("Alexa.RangeController", inst)] == 33

    # ReportState includes the mapped value (medium -> 67)
    resp = await async_handle_directive(hass, cfg, directive("Alexa", "ReportState", "fan.fan"), Context())
    props = {(p["namespace"], p.get("instance")): p["value"] for p in resp["context"]["properties"]}
    assert props[("Alexa.RangeController", inst)] == 67

    # Adjust up by default step -> wide
    resp = await async_handle_directive(
        hass, cfg,
        directive("Alexa.RangeController", "AdjustRangeValue", "fan.fan", {"rangeValueDelta": 10, "rangeValueDeltaDefault": True}, inst),
        Context(),
    )
    await hass.async_block_till_done()
    assert calls[-1].data["option"] == "wide"


async def test_inverted_cover(hass: HomeAssistant, setup) -> None:
    cfg = setup.runtime_data
    state = hass.states.get("cover.curtain_alexa")
    assert state.attributes["current_position"] == 70
    assert state.state == "open"
    close = async_mock_service(hass, "cover", "close_cover")

    # The real cover must not be reachable directly anymore
    resp = await async_handle_directive(hass, cfg, directive("Alexa.PowerController", "TurnOn", "cover.curtain"), Context())
    assert resp["event"]["header"]["name"] == "ErrorResponse"

    resp = await async_handle_directive(hass, cfg, directive("Alexa.PowerController", "TurnOn", "cover.curtain_alexa"), Context())
    assert resp["event"]["header"]["name"] == "Response", resp
    await hass.async_block_till_done()
    assert close and close[-1].data["entity_id"] == "cover.curtain"

    from unittest.mock import patch
    from custom_components.alexa_local.cover import InvertedCover

    with patch.object(InvertedCover, "_call") as mock_call:
        resp = await async_handle_directive(
            hass, cfg,
            directive("Alexa.RangeController", "SetRangeValue", "cover.curtain_alexa", {"rangeValue": 25}, "cover.position"),
            Context(),
        )
        await hass.async_block_till_done()
    mock_call.assert_called_with("set_cover_position", position=75)

    hass.states.async_set("cover.curtain", "closed", {"current_position": 0, "supported_features": 15, "device_class": "curtain"})
    await hass.async_block_till_done()
    state = hass.states.get("cover.curtain_alexa")
    assert state.attributes["current_position"] == 100 and state.state == "open"


async def test_label_change_and_sensor(hass: HomeAssistant, setup) -> None:
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed
    from datetime import timedelta

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=10))
    await hass.async_block_till_done()
    sensor = hass.states.get("sensor.alexa_local_exposed_entities")
    assert sensor.state == "3"
    er.async_get(hass).async_update_entity("light.hidden", labels={"alexa"})
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=20))
    await hass.async_block_till_done()
    assert hass.states.get("sensor.alexa_local_exposed_entities").state == "4"


async def test_unload(hass: HomeAssistant, setup) -> None:
    from homeassistant.components.alexa.entities import ENTITY_ADAPTERS
    assert getattr(ENTITY_ADAPTERS["light"], "__wrapped__", None) is not None
    assert await hass.config_entries.async_unload(setup.entry_id)
    assert getattr(ENTITY_ADAPTERS["light"], "__wrapped__", None) is None


async def test_state_reporting(hass: HomeAssistant, aioclient_mock) -> None:
    from datetime import timedelta
    from unittest.mock import patch

    from pytest_homeassistant_custom_component.common import async_fire_time_changed
    from homeassistant.util import dt as dt_util

    await async_setup_component(hass, "http", {})
    lr.async_get(hass).async_create("Alexa")
    ent_reg = er.async_get(hass)
    for obj in ("a", "b"):
        e = ent_reg.async_get_or_create("light", "t", obj, suggested_object_id=obj)
        hass.states.async_set(e.entity_id, "off", {"supported_color_modes": ["onoff"]})
    ent_reg.async_update_entity("light.a", labels={"alexa"})
    hass.states.async_set("select.s", "x", {"options": ["x", "y"]})

    aioclient_mock.post("https://api.amazon.com/auth/o2/token", json={"access_token": "AT", "refresh_token": "RT", "expires_in": 3600})
    aioclient_mock.post("https://api.eu.amazonalexa.com/v3/events", status=202)

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"label": "alexa", "locale": "en-US", "region": "auto", "client_id": "cid", "client_secret": "sec"},
        subentries_data=[ConfigSubentryData(
            subentry_id=SWING_SUB, subentry_type="mapped_control", title="x", unique_id=None,
            data={"target": "light.a", "names": ["Mood"], "source": "select.s", "kind": "options", "option_map": {"x": 0, "y": 100}},
        )],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    cfg = entry.runtime_data
    cfg.note_aws_region("eu-west-1")

    resp = await async_handle_directive(hass, cfg, {"directive": {"header": {"namespace": "Alexa.Authorization", "name": "AcceptGrant", "messageId": "1", "payloadVersion": "3"}, "payload": {"grant": {"type": "OAuth2.AuthorizationCode", "code": "C"}, "grantee": {"type": "BearerToken", "token": "x"}}}}, Context())
    assert resp["event"]["header"]["name"] == "AcceptGrant.Response", resp
    assert cfg.is_reporting_states

    await async_handle_directive(hass, cfg, directive("Alexa.Discovery", "Discover"), Context())

    # mapped control source changes -> ChangeReport for light.a
    with patch.object(hass, "is_running", True):
        n = aioclient_mock.call_count
        hass.states.async_set("select.s", "y", {"options": ["x", "y"]})
        await hass.async_block_till_done()
    events = [c for c in aioclient_mock.mock_calls[n:] if "amazonalexa" in str(c[1])]
    assert events, aioclient_mock.mock_calls
    body = events[-1][2]
    assert body["event"]["header"]["name"] == "ChangeReport"
    assert body["event"]["endpoint"]["endpointId"] == "light#a"

    # label added -> AddOrUpdateReport
    n = aioclient_mock.call_count
    ent_reg.async_update_entity("light.b", labels={"alexa"})
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=10))
    await hass.async_block_till_done()
    names = [c[2]["event"]["header"]["name"] for c in aioclient_mock.mock_calls[n:] if "amazonalexa" in str(c[1])]
    assert names == ["AddOrUpdateReport"], names

    # label removed -> DeleteReport
    n = aioclient_mock.call_count
    ent_reg.async_update_entity("light.b", labels=set())
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=20))
    await hass.async_block_till_done()
    names = [c[2]["event"]["header"]["name"] for c in aioclient_mock.mock_calls[n:] if "amazonalexa" in str(c[1])]
    assert names == ["DeleteReport"], names

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import label_registry as lr
from homeassistant.setup import async_setup_component

from custom_components.alexa_local.const import DOMAIN


async def test_flow_and_subentries(hass: HomeAssistant) -> None:
    await async_setup_component(hass, "http", {})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert lr.async_get(hass).async_get_label("alexa") is not None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"label": "alexa", "locale": "en-US", "region": "auto"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    entry = result["result"]

    hass.states.async_set("select.swing", "a", {"options": ["a", "b", "c"]})
    hass.states.async_set("fan.fan", "on", {})
    result = await hass.config_entries.subentries.async_init((entry.entry_id, "mapped_control"), context={"source": "user"})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"target": "fan.fan", "names": "Swing, Sweep", "source": "select.swing"}
    )
    assert result["step_id"] == "map_options"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"a": 0, "b": 50, "c": 100, "presets": True, "percent": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    sub = next(iter(entry.subentries.values()))
    assert sub.data["names"] == ["Swing", "Sweep"]
    assert sub.data["option_map"] == {"a": 0, "b": 50, "c": 100}

    hass.states.async_set("number.level", "5", {"min": 0, "max": 10, "step": 1})
    result = await hass.config_entries.subentries.async_init((entry.entry_id, "mapped_control"), context={"source": "user"})
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"target": "fan.fan", "names": "Level", "source": "number.level"}
    )
    assert result["step_id"] == "map_linear"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"alexa_min": 0, "alexa_max": 100, "source_min": 0, "source_max": 10, "precision": 1, "percent": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    hass.states.async_set("cover.c", "open", {"current_position": 10, "supported_features": 15})
    result = await hass.config_entries.subentries.async_init((entry.entry_id, "inverted_cover"), context={"source": "user"})
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"source": "cover.c", "invert_position": True, "invert_tilt": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert hass.states.get("cover.c_alexa").attributes["current_position"] == 90

"""Diagnostic sensor listing what is exposed to Alexa."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, NAME, SIGNAL_EXPOSED_UPDATED


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensor."""
    async_add_entities([ExposedSensor(entry)])


class ExposedSensor(SensorEntity):
    """Number of entities exposed to Alexa, with the list as attribute."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "exposed"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:account-voice"

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize."""
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_exposed"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=NAME,
            manufacturer="Home Assistant",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        """Listen for updates."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_EXPOSED_UPDATED, self._async_updated
            )
        )
        self._async_updated(write=False)

    @callback
    def _async_updated(self, write: bool = True) -> None:
        cfg = self._entry.runtime_data
        self._attr_native_value = len(cfg.exposed)
        self._attr_extra_state_attributes = {
            "entities": cfg.exposed,
            "state_reporting": cfg.is_reporting_states,
            "event_gateway": cfg.endpoint,
        }
        if write:
            self.async_write_ha_state()

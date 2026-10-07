"""Alexa Local: a self-hosted Alexa Smart Home bridge for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import label_registry as lr

from . import adapters
from .config import AlexaLocalConfig
from .const import CONF_LABEL, DEFAULT_LABEL, DOMAIN
from .smart_home import AlexaLocalView

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.COVER, Platform.SENSOR]
VIEW_REGISTERED = f"{DOMAIN}_view"

type AlexaLocalConfigEntry = ConfigEntry[AlexaLocalConfig]


async def async_setup_entry(hass: HomeAssistant, entry: AlexaLocalConfigEntry) -> bool:
    """Set up Alexa Local from a config entry."""
    label_id = {**entry.data, **entry.options}.get(CONF_LABEL) or DEFAULT_LABEL
    labels = lr.async_get(hass)
    if labels.async_get_label(label_id) is None and label_id == DEFAULT_LABEL:
        labels.async_create("Alexa", icon="mdi:account-voice")

    config = AlexaLocalConfig(hass, entry)
    await config.async_initialize()
    entry.runtime_data = config

    adapters.install()
    entry.async_on_unload(adapters.uninstall)

    if not hass.data.get(VIEW_REGISTERED):
        hass.http.register_view(AlexaLocalView())
        hass.data[VIEW_REGISTERED] = True
    hass.data[DOMAIN] = config

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    config.async_start()
    if config.should_report_state:
        try:
            await config.async_enable_proactive_mode()
        except Exception:
            _LOGGER.exception("Could not enable Alexa state reporting")

    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: AlexaLocalConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: AlexaLocalConfigEntry) -> bool:
    """Unload."""
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    config = entry.runtime_data
    config.async_stop()
    await config.async_disable_proactive_mode()
    config.async_deinitialize()
    if hass.data.get(DOMAIN) is config:
        hass.data.pop(DOMAIN)
    return ok

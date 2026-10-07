"""Inverted cover proxies: what Alexa sees instead of the real cover."""

from __future__ import annotations

from typing import Any

from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_CURRENT_TILT_POSITION,
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    DOMAIN as COVER_DOMAIN,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    STATE_CLOSED,
    STATE_CLOSING,
    STATE_OPEN,
    STATE_OPENING,
    STATE_UNAVAILABLE,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_INVERT_POSITION,
    CONF_INVERT_TILT,
    CONF_NAME,
    CONF_SOURCE,
    DOMAIN,
    SUBENTRY_INVERTED_COVER,
)

_SWAP_FEATURES = (
    (CoverEntityFeature.OPEN, CoverEntityFeature.CLOSE),
    (CoverEntityFeature.OPEN_TILT, CoverEntityFeature.CLOSE_TILT),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up inverted cover proxies from subentries."""
    for sub in entry.subentries.values():
        if sub.subentry_type != SUBENTRY_INVERTED_COVER:
            continue
        async_add_entities(
            [InvertedCover(entry, sub)], config_subentry_id=sub.subentry_id
        )


def _inv(value: Any) -> int | None:
    try:
        return 100 - int(float(value))
    except TypeError, ValueError:
        return None


class InvertedCover(CoverEntity):
    """A cover that mirrors another cover with reversed direction."""

    _attr_should_poll = False
    _attr_has_entity_name = False
    _attr_entity_registry_visible_default = False

    def __init__(self, entry: ConfigEntry, sub: ConfigSubentry) -> None:
        """Initialize."""
        self._entry = entry
        self._source: str = sub.data[CONF_SOURCE]
        self._inv_pos: bool = sub.data.get(CONF_INVERT_POSITION, True)
        self._inv_tilt: bool = sub.data.get(CONF_INVERT_TILT, True)
        self._custom_name: str | None = sub.data.get(CONF_NAME) or None
        self._attr_unique_id = sub.subentry_id
        self._attr_name = self._custom_name or sub.title
        object_id = self._source.split(".", 1)[1]
        self.entity_id = f"{COVER_DOMAIN}.{object_id}_alexa"
        self._attr_extra_state_attributes = {"source_entity_id": self._source}

    async def async_added_to_hass(self) -> None:
        """Track the source cover."""
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._source], self._async_source_changed
            )
        )
        self._update_from_source()
        self._entry.runtime_data.register_proxy(self.entity_id, self._source)

    @callback
    def _async_source_changed(self, _event: Event[EventStateChangedData]) -> None:
        self._update_from_source()
        self.async_write_ha_state()

    @callback
    def _update_from_source(self) -> None:
        state = self.hass.states.get(self._source)
        if state is None or state.state == STATE_UNAVAILABLE:
            self._attr_available = False
            return
        self._attr_available = True
        attrs = state.attributes
        if not self._custom_name:
            self._attr_name = state.name
        self._attr_device_class = attrs.get(ATTR_DEVICE_CLASS)

        features = CoverEntityFeature(attrs.get(ATTR_SUPPORTED_FEATURES, 0))
        for (open_f, close_f), inverted in zip(
            _SWAP_FEATURES, (self._inv_pos, self._inv_tilt), strict=True
        ):
            if inverted and bool(features & open_f) != bool(features & close_f):
                features ^= open_f | close_f
        self._attr_supported_features = features

        pos = attrs.get(ATTR_CURRENT_POSITION)
        tilt = attrs.get(ATTR_CURRENT_TILT_POSITION)
        self._attr_current_cover_position = (
            _inv(pos) if self._inv_pos else (None if pos is None else int(pos))
        )
        self._attr_current_cover_tilt_position = (
            _inv(tilt) if self._inv_tilt else (None if tilt is None else int(tilt))
        )

        src = state.state
        if self._inv_pos:
            src = {
                STATE_OPEN: STATE_CLOSED,
                STATE_CLOSED: STATE_OPEN,
                STATE_OPENING: STATE_CLOSING,
                STATE_CLOSING: STATE_OPENING,
            }.get(src, src)
        self._attr_is_opening = src == STATE_OPENING
        self._attr_is_closing = src == STATE_CLOSING
        if self._attr_current_cover_position is not None:
            self._attr_is_closed = self._attr_current_cover_position == 0
        elif src in (STATE_OPEN, STATE_CLOSED):
            self._attr_is_closed = src == STATE_CLOSED
        else:
            self._attr_is_closed = None

    async def _call(self, service: str, **data: Any) -> None:
        await self.hass.services.async_call(
            COVER_DOMAIN,
            service,
            {ATTR_ENTITY_ID: self._source, **data},
            blocking=True,
            context=self._context,
        )

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open (the source closes when inverted)."""
        await self._call("close_cover" if self._inv_pos else "open_cover")

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close (the source opens when inverted)."""
        await self._call("open_cover" if self._inv_pos else "close_cover")

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Set position."""
        pos = int(kwargs[ATTR_POSITION])
        await self._call(
            "set_cover_position", position=100 - pos if self._inv_pos else pos
        )

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop."""
        await self._call("stop_cover")

    async def async_open_cover_tilt(self, **kwargs: Any) -> None:
        """Open tilt."""
        await self._call("close_cover_tilt" if self._inv_tilt else "open_cover_tilt")

    async def async_close_cover_tilt(self, **kwargs: Any) -> None:
        """Close tilt."""
        await self._call("open_cover_tilt" if self._inv_tilt else "close_cover_tilt")

    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        """Set tilt."""
        tilt = int(kwargs[ATTR_TILT_POSITION])
        await self._call(
            "set_cover_tilt_position",
            tilt_position=100 - tilt if self._inv_tilt else tilt,
        )

    async def async_stop_cover_tilt(self, **kwargs: Any) -> None:
        """Stop tilt."""
        await self._call("stop_cover_tilt")

"""Alexa configuration backed by a config entry."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from homeassistant.components.alexa.auth import Auth
from homeassistant.components.alexa.config import AbstractConfig, AlexaConfigStore
from homeassistant.components.alexa.entities import AlexaEntity, async_get_entities
from homeassistant.components.alexa.state_report import (
    async_send_add_or_update_message,
    async_send_changereport_message,
    async_send_delete_message,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    label_registry as lr,
)
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.storage import Store

from .const import (
    AWS_REGION_TO_GATEWAY,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_INVERT_POSITION,
    CONF_INVERT_TILT,
    CONF_LABEL,
    CONF_LOCALE,
    CONF_HIDE_COVER_POWER,
    CONF_REGION,
    CONF_SOURCE,
    DEFAULT_LABEL,
    DEFAULT_LOCALE,
    DOMAIN,
    EVENT_GATEWAYS,
    REGION_AUTO,
    SIGNAL_EXPOSED_UPDATED,
    SUBENTRY_INVERTED_COVER,
    SUBENTRY_MAPPED_CONTROL,
)
from .controls import MappedControl

_LOGGER = logging.getLogger(__name__)

SYNC_DELAY = 5


class _LocalConfigStore(AlexaConfigStore):
    _STORAGE_KEY = f"{DOMAIN}.authorized"


class _LocalAuth(Auth):
    def __init__(self, hass: HomeAssistant, client_id: str, client_secret: str) -> None:
        super().__init__(hass, client_id, client_secret)
        self._store = Store(hass, 1, f"{DOMAIN}.auth")


def discovery_signature(endpoint: dict[str, Any]) -> str:
    """Stable hash of a discovery endpoint (ignores HA version info)."""
    data = {k: v for k, v in endpoint.items() if k != "additionalAttributes"}
    return hashlib.sha1(
        json.dumps(data, sort_keys=True, default=str).encode()
    ).hexdigest()


class AlexaLocalConfig(AbstractConfig):
    """Alexa config for one Alexa Local config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(hass)
        self.entry = entry
        data = {**entry.data, **entry.options}
        self.label_id: str = data.get(CONF_LABEL) or DEFAULT_LABEL
        self._locale: str = data.get(CONF_LOCALE) or DEFAULT_LOCALE
        self._region_setting: str = data.get(CONF_REGION) or REGION_AUTO
        self.alexa_local_hide_cover_power: bool = data.get(CONF_HIDE_COVER_POWER, True)
        cid = (data.get(CONF_CLIENT_ID) or "").strip()
        secret = (data.get(CONF_CLIENT_SECRET) or "").strip()
        self._auth: _LocalAuth | None = (
            _LocalAuth(hass, cid, secret) if cid and secret else None
        )

        # Inverted covers: source entity -> subentry id; proxy entity -> source.
        self.inverted_sources: dict[str, str] = {}
        self.inverted_options: dict[str, dict[str, bool]] = {}
        self.proxy_to_source: dict[str, str] = {}
        # Mapped controls: target entity -> controls; instance -> control
        self.controls: dict[str, list[MappedControl]] = {}
        self.instances: dict[str, MappedControl] = {}

        self._sync_store: Store = Store(hass, 1, f"{DOMAIN}.sync")
        self._sync_data: dict[str, Any] = {"region": None, "signatures": None}
        self._sync_unsub: CALLBACK_TYPE | None = None
        self._unsubs: list[CALLBACK_TYPE] = []
        self.exposed: list[str] = []

    # ------------------------------------------------------------------ setup
    async def async_initialize(self) -> None:
        """Load stores and subentries."""
        self._store = _LocalConfigStore(self.hass)
        await self._store.async_load()
        if stored := await self._sync_store.async_load():
            self._sync_data.update(stored)
        self._load_subentries()

    def _load_subentries(self) -> None:
        for sub in self.entry.subentries.values():
            if sub.subentry_type == SUBENTRY_INVERTED_COVER:
                source = sub.data[CONF_SOURCE]
                self.inverted_sources[source] = sub.subentry_id
                self.inverted_options[sub.subentry_id] = {
                    CONF_INVERT_POSITION: sub.data.get(CONF_INVERT_POSITION, True),
                    CONF_INVERT_TILT: sub.data.get(CONF_INVERT_TILT, True),
                }
            elif sub.subentry_type == SUBENTRY_MAPPED_CONTROL:
                try:
                    control = MappedControl.from_subentry(sub.subentry_id, sub.data)
                except (KeyError, ValueError, TypeError):
                    _LOGGER.exception("Invalid mapped control %s", sub.title)
                    continue
                self.instances[control.instance] = control

    @callback
    def register_proxy(self, proxy_entity_id: str, source: str) -> None:
        """Register an inverted-cover proxy entity."""
        self.proxy_to_source = {
            k: v for k, v in self.proxy_to_source.items() if v != source
        }
        self.proxy_to_source[proxy_entity_id] = source
        self._rebuild_controls()
        self.async_schedule_sync()

    def _rebuild_controls(self) -> None:
        """Map control targets; a target that was replaced by a proxy follows it."""
        source_to_proxy = {v: k for k, v in self.proxy_to_source.items()}
        controls: dict[str, list[MappedControl]] = {}
        for control in self.instances.values():
            target = source_to_proxy.get(control.target, control.target)
            controls.setdefault(target, []).append(control)
        self.controls = controls

    @callback
    def async_start(self) -> None:
        """Start listeners for exposure changes and mapped control sources."""
        self._rebuild_controls()

        @callback
        def _registry_changed(_event: Event) -> None:
            self.async_schedule_sync()

        for event_type in (
            er.EVENT_ENTITY_REGISTRY_UPDATED,
            dr.EVENT_DEVICE_REGISTRY_UPDATED,
            lr.EVENT_LABEL_REGISTRY_UPDATED,
        ):
            self._unsubs.append(self.hass.bus.async_listen(event_type, _registry_changed))

        sources = {c.source for c in self.instances.values()}
        if sources:
            self._unsubs.append(
                async_track_state_change_event(
                    self.hass, list(sources), self._async_source_changed
                )
            )

        if self.hass.is_running:
            self.async_schedule_sync()
        else:
            self._unsubs.append(
                self.hass.bus.async_listen_once(
                    EVENT_HOMEASSISTANT_STARTED,
                    lambda _e: self.async_schedule_sync(),
                )
            )

    @callback
    def async_stop(self) -> None:
        """Stop listeners."""
        while self._unsubs:
            self._unsubs.pop()()
        if self._sync_unsub:
            self._sync_unsub()
            self._sync_unsub = None

    # --------------------------------------------------------- AbstractConfig
    @property
    def supports_auth(self) -> bool:
        """Return if state reporting credentials are configured."""
        return self._auth is not None

    @property
    def should_report_state(self) -> bool:
        """Return if states should be proactively reported."""
        return self._auth is not None and self.authorized

    @property
    def endpoint(self) -> str:
        """Alexa event gateway."""
        region = self._region_setting
        if region not in EVENT_GATEWAYS:
            region = self._sync_data.get("region") or "NA"
        return EVENT_GATEWAYS[region]

    @property
    def locale(self) -> str:
        """Locale."""
        return self._locale

    @callback
    def user_identifier(self) -> str:
        """Identifier for this config."""
        return self.entry.entry_id

    @callback
    def alexa_local_controls_for(self, entity_id: str) -> list[MappedControl]:
        """Mapped controls attached to an entity (used by the adapter hook)."""
        return self.controls.get(entity_id, [])

    def _labeled(self, entity_id: str) -> bool:
        ent = er.async_get(self.hass).async_get(entity_id)
        if ent is None or ent.disabled_by is not None:
            return False
        if self.label_id in ent.labels:
            return True
        if (
            ent.device_id
            and ent.entity_category is None
            and ent.hidden_by is None
            and (device := dr.async_get(self.hass).async_get(ent.device_id))
            and self.label_id in device.labels
        ):
            return True
        return False

    @callback
    def should_expose(self, entity_id: str) -> bool:
        """Expose entities carrying the configured label."""
        if (source := self.proxy_to_source.get(entity_id)) is not None:
            return self._labeled(source) or self._labeled(entity_id)
        if entity_id in self.inverted_sources and entity_id in set(
            self.proxy_to_source.values()
        ):
            # Replaced by its inverted proxy.
            return False
        return self._labeled(entity_id)

    @callback
    def async_invalidate_access_token(self) -> None:
        """Invalidate access token."""
        if self._auth is not None:
            self._auth.async_invalidate_access_token()

    async def async_get_access_token(self) -> str | None:
        """Get an access token."""
        if self._auth is None:
            return None
        return await self._auth.async_get_access_token()

    async def async_accept_grant(self, code: str) -> str | None:
        """Accept a grant."""
        if self._auth is None:
            return None
        return await self._auth.async_do_auth(code)

    # ---------------------------------------------------------------- region
    @callback
    def note_aws_region(self, aws_region: str | None) -> None:
        """Remember which Alexa region talks to us (sent by the Lambda)."""
        region = AWS_REGION_TO_GATEWAY.get((aws_region or "").strip())
        if region and region != self._sync_data.get("region"):
            _LOGGER.info("Alexa region detected: %s", region)
            self._sync_data["region"] = region
            self._sync_store.async_delay_save(lambda: self._sync_data, 1)

    # ------------------------------------------------------------- reporting
    async def _async_source_changed(self, event: Event[EventStateChangedData]) -> None:
        """A mapped control's source changed: report the target's new state."""
        entity_id = event.data["entity_id"]
        old, new = event.data["old_state"], event.data["new_state"]
        if old is not None and new is not None and old.state == new.state:
            return
        if not (self.should_report_state and self.is_reporting_states):
            return
        for target, controls in self.controls.items():
            if not any(c.source == entity_id for c in controls):
                continue
            if not self.should_expose(target):
                continue
            if (state := self.hass.states.get(target)) is None:
                continue
            from homeassistant.components.alexa.entities import ENTITY_ADAPTERS

            if state.domain not in ENTITY_ADAPTERS:
                continue
            alexa_entity: AlexaEntity = ENTITY_ADAPTERS[state.domain](
                self.hass, self, state
            )
            properties = list(alexa_entity.serialize_properties())
            try:
                await async_send_changereport_message(
                    self.hass, self, alexa_entity, properties
                )
            except Exception:
                _LOGGER.exception("Failed to report %s", target)

    # ------------------------------------------------------------------ sync
    @callback
    def exposed_entities(self) -> list[AlexaEntity]:
        """All Alexa entities currently exposed."""
        return [
            e
            for e in async_get_entities(self.hass, self)
            if self.should_expose(e.entity_id)
        ]

    @callback
    def record_discovery(self, endpoints: list[dict[str, Any]]) -> None:
        """Remember what Alexa got from a Discover directive."""
        self._sync_data["signatures"] = {
            ep["endpointId"]: discovery_signature(ep) for ep in endpoints
        }
        self._sync_store.async_delay_save(lambda: self._sync_data, 1)

    @callback
    def async_schedule_sync(self) -> None:
        """Debounced sync of exposed entities with Alexa."""
        if self._sync_unsub:
            self._sync_unsub()

        @callback
        def _run(_now: Any) -> None:
            self._sync_unsub = None
            self.entry.async_create_background_task(
                self.hass, self.async_sync(), f"{DOMAIN}_sync"
            )

        self._sync_unsub = async_call_later(self.hass, SYNC_DELAY, _run)

    async def async_sync(self) -> None:
        """Push added/changed/removed endpoints to Alexa (if reporting works)."""
        entities = self.exposed_entities()
        current: dict[str, tuple[str, str]] = {}
        for ent in entities:
            try:
                current[ent.alexa_id()] = (
                    ent.entity_id,
                    discovery_signature(ent.serialize_discovery()),
                )
            except Exception:
                _LOGGER.exception("Unable to serialize %s", ent.entity_id)
        self.exposed = sorted(eid for eid, _ in current.values())
        async_dispatcher_send(self.hass, SIGNAL_EXPOSED_UPDATED)

        stored: dict[str, str] | None = self._sync_data.get("signatures")
        if stored is None or not self.should_report_state:
            # Never discovered yet / no state reporting: Alexa will pick
            # everything up on the next "discover devices".
            return

        changed = [eid for aid, (eid, sig) in current.items() if stored.get(aid) != sig]
        removed = [aid.replace("#", ".") for aid in stored if aid not in current]
        if not changed and not removed:
            return

        new_signatures = dict(stored)
        try:
            if changed:
                resp = await async_send_add_or_update_message(self.hass, self, changed)
                if resp.status in (200, 202):
                    for aid, (eid, sig) in current.items():
                        if eid in changed:
                            new_signatures[aid] = sig
                    _LOGGER.info("Sent AddOrUpdateReport for %s", changed)
                else:
                    _LOGGER.warning(
                        "AddOrUpdateReport failed (%s): %s",
                        resp.status,
                        await resp.text(),
                    )
            if removed:
                resp = await async_send_delete_message(self.hass, self, removed)
                if resp.status in (200, 202):
                    for eid in removed:
                        new_signatures.pop(self.generate_alexa_id(eid), None)
                    _LOGGER.info("Sent DeleteReport for %s", removed)
                else:
                    _LOGGER.warning(
                        "DeleteReport failed (%s): %s", resp.status, await resp.text()
                    )
        except Exception:
            _LOGGER.exception("Error syncing devices with Alexa")
            return

        self._sync_data["signatures"] = new_signatures
        self._sync_store.async_delay_save(lambda: self._sync_data, 1)

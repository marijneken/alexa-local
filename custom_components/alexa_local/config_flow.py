"""Config flow for Alexa Local."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.alexa.const import CONF_SUPPORTED_LOCALES
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers import label_registry as lr, selector

from .const import (
    CONF_ALEXA_MAX,
    CONF_ALEXA_MIN,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_INVERT_POSITION,
    CONF_INVERT_TILT,
    CONF_KIND,
    CONF_LABEL,
    CONF_LOCALE,
    CONF_NAME,
    CONF_NAMES,
    CONF_OPTION_MAP,
    CONF_PERCENT,
    CONF_PRECISION,
    CONF_PRESETS,
    CONF_HIDE_COVER_POWER,
    CONF_REGION,
    CONF_SOURCE,
    CONF_SOURCE_MAX,
    CONF_SOURCE_MIN,
    CONF_TARGET,
    DEFAULT_LABEL,
    DEFAULT_LOCALE,
    DOMAIN,
    KIND_LINEAR,
    KIND_OPTIONS,
    NAME,
    NUMBER_DOMAINS,
    OPTION_DOMAINS,
    REGION_AUTO,
    SUBENTRY_INVERTED_COVER,
    SUBENTRY_MAPPED_CONTROL,
)

REGIONS = [REGION_AUTO, "NA", "EU", "FE"]


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    """Strip stray whitespace from pasted credentials."""
    return {k: v.strip() if isinstance(v, str) else v for k, v in data.items()}


def _settings_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_LABEL, default=defaults.get(CONF_LABEL, DEFAULT_LABEL)
            ): selector.LabelSelector(),
            vol.Required(
                CONF_LOCALE, default=defaults.get(CONF_LOCALE, DEFAULT_LOCALE)
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(CONF_SUPPORTED_LOCALES),
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_CLIENT_ID,
                description={"suggested_value": defaults.get(CONF_CLIENT_ID)},
            ): selector.TextSelector(),
            vol.Optional(
                CONF_CLIENT_SECRET,
                description={"suggested_value": defaults.get(CONF_CLIENT_SECRET)},
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Required(
                CONF_REGION, default=defaults.get(CONF_REGION, REGION_AUTO)
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=REGIONS,
                    translation_key="region",
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_HIDE_COVER_POWER,
                default=defaults.get(CONF_HIDE_COVER_POWER, True),
            ): selector.BooleanSelector(),
        }
    )


class AlexaLocalConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Initial setup."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(title=NAME, data=_clean(user_input))

        labels = lr.async_get(self.hass)
        if labels.async_get_label(DEFAULT_LABEL) is None:
            labels.async_create("Alexa", icon="mdi:account-voice")
        return self.async_show_form(step_id="user", data_schema=_settings_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Options flow."""
        return AlexaLocalOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Subentry types."""
        return {
            SUBENTRY_INVERTED_COVER: InvertedCoverFlow,
            SUBENTRY_MAPPED_CONTROL: MappedControlFlow,
        }


class AlexaLocalOptionsFlow(OptionsFlow):
    """Edit settings."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Settings."""
        if user_input is not None:
            return self.async_create_entry(data=_clean(user_input))
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init", data_schema=_settings_schema(current)
        )


def _state_name(hass, entity_id: str) -> str:  # noqa: ANN001
    state = hass.states.get(entity_id)
    return state.name if state else entity_id


class InvertedCoverFlow(ConfigSubentryFlow):
    """Add/edit an inverted cover."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add."""
        return await self._async_form(user_input, None)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit."""
        return await self._async_form(user_input, self._get_reconfigure_subentry())

    async def _async_form(self, user_input, subentry) -> SubentryFlowResult:  # noqa: ANN001
        entry = self._get_entry()
        if entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="entry_not_loaded")
        errors: dict[str, str] = {}
        if user_input is not None:
            source = user_input[CONF_SOURCE]
            for other in entry.subentries.values():
                if (
                    other.subentry_type == SUBENTRY_INVERTED_COVER
                    and other.data.get(CONF_SOURCE) == source
                    and (subentry is None or other.subentry_id != subentry.subentry_id)
                ):
                    errors[CONF_SOURCE] = "already_configured"
            if not errors:
                title = user_input.get(CONF_NAME) or _state_name(self.hass, source)
                if subentry is None:
                    return self.async_create_entry(title=title, data=user_input)
                return self.async_update_and_abort(
                    entry, subentry, title=title, data=user_input
                )
        defaults = dict(subentry.data) if subentry else {}
        if user_input:
            defaults.update(user_input)
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SOURCE, default=defaults.get(CONF_SOURCE, vol.UNDEFINED)
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="cover")
                ),
                vol.Optional(
                    CONF_NAME, description={"suggested_value": defaults.get(CONF_NAME)}
                ): selector.TextSelector(),
                vol.Required(
                    CONF_INVERT_POSITION,
                    default=defaults.get(CONF_INVERT_POSITION, True),
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_INVERT_TILT, default=defaults.get(CONF_INVERT_TILT, True)
                ): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="reconfigure" if subentry else "user",
            data_schema=schema,
            errors=errors,
        )


class MappedControlFlow(ConfigSubentryFlow):
    """Add/edit a mapped control."""

    def __init__(self) -> None:
        """Init."""
        self._data: dict[str, Any] = {}
        self._subentry = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add."""
        return await self._async_basics(user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit."""
        self._subentry = self._get_reconfigure_subentry()
        self._data = dict(self._subentry.data)
        return await self._async_basics(None, step_id="reconfigure_basics")

    async def async_step_reconfigure_basics(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit basics."""
        return await self._async_basics(user_input, step_id="reconfigure_basics")

    async def _async_basics(
        self, user_input: dict[str, Any] | None, step_id: str = "user"
    ) -> SubentryFlowResult:
        if self._get_entry().state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="entry_not_loaded")
        errors: dict[str, str] = {}
        if user_input is not None:
            names = [
                n.strip() for n in user_input[CONF_NAMES].split(",") if n.strip()
            ]
            source = user_input[CONF_SOURCE]
            state = self.hass.states.get(source)
            if not names:
                errors[CONF_NAMES] = "no_names"
            elif state is None:
                errors[CONF_SOURCE] = "source_missing"
            else:
                domain = source.split(".", 1)[0]
                kind = KIND_OPTIONS if domain in OPTION_DOMAINS else KIND_LINEAR
                if self._data.get(CONF_SOURCE) != source:
                    # Source changed: drop the old mapping.
                    for key in (CONF_OPTION_MAP, CONF_SOURCE_MIN, CONF_SOURCE_MAX):
                        self._data.pop(key, None)
                self._data.update(
                    {
                        CONF_TARGET: user_input[CONF_TARGET],
                        CONF_NAMES: names,
                        CONF_SOURCE: source,
                        CONF_KIND: kind,
                    }
                )
                if kind == KIND_OPTIONS:
                    return await self.async_step_map_options()
                return await self.async_step_map_linear()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_TARGET, default=self._data.get(CONF_TARGET, vol.UNDEFINED)
                ): selector.EntitySelector(),
                vol.Required(
                    CONF_NAMES,
                    default=", ".join(self._data.get(CONF_NAMES, [])) or vol.UNDEFINED,
                ): selector.TextSelector(),
                vol.Required(
                    CONF_SOURCE, default=self._data.get(CONF_SOURCE, vol.UNDEFINED)
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=[*OPTION_DOMAINS, *NUMBER_DOMAINS]
                    )
                ),
            }
        )
        return self.async_show_form(step_id=step_id, data_schema=schema, errors=errors)

    async def async_step_map_options(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Map each option to an Alexa value."""
        state = self.hass.states.get(self._data[CONF_SOURCE])
        options: list[str] = list(state.attributes.get("options", [])) if state else []
        if user_input is not None:
            option_map = {
                opt: float(user_input[opt])
                for opt in options
                if user_input.get(opt) is not None
            }
            if not option_map:
                return self.async_show_form(
                    step_id="map_options",
                    data_schema=self._options_schema(options),
                    errors={"base": "no_mapping"},
                )
            self._data[CONF_OPTION_MAP] = option_map
            self._data[CONF_PRESETS] = user_input.get(CONF_PRESETS, True)
            self._data[CONF_PERCENT] = user_input.get(CONF_PERCENT, True)
            return self._async_finish()
        return self.async_show_form(
            step_id="map_options",
            data_schema=self._options_schema(options),
            description_placeholders={"source": self._data[CONF_SOURCE]},
        )

    def _options_schema(self, options: list[str]) -> vol.Schema:
        existing: dict[str, float] = self._data.get(CONF_OPTION_MAP) or {}
        n = len(options)
        fields: dict[Any, Any] = {}
        for i, opt in enumerate(options):
            default = existing.get(opt) if existing else (
                round(i * 100 / (n - 1)) if n > 1 else 100
            )
            fields[
                vol.Optional(opt, description={"suggested_value": default})
            ] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=100000, step=1, mode=selector.NumberSelectorMode.BOX
                )
            )
        fields[vol.Required(CONF_PRESETS, default=self._data.get(CONF_PRESETS, True))] = (
            selector.BooleanSelector()
        )
        fields[vol.Required(CONF_PERCENT, default=self._data.get(CONF_PERCENT, True))] = (
            selector.BooleanSelector()
        )
        return vol.Schema(fields)

    async def async_step_map_linear(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Linear mapping between Alexa range and source range."""
        if user_input is not None:
            self._data.update(user_input)
            return self._async_finish()
        state = self.hass.states.get(self._data[CONF_SOURCE])
        attrs = state.attributes if state else {}
        d = self._data
        num = selector.NumberSelector(
            selector.NumberSelectorConfig(step="any", mode=selector.NumberSelectorMode.BOX)
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_ALEXA_MIN, default=d.get(CONF_ALEXA_MIN, 0)): num,
                vol.Required(CONF_ALEXA_MAX, default=d.get(CONF_ALEXA_MAX, 100)): num,
                vol.Required(
                    CONF_SOURCE_MIN, default=d.get(CONF_SOURCE_MIN, attrs.get("min", 0))
                ): num,
                vol.Required(
                    CONF_SOURCE_MAX,
                    default=d.get(CONF_SOURCE_MAX, attrs.get("max", 100)),
                ): num,
                vol.Required(CONF_PRECISION, default=d.get(CONF_PRECISION, 1)): num,
                vol.Required(CONF_PERCENT, default=d.get(CONF_PERCENT, True)): (
                    selector.BooleanSelector()
                ),
            }
        )
        return self.async_show_form(
            step_id="map_linear",
            data_schema=schema,
            description_placeholders={"source": self._data[CONF_SOURCE]},
        )

    @callback
    def _async_finish(self) -> SubentryFlowResult:
        title = f"{_state_name(self.hass, self._data[CONF_TARGET])}: {self._data[CONF_NAMES][0]}"
        if self._subentry is None:
            return self.async_create_entry(title=title, data=self._data)
        return self.async_update_and_abort(
            self._get_entry(), self._subentry, title=title, data=self._data
        )

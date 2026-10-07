"""Mapped controls: extra Alexa RangeControllers that drive another entity."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import logging
import re
from typing import Any

from homeassistant.components.alexa.capabilities import AlexaCapability
from homeassistant.components.alexa.const import CONF_SUPPORTED_LOCALES
from homeassistant.components.alexa.errors import (
    AlexaInvalidValueError,
    UnsupportedProperty,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Context, HomeAssistant, State

from .const import (
    CONF_ALEXA_MAX,
    CONF_ALEXA_MIN,
    CONF_KIND,
    CONF_NAMES,
    CONF_OPTION_MAP,
    CONF_PERCENT,
    CONF_PRECISION,
    CONF_PRESETS,
    CONF_SOURCE,
    CONF_SOURCE_MAX,
    CONF_SOURCE_MIN,
    CONF_TARGET,
    KIND_OPTIONS,
)

_LOGGER = logging.getLogger(__name__)

UNIT_PERCENT = "Alexa.Unit.Percent"


def instance_for(subentry_id: str) -> str:
    """Return a stable, collision-free Alexa instance name for a control."""
    sid = re.sub(r"[^a-z0-9]", "", subentry_id.lower())
    return f"Ctl{sid[-8:]}.Lvl{sid[:6]}"


def _text_label(text: str, locale: str) -> dict[str, Any]:
    return {"@type": "text", "value": {"text": text, "locale": locale}}


@dataclass(slots=True)
class MappedControl:
    """A RangeController attached to `target` that drives `source`."""

    subentry_id: str
    target: str
    source: str
    names: list[str]
    kind: str
    option_map: dict[str, float] = field(default_factory=dict)
    presets: bool = True
    alexa_min: float = 0
    alexa_max: float = 100
    source_min: float = 0
    source_max: float = 100
    precision: float = 1
    percent: bool = True

    @property
    def instance(self) -> str:
        """Alexa instance name."""
        return instance_for(self.subentry_id)

    @classmethod
    def from_subentry(cls, subentry_id: str, data: Mapping[str, Any]) -> MappedControl:
        """Build from subentry data."""
        return cls(
            subentry_id=subentry_id,
            target=data[CONF_TARGET],
            source=data[CONF_SOURCE],
            names=list(data.get(CONF_NAMES) or []),
            kind=data[CONF_KIND],
            option_map={
                str(k): float(v)
                for k, v in (data.get(CONF_OPTION_MAP) or {}).items()
                if v is not None
            },
            presets=bool(data.get(CONF_PRESETS, True)),
            alexa_min=float(data.get(CONF_ALEXA_MIN, 0)),
            alexa_max=float(data.get(CONF_ALEXA_MAX, 100)),
            source_min=float(data.get(CONF_SOURCE_MIN, 0)),
            source_max=float(data.get(CONF_SOURCE_MAX, 100)),
            precision=float(data.get(CONF_PRECISION, 1) or 1),
            percent=bool(data.get(CONF_PERCENT, True)),
        )

    # ---- range ----------------------------------------------------------
    @property
    def range_min(self) -> float:
        """Lowest Alexa value."""
        if self.kind == KIND_OPTIONS:
            return min(self.option_map.values(), default=0)
        return min(self.alexa_min, self.alexa_max)

    @property
    def range_max(self) -> float:
        """Highest Alexa value."""
        if self.kind == KIND_OPTIONS:
            return max(self.option_map.values(), default=100)
        return max(self.alexa_min, self.alexa_max)

    def _round(self, value: float) -> float:
        prec = self.precision or 1
        value = round(round(value / prec) * prec, 6)
        return int(value) if float(value).is_integer() else value

    # ---- state -> Alexa -------------------------------------------------
    def alexa_value(self, state: State | None) -> float | None:
        """Return the Alexa range value for the source's current state."""
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        if self.kind == KIND_OPTIONS:
            value = self.option_map.get(state.state)
            return None if value is None else self._round(value)
        try:
            raw = float(state.state)
        except ValueError:
            return None
        span = self.source_max - self.source_min
        if span == 0:
            return self._round(self.alexa_min)
        frac = (raw - self.source_min) / span
        frac = min(max(frac, 0.0), 1.0)
        return self._round(self.alexa_min + frac * (self.alexa_max - self.alexa_min))

    # ---- Alexa -> source ------------------------------------------------
    def _sorted_options(self) -> list[tuple[str, float]]:
        return sorted(self.option_map.items(), key=lambda kv: kv[1])

    def _nearest_option(self, value: float) -> tuple[str, float]:
        if not self.option_map:
            raise AlexaInvalidValueError("No options mapped")
        return min(self._sorted_options(), key=lambda kv: abs(kv[1] - value))

    def resolve_set(self, value: float) -> tuple[Any, float]:
        """Return (source value, resulting Alexa value) for SetRangeValue."""
        if self.kind == KIND_OPTIONS:
            option, mapped = self._nearest_option(value)
            return option, self._round(mapped)
        lo, hi = self.range_min, self.range_max
        value = min(max(value, lo), hi)
        span = self.alexa_max - self.alexa_min
        frac = 0.0 if span == 0 else (value - self.alexa_min) / span
        raw = self.source_min + frac * (self.source_max - self.source_min)
        return raw, self._round(value)

    def resolve_adjust(
        self, state: State | None, delta: float, use_default: bool
    ) -> tuple[Any, float]:
        """Return (source value, resulting Alexa value) for AdjustRangeValue."""
        current = self.alexa_value(state)
        if self.kind == KIND_OPTIONS:
            ordered = self._sorted_options()
            if not ordered:
                raise AlexaInvalidValueError("No options mapped")
            if current is None:
                idx = 0
            else:
                idx = min(
                    range(len(ordered)), key=lambda i: abs(ordered[i][1] - current)
                )
            if use_default or current is None:
                step = 1 if delta >= 0 else -1
                new_idx = min(max(idx + step, 0), len(ordered) - 1)
                option, mapped = ordered[new_idx]
                return option, self._round(mapped)
            option, mapped = self._nearest_option(current + delta)
            if option == ordered[idx][0] and delta:
                step = 1 if delta > 0 else -1
                option, mapped = ordered[min(max(idx + step, 0), len(ordered) - 1)]
            return option, self._round(mapped)
        if use_default:
            delta = (self.range_max - self.range_min) / 10 * (1 if delta >= 0 else -1)
        base = current if current is not None else self.range_min
        return self.resolve_set(base + delta)

    async def async_apply(
        self, hass: HomeAssistant, source_value: Any, context: Context | None
    ) -> None:
        """Call the service that sets the source entity."""
        domain = self.source.split(".", 1)[0]
        if self.kind == KIND_OPTIONS:
            service, data = "select_option", {"option": source_value}
        else:
            state = hass.states.get(self.source)
            step = None
            if state is not None:
                step = state.attributes.get("step")
                lo = state.attributes.get("min")
                hi = state.attributes.get("max")
                if step:
                    base = float(lo) if lo is not None else 0.0
                    source_value = base + round((source_value - base) / step) * step
                if lo is not None:
                    source_value = max(float(lo), source_value)
                if hi is not None:
                    source_value = min(float(hi), source_value)
            source_value = round(float(source_value), 6)
            service, data = "set_value", {"value": source_value}
        await hass.services.async_call(
            domain,
            service,
            {ATTR_ENTITY_ID: self.source, **data},
            blocking=False,
            context=context,
        )


class MappedRangeCapability(AlexaCapability):
    """Alexa.RangeController backed by a MappedControl."""

    supported_locales = set(CONF_SUPPORTED_LOCALES)

    def __init__(
        self, hass: HomeAssistant, target: State, control: MappedControl, locale: str
    ) -> None:
        """Initialize."""
        super().__init__(target, instance=control.instance)
        self.hass = hass
        self.control = control
        self.locale = locale or "en-US"
        self._resource = None
        self._semantics = None

    def name(self) -> str:
        """Interface name."""
        return "Alexa.RangeController"

    def properties_supported(self) -> list[dict[str, str]]:
        """Supported properties."""
        return [{"name": "rangeValue"}]

    def properties_proactively_reported(self) -> bool:
        """Proactively reported."""
        return True

    def properties_retrievable(self) -> bool:
        """Retrievable."""
        return True

    def get_property(self, name: str) -> Any:
        """Return the current value."""
        if name != "rangeValue":
            raise UnsupportedProperty(name)
        return self.control.alexa_value(self.hass.states.get(self.control.source))

    def capability_resources(self) -> dict[str, list[dict[str, Any]]]:
        """Friendly names of this control."""
        names = self.control.names or ["Level"]
        return {"friendlyNames": [_text_label(n, self.locale) for n in names]}

    def configuration(self) -> dict[str, Any]:
        """Range configuration."""
        ctl = self.control
        cfg: dict[str, Any] = {
            "supportedRange": {
                "minimumValue": ctl._round(ctl.range_min),
                "maximumValue": ctl._round(ctl.range_max),
                "precision": ctl.precision or 1,
            }
        }
        if ctl.percent:
            cfg["unitOfMeasure"] = UNIT_PERCENT
        if ctl.kind == KIND_OPTIONS and ctl.presets:
            presets = []
            seen_values: set[float] = set()
            seen_names: set[str] = set()
            for option, value in ctl._sorted_options():
                label = option.replace("_", " ").strip()
                key = label.lower()
                if (
                    not re.search(r"[A-Za-z]", label)
                    or key in ("on", "off")
                    or key in seen_names
                    or value in seen_values
                ):
                    continue
                seen_names.add(key)
                seen_values.add(value)
                presets.append(
                    {
                        "rangeValue": ctl._round(value),
                        "presetResources": {
                            "friendlyNames": [_text_label(label, self.locale)]
                        },
                    }
                )
            if presets:
                cfg["presets"] = presets
        return cfg

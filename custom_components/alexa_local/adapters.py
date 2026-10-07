"""Hook into core's Alexa entity adapters to add mapped controls.

Core looks adapters up as ``ENTITY_ADAPTERS[domain](hass, config, state)``.
We swap each entry for a small factory that returns the original adapter,
or - only when the config is ours and the entity has mapped controls - a
subclass that yields the extra RangeControllers. Other users of the core
Alexa code (e.g. Nabu Casa cloud) are unaffected.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from typing import Any

from homeassistant.components.alexa.entities import ENTITY_ADAPTERS, AlexaEntity

from .controls import MappedRangeCapability

_ORIGINALS: dict[str, type[AlexaEntity]] = {}
_EXT_CLASSES: dict[type[AlexaEntity], type[AlexaEntity]] = {}
_USERS = 0


def _ext_class(base: type[AlexaEntity]) -> type[AlexaEntity]:
    if (cls := _EXT_CLASSES.get(base)) is not None:
        return cls

    class Extended(base):  # type: ignore[valid-type, misc]
        _local_controls: tuple = ()

        def interfaces(self) -> Generator[Any]:
            yield from super().interfaces()
            for control in self._local_controls:
                yield MappedRangeCapability(
                    self.hass, self.entity, control, self.config.locale
                )

    Extended.__name__ = Extended.__qualname__ = f"AlexaLocal{base.__name__}"
    _EXT_CLASSES[base] = Extended
    return Extended


def _factory(base: type[AlexaEntity]) -> Callable[..., AlexaEntity]:
    def create(hass, config, state) -> AlexaEntity:  # noqa: ANN001
        controls_for = getattr(config, "alexa_local_controls_for", None)
        if controls_for is not None and (controls := controls_for(state.entity_id)):
            entity = _ext_class(base)(hass, config, state)
            entity._local_controls = tuple(controls)  # noqa: SLF001
            return entity
        return base(hass, config, state)

    create.__wrapped__ = base  # type: ignore[attr-defined]
    create.__name__ = f"alexa_local_{base.__name__}"
    return create


def install() -> None:
    """Install the adapter factories (ref counted)."""
    global _USERS  # noqa: PLW0603
    _USERS += 1
    if _ORIGINALS:
        return
    for domain, adapter in list(ENTITY_ADAPTERS.items()):
        base = getattr(adapter, "__wrapped__", adapter)
        _ORIGINALS[domain] = base
        ENTITY_ADAPTERS[domain] = _factory(base)  # type: ignore[assignment]


def uninstall() -> None:
    """Restore the original adapters."""
    global _USERS  # noqa: PLW0603
    _USERS = max(0, _USERS - 1)
    if _USERS:
        return
    for domain, base in _ORIGINALS.items():
        ENTITY_ADAPTERS[domain] = base
    _ORIGINALS.clear()


def supported_domains() -> set[str]:
    """Domains the Alexa code knows how to expose."""
    return set(ENTITY_ADAPTERS)

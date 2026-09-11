"""Kind contracts: what a component *kind* does to light.

The archetype of a component (source / selector / splitter / passive / detector) is derived from
its ``kind`` here, in code — never from config strings. Each contract also says what a selector's
state means (which positions transmit, which block, which aspects emit), how a stateful source
classifies its light and how a derived selector computes its position from the environment.

The registry is injectable: ocabox-server registers its device kinds, tests register toys; the
default registry knows the kinds that exist at OCM today.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Mapping

from datamodels.optics import DARK, Archetype, OpticalComponentSpec, SkyState, SourceFamily

if TYPE_CHECKING:
    from datamodels.optics import Environment


class SelectorShape(Enum):
    """How a selector switches light. Resolved per component: the same ``mirror`` kind is
    OUTPUT_PORTS as M3 (one input, a port per instrument) and FAN_IN as BESO's M4/M5
    (several inputs, one output)."""

    OUTPUT_PORTS = "output_ports"  #: position = the output port that transmits; every other output is dark
    FAN_IN = "fan_in"  #: position = which input is transmitted (``inputs: {pos: X}``)
    GATE = "gate"  #: one input, one output; a position either transmits or blocks (cover, dark slide)


@dataclass(frozen=True)
class Aspect:
    """A secondary, independent axis of a selector that *emits* when in ``on``: the calibrator
    lamp of an Alpaca ICoverCalibrator. Its proven state lives under ``<component>.<name>``."""

    name: str
    emits: str
    on: str = "on"
    off: str = "off"

    @property
    def positions(self) -> frozenset[str]:
        return frozenset({self.on, self.off})


@dataclass(frozen=True)
class Kind:
    """Base contract. Subclasses refine the archetype-specific behaviour; the solver only ever
    talks to these methods."""

    name: str
    archetype: Archetype

    def declared_positions(self, spec: OpticalComponentSpec) -> frozenset[str] | None:
        """Position vocabulary of the component: ``positions:`` from config when present, the kind's
        intrinsic positions otherwise. ``None`` = unconstrained (a splitter without ports)."""
        if spec.positions is not None:
            return frozenset(spec.positions)
        return None


# --- sources ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Source(Kind):
    archetype: Archetype = field(default=Archetype.SOURCE, init=False)

    def possible_classes(self, spec: OpticalComponentSpec) -> frozenset[str]:
        """Every light class this source can ever emit (static, for route enumeration)."""
        raise NotImplementedError

    def emission(self, spec: OpticalComponentSpec, position: str | None, env: "Environment") -> str | None:
        """The class emitted now. ``position`` is the proven state of the source if it has one
        (a lamp: on/off), ``None`` if none is known. Returns ``None`` when the class cannot be
        decided (``undefined``)."""
        raise NotImplementedError


@dataclass(frozen=True)
class ConstantSource(Source):
    """Emits one class whenever it is on the path. ``switchable`` sources (lamps) are dark when
    proven off; without telemetry they are taken as lit — the class names the *type* of light
    on the path, and a lamp's power state is the calibration sequence's business."""

    emits: str = ""
    switchable: bool = False

    def possible_classes(self, spec: OpticalComponentSpec) -> frozenset[str]:
        return frozenset({self.emits})

    def emission(self, spec: OpticalComponentSpec, position: str | None, env: "Environment") -> str | None:
        if self.switchable and position == "off":
            return DARK
        return self.emits


@dataclass(frozen=True)
class SkySource(Source):
    """The sky is a stateful source: its class follows the sun. Thresholds are kind knowledge,
    overridable per component (``science_sun_alt``, ``flat_sun_alt``: ``[min, max]``)."""

    science_sun_alt: float = -18.0
    flat_sun_alt: tuple[float, float] = (-15.0, 1.0)

    def possible_classes(self, spec: OpticalComponentSpec) -> frozenset[str]:
        return frozenset(f"{SourceFamily.SKY}.{s}" for s in SkyState)

    def thresholds(self, spec: OpticalComponentSpec) -> tuple[float, tuple[float, float]]:
        extra = spec.model_extra or {}
        science = float(extra.get("science_sun_alt", self.science_sun_alt))
        flat = extra.get("flat_sun_alt", self.flat_sun_alt)
        return science, (float(flat[0]), float(flat[1]))

    def sky_state(self, spec: OpticalComponentSpec, sun_alt_deg: float | None) -> SkyState | None:
        if sun_alt_deg is None:
            return None
        science, (flat_lo, flat_hi) = self.thresholds(spec)
        if sun_alt_deg < science:
            return SkyState.SCIENCE
        if sun_alt_deg < flat_lo:
            return SkyState.TWILIGHT
        if sun_alt_deg <= flat_hi:
            return SkyState.FLAT
        return SkyState.DAY

    def emission(self, spec: OpticalComponentSpec, position: str | None, env: "Environment") -> str | None:
        state = self.sky_state(spec, env.sun_alt_deg)
        return None if state is None else f"{SourceFamily.SKY}.{state}"


# --- selectors -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Selector(Kind):
    """A switch. ``intrinsic_positions`` are known without config (a cover: open/close);
    ``dark_positions`` block the light (the closed cover, the closed dome). Aspects are the
    independent emitting axes."""

    archetype: Archetype = field(default=Archetype.SELECTOR, init=False)
    intrinsic_positions: frozenset[str] = frozenset()
    dark_positions: frozenset[str] = frozenset()
    aspects: tuple[Aspect, ...] = ()
    gate: bool = False  #: one input, one output regardless of config (cover, dark slide)
    fan_in_positions: frozenset[str] | None = None  #: allowed ``inputs:`` keys when the kind fixes them (dome); None = free

    def aspect(self, name: str) -> Aspect | None:
        return next((a for a in self.aspects if a.name == name), None)

    def declared_positions(self, spec: OpticalComponentSpec) -> frozenset[str] | None:
        declared = super().declared_positions(spec)
        if declared is None:
            declared = frozenset()
        if spec.optics is not None and spec.optics.is_fan_in:
            declared = declared | frozenset(spec.optics.inputs or {})
        declared = declared | self.intrinsic_positions
        return declared or None

    def shape(self, spec: OpticalComponentSpec) -> SelectorShape:
        if spec.optics is not None and spec.optics.is_fan_in:
            return SelectorShape.FAN_IN
        if self.gate:
            return SelectorShape.GATE
        return SelectorShape.OUTPUT_PORTS

    def blocks(self, spec: OpticalComponentSpec, position: str) -> bool:
        if position in self.dark_positions:
            return True
        # config may mark a position as blocking: `positions: {closed: {slot: 7, dark: true}}`
        if spec.positions is not None and position in spec.positions:
            return bool((spec.positions[position].model_extra or {}).get("dark", False))
        return False

    def derived_position(
        self, spec: OpticalComponentSpec, components: Mapping[str, OpticalComponentSpec], env: "Environment"
    ) -> str | None:
        """Position computed from the environment when no telemetry is given for the selector
        itself. ``None`` = cannot derive → the selector is ``undefined`` without telemetry."""
        return None


def _angular_distance(a: float, b: float) -> float:
    d = abs((a - b) % 360.0)
    return min(d, 360.0 - d)


@dataclass(frozen=True)
class DomeKind(Selector):
    """The dome is a *derived* selector between the sky and the flat screen: shutter closed ⇒
    dark; shutter open and mount + dome pointed at the screen (``domeflat_az`` on the dome,
    ``domeflat_az_offset`` / ``domeflat_alt`` on the mount — values already in config) ⇒
    ``flat``; otherwise ``open``. The input names are fixed by the kind so that the derivation
    and the authored ``inputs:`` agree."""

    intrinsic_positions: frozenset[str] = frozenset({"closed"})
    dark_positions: frozenset[str] = frozenset({"closed"})
    fan_in_positions: frozenset[str] | None = frozenset({"open", "flat"})
    open_input: str = "open"
    flat_input: str = "flat"
    default_tolerance_deg: float = 3.0

    def derived_position(
        self, spec: OpticalComponentSpec, components: Mapping[str, OpticalComponentSpec], env: "Environment"
    ) -> str | None:
        if env.dome_shutter_open is None:
            return None
        if not env.dome_shutter_open:
            return "closed"
        inputs = (spec.optics.inputs or {}) if spec.optics is not None else {}
        if self.flat_input not in inputs:
            return self.open_input
        extra = spec.model_extra or {}
        domeflat_az = extra.get("domeflat_az")
        mount = next((c for c in components.values() if c.kind == "telescope"), None)
        mount_extra = (mount.model_extra or {}) if mount is not None else {}
        if domeflat_az is None or env.dome_az_deg is None or env.mount_az_deg is None or env.mount_alt_deg is None:
            return None
        tol = float(extra.get("slew_tolerance", self.default_tolerance_deg))
        mount_flat_az = float(domeflat_az) + float(mount_extra.get("domeflat_az_offset", 0.0))
        at_screen = _angular_distance(env.dome_az_deg, float(domeflat_az)) <= tol and _angular_distance(
            env.mount_az_deg, mount_flat_az
        ) <= tol
        flat_alt = mount_extra.get("domeflat_alt")
        if at_screen and flat_alt is not None:
            at_screen = abs(env.mount_alt_deg - float(flat_alt)) <= tol
        return self.flat_input if at_screen else self.open_input


# --- the rest ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Splitter(Kind):
    archetype: Archetype = field(default=Archetype.SPLITTER, init=False)


@dataclass(frozen=True)
class Passive(Kind):
    """Transmits unchanged. ``promotable`` kinds become a GATE selector when their config declares
    ``positions:`` with at least one ``dark: true`` position (a filterwheel with a dark slide)."""

    archetype: Archetype = field(default=Archetype.PASSIVE, init=False)
    promotable: bool = False

    def promoted(self, spec: OpticalComponentSpec) -> Selector | None:
        if not self.promotable or spec.positions is None:
            return None
        dark = frozenset(
            sym for sym in spec.positions if bool((spec.positions[sym].model_extra or {}).get("dark", False))
        )
        if not dark:
            return None
        return Selector(name=self.name, gate=True, dark_positions=dark)


@dataclass(frozen=True)
class Detector(Kind):
    archetype: Archetype = field(default=Archetype.DETECTOR, init=False)


# --- registry ----------------------------------------------------------------------------------


class KindRegistry:
    """``kind`` name → contract. Kinds outside optics (mount, focuser, switch …) are simply not
    registered; a component of such a kind may not carry an ``optics:`` block."""

    def __init__(self, kinds: Mapping[str, Kind] | None = None):
        self._kinds: dict[str, Kind] = dict(kinds or {})

    def register(self, kind: Kind, *aliases: str) -> None:
        for name in (kind.name, *aliases):
            self._kinds[name] = kind

    def get(self, kind_name: str) -> Kind | None:
        return self._kinds.get(kind_name)

    def resolve(self, spec: OpticalComponentSpec) -> Kind | None:
        """The contract for a concrete component, after self-promotion."""
        kind = self.get(spec.kind)
        if isinstance(kind, Passive):
            promoted = kind.promoted(spec)
            if promoted is not None:
                return promoted
        return kind

    def __contains__(self, kind_name: object) -> bool:
        return kind_name in self._kinds

    def copy(self) -> "KindRegistry":
        return KindRegistry(self._kinds)


COVER_CALIBRATOR = Selector(
    name="covercalibrator",
    gate=True,
    intrinsic_positions=frozenset({"open", "close"}),
    dark_positions=frozenset({"close"}),
    aspects=(Aspect(name="calibrator", emits=str(SourceFamily.LAMP)),),
)


def default_registry() -> KindRegistry:
    reg = KindRegistry()
    reg.register(SkySource(name="sky"))
    reg.register(ConstantSource(name="flatscreen", emits=str(SourceFamily.FLATSCREEN)))
    reg.register(ConstantSource(name="lamp", emits=str(SourceFamily.LAMP), switchable=True))
    reg.register(ConstantSource(name="beamdump", emits=DARK))
    reg.register(DomeKind(name="dome"))
    reg.register(COVER_CALIBRATOR)
    reg.register(Selector(name="tertiaryOCA"), "tertiary", "mirror")
    reg.register(Splitter(name="splitter"))
    reg.register(Passive(name="rotator"))
    reg.register(Passive(name="fiber"))
    reg.register(Passive(name="filterwheel", promotable=True))
    reg.register(Detector(name="camera"))
    reg.register(Detector(name="spectrograph"))
    return reg


DEFAULT_REGISTRY = default_registry()

"""Kind contracts: what a component *kind* does to light.

Every optical kind is a **transfer table**: given the values of its state axes, which of its
output ports carry what. A table value is a set of *signals* — ``Transmit(input port)`` (light
arriving at that input passes) or ``Emit(light class)`` (the component itself is what you look
at: a source, a closed cover, the back of a mirror; ``dark`` and ``undefined`` are emissions of
the reserved classes). The traversal (``sees``, ``routes``) knows nothing about mirrors, covers
or domes: it only asks kinds for their tables. The archetype is derived from ``kind`` here, in
code — never from config strings.

State axes: a selector has a primary axis (its position, state key = the component name) and
may have secondary axes (``covercalibrator.calibrator``). Each axis names its vocabulary in
authored order and, for the dome, how to derive its value from the environment. No axis has a
value nobody reported: unreported is undefined, like stale or moving (commanded ≠ proven).

The registry is injectable: ocabox-server registers its device kinds, tests register toys; the
default registry knows the kinds that exist at OCM today.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, ClassVar, Mapping

from datamodels.optics import DARK, UNDEFINED, Archetype, Environment, OpticalComponentSpec, SkyState, SourceFamily

# --- signals -----------------------------------------------------------------------------------

#: Name of the single input / single output port of components that have just one.
IN = "in"
OUT = "out"


@dataclass(frozen=True)
class Transmit:
    """Light arriving at ``port`` (an input port of the same component) passes to this output."""

    port: str = IN


@dataclass(frozen=True)
class Emit:
    """This component is the terminal: it puts ``light`` on the output (a source class, or the
    reserved ``dark`` / ``undefined``)."""

    light: str


Signal = Transmit | Emit
Signals = frozenset[Signal]
DARK_OUT: Signals = frozenset({Emit(DARK)})
UNDEFINED_OUT: Signals = frozenset({Emit(UNDEFINED)})


def is_dark(signal: Signal) -> bool:
    return isinstance(signal, Emit) and signal.light == DARK


# --- axes --------------------------------------------------------------------------------------

Deriver = Callable[[OpticalComponentSpec, Mapping[str, OpticalComponentSpec], Environment], "str | None"]


@dataclass(frozen=True)
class Axis:
    """One state axis of a component. ``name=None`` is the primary axis (state key = the
    component name); a named axis is an aspect (state key ``component.name``).

    ``vocabulary`` — the legal symbols in authored order (routes enumerate and tie-break in this
    order). ``derive`` — computes the value from the environment when no telemetry names it (the
    dome). ``actuated`` axes appear in routes as positions to set; the sun is not actuated. An
    axis with neither telemetry nor derivation is undefined — never assumed."""

    name: str | None
    vocabulary: tuple[str, ...]
    derive: Deriver | None = None
    actuated: bool = True


#: axis name → value; a ``None`` value means *undefined*.
AxisValues = Mapping[str | None, str | None]


class SelectorShape(Enum):
    """Descriptive only (introspection, drawing): how a selector's ports are laid out. The
    traversal never looks at it — it reads the transfer table."""

    OUTPUT_PORTS = "output_ports"  #: one input, a port per position (M3)
    FAN_IN = "fan_in"  #: several inputs, one output, the position picks the input (M4/M5, dome)
    GATE = "gate"  #: one input, one output; a position either transmits or blocks (cover)


# --- kinds ---------------------------------------------------------------------------------------


def _number(extra: Mapping, key: str, problems: list[str], *, minimum: float | None = None) -> float | None:
    value = extra.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        problems.append(f"{key} must be a number, got {value!r}")
        return None
    if minimum is not None and value < minimum:
        problems.append(f"{key} must be >= {minimum:g}, got {value!r}")
        return None
    return float(value)


@dataclass(frozen=True)
class Kind:
    """Base contract. The solver only ever talks to these methods."""

    name: str
    archetype: Archetype

    def inputs(self, spec: OpticalComponentSpec) -> frozenset[str]:
        """Input port names: ``{IN}`` for single-input components, the position symbols for a
        fan-in selector, none for a source."""
        return frozenset({IN})

    def outputs(self, spec: OpticalComponentSpec) -> frozenset[str] | None:
        """Output port names: ``{OUT}`` for a single output, the position symbols for a switch
        between instruments, ``None`` = open (a splitter feeds whatever port a downstream names),
        empty for a detector."""
        return frozenset({OUT})

    def axes(self, spec: OpticalComponentSpec) -> tuple[Axis, ...]:
        return ()

    def transfer(self, spec: OpticalComponentSpec, values: AxisValues) -> dict[str, Signals]:
        """The table row for these axis values: output port → signals. Kinds with open outputs
        (splitters) return the row under ``OUT`` and it applies to every port."""
        return {OUT: frozenset({Transmit()})}

    def validate(self, spec: OpticalComponentSpec, components: Mapping[str, OpticalComponentSpec]) -> list[str]:
        """Kind-specific options read from the component's extras, checked at graph load so that a
        malformed threshold is an ``invalid_option`` verdict, never a ``TypeError`` at 3 a.m."""
        return []


# --- sources: no inputs, one output, the emission decided by the kind ------------------------------


@dataclass(frozen=True)
class Source(Kind):
    archetype: Archetype = field(default=Archetype.SOURCE, init=False)

    def inputs(self, spec):
        return frozenset()

    def possible_classes(self, spec: OpticalComponentSpec) -> frozenset[str]:
        """Every light class this source can ever emit (static route enumeration)."""
        raise NotImplementedError

    def emission(self, spec: OpticalComponentSpec, values: AxisValues) -> str:
        """The class emitted for these axis values; ``UNDEFINED`` when it cannot be decided."""
        raise NotImplementedError

    def transfer(self, spec, values):
        return {OUT: frozenset({Emit(self.emission(spec, values))})}


@dataclass(frozen=True)
class ConstantSource(Source):
    """Emits one class whenever it is on the path. ``switchable`` sources (lamps) have a power
    axis: proven ``on`` ⇒ the class, proven ``off`` ⇒ dark, unreported or unusable ⇒ undefined —
    a lamp nobody has seen lit certifies no arc."""

    emits: str = ""
    switchable: bool = False

    def axes(self, spec):
        return (Axis(None, ("off", "on"), actuated=False),) if self.switchable else ()

    def possible_classes(self, spec):
        return frozenset({self.emits})

    def emission(self, spec, values):
        if not self.switchable:
            return self.emits
        power = values.get(None)
        if power is None:
            return UNDEFINED
        return DARK if power == "off" else self.emits


@dataclass(frozen=True)
class SkySource(Source):
    """The sky is a stateful source: its class follows the sun. Thresholds are kind knowledge,
    overridable per component (``science_sun_alt``, ``flat_sun_alt: [min, max]``)."""

    name: str = "sky"
    science_sun_alt: float = -18.0
    flat_sun_alt: tuple[float, float] = (-15.0, 1.0)

    def axes(self, spec):
        return (Axis(None, tuple(str(s) for s in SkyState), derive=self._sun_state, actuated=False),)

    def _sun_state(self, spec, components, env: Environment) -> str | None:
        state = self.sky_state(spec, env.sun_alt_deg)
        return None if state is None else str(state)

    def possible_classes(self, spec):
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

    def emission(self, spec, values):
        state = values.get(None)
        return UNDEFINED if state is None else f"{SourceFamily.SKY}.{state}"

    def validate(self, spec, components):
        extra = spec.model_extra or {}
        problems: list[str] = []
        science = _number(extra, "science_sun_alt", problems)
        flat = extra.get("flat_sun_alt")
        lo = hi = None
        if flat is not None:
            if not isinstance(flat, (list, tuple)) or len(flat) != 2 or any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in flat):
                problems.append(f"flat_sun_alt must be [min, max] in degrees, got {flat!r}")
            else:
                lo, hi = float(flat[0]), float(flat[1])
        if problems:
            return problems
        science = self.science_sun_alt if science is None else science
        lo = self.flat_sun_alt[0] if lo is None else lo
        hi = self.flat_sun_alt[1] if hi is None else hi
        if not science < lo <= hi:
            problems.append(f"sun-altitude thresholds must satisfy science_sun_alt < flat min <= flat max, got {science} / {lo} / {hi}")
        return problems


# --- selectors: a position axis that routes or blocks --------------------------------------------


@dataclass(frozen=True)
class Aspect:
    """A secondary axis of a selector that *emits* when ``on`` (the calibrator lamp of an Alpaca
    ICoverCalibrator). Unreported or unusable ⇒ undefined, like every axis: a lamp nobody has seen
    off certifies no clean sky (a cover without a lamp reports ``NotPresent`` ⇒ ``off``)."""

    name: str
    emits: str
    on: str = "on"
    off: str = "off"

    @property
    def positions(self) -> tuple[str, str]:
        return (self.off, self.on)

    def axis(self) -> Axis:
        return Axis(self.name, self.positions)


@dataclass(frozen=True)
class Selector(Kind):
    """A switch. Its physical shape follows from the config, the table is the same idea for all:

    - ``inputs: {pos: X}`` ⇒ **fan-in**: the position picks which input passes (M4/M5, dome);
    - ``gate=True`` ⇒ **gate**: one input, one output, a position transmits or blocks (cover);
    - otherwise ⇒ **output ports**: one input, a port per position, the position picks the output (M3).

    ``intrinsic_positions`` are known without config (a cover: open/close); ``dark_positions``
    block; ``fan_in_positions`` fixes the authored input names when the kind requires it (dome);
    ``derive_position`` computes the position from the environment when no telemetry names it.
    """

    archetype: Archetype = field(default=Archetype.SELECTOR, init=False)
    intrinsic_positions: tuple[str, ...] = ()
    dark_positions: frozenset[str] = frozenset()
    aspects: tuple[Aspect, ...] = ()
    gate: bool = False
    fan_in_positions: frozenset[str] | None = None
    derive_position: Deriver | None = None

    # -- structure

    def is_fan_in(self, spec: OpticalComponentSpec) -> bool:
        return spec.optics is not None and spec.optics.is_fan_in

    def shape(self, spec: OpticalComponentSpec) -> SelectorShape:
        if self.is_fan_in(spec):
            return SelectorShape.FAN_IN
        return SelectorShape.GATE if self.gate else SelectorShape.OUTPUT_PORTS

    def positions(self, spec: OpticalComponentSpec) -> tuple[str, ...]:
        """The position vocabulary in authored order: declared ``positions:``, then fan-in
        ``inputs:``, then the kind's intrinsic ones — each symbol once."""
        authored: list[str] = list(spec.positions) if spec.positions is not None else []
        if self.is_fan_in(spec):
            authored += list(spec.optics.inputs or {})
        authored += self.intrinsic_positions
        return tuple(dict.fromkeys(authored))

    def blocks(self, spec: OpticalComponentSpec, position: str) -> bool:
        if position in self.dark_positions:
            return True
        if spec.positions is not None and position in spec.positions:
            return bool((spec.positions[position].model_extra or {}).get("dark", False))
        return False

    def inputs(self, spec):
        return frozenset(spec.optics.inputs or {}) if self.is_fan_in(spec) else frozenset({IN})

    def outputs(self, spec):
        if self.is_fan_in(spec) or self.gate:
            return frozenset({OUT})
        return frozenset(self.positions(spec))

    def aspect(self, name: str) -> Aspect | None:
        return next((a for a in self.aspects if a.name == name), None)

    def axes(self, spec):
        return (Axis(None, self.positions(spec), derive=self.derive_position), *(a.axis() for a in self.aspects))

    # -- the table

    def transfer(self, spec, values):
        position = values.get(None)
        own: set[Signal] = set()  # what the selector emits by itself, independent of its position
        for aspect in self.aspects:
            a = values.get(aspect.name)
            if a is None:
                own.add(Emit(UNDEFINED))
            elif a == aspect.on:
                own.add(Emit(aspect.emits))
        outputs = self.outputs(spec)
        if position is None:
            return {port: frozenset(own | {Emit(UNDEFINED)}) for port in outputs}
        if self.is_fan_in(spec):
            through = {Transmit(position)} if position in self.inputs(spec) else set()
        elif self.gate:
            through = set() if self.blocks(spec, position) else {Transmit(IN)}
        else:
            return {port: frozenset(({Transmit(IN)} if port == position else set()) | own) or DARK_OUT for port in outputs}
        return {OUT: frozenset(through | own) or DARK_OUT}


def _angular_distance(a: float, b: float) -> float:
    d = abs((a - b) % 360.0)
    return min(d, 360.0 - d)


@dataclass(frozen=True)
class DomeKind(Selector):
    """The dome is a *derived* fan-in selector between the sky and the flat screen: shutter
    closed ⇒ ``closed`` (dark); open and mount + dome at the screen (``domeflat_az`` on the dome,
    ``domeflat_az_offset`` / ``domeflat_alt`` on the mount — values already in config) ⇒ ``flat``;
    otherwise ``open``. Undecidable (no shutter state; pointing unknown or no altitude target
    while a ``flat`` input exists) ⇒ undefined. Input names are fixed by the kind so that the
    derivation and the authored ``inputs:`` agree."""

    OPEN: ClassVar[str] = "open"
    FLAT: ClassVar[str] = "flat"
    CLOSED: ClassVar[str] = "closed"
    DEFAULT_TOLERANCE_DEG: ClassVar[float] = 3.0

    name: str = "dome"
    intrinsic_positions: tuple[str, ...] = ("closed",)
    dark_positions: frozenset[str] = frozenset({"closed"})
    fan_in_positions: frozenset[str] | None = frozenset({"open", "flat"})

    def axes(self, spec):
        return (Axis(None, self.positions(spec), derive=self.derived_position), *(a.axis() for a in self.aspects))

    def derived_position(self, spec, components, env: Environment) -> str | None:
        if env.dome_shutter_open is None:
            return None
        if not env.dome_shutter_open:
            return self.CLOSED
        if self.FLAT not in self.inputs(spec):
            return self.OPEN
        extra = spec.model_extra or {}
        domeflat_az = extra.get("domeflat_az")
        mount = next((c for c in components.values() if c.kind == "telescope"), None)
        mount_extra = (mount.model_extra or {}) if mount is not None else {}
        if domeflat_az is None or env.dome_az_deg is None or env.mount_az_deg is None or env.mount_alt_deg is None:
            return None
        tol = float(extra.get("slew_tolerance", self.DEFAULT_TOLERANCE_DEG))
        mount_flat_az = float(domeflat_az) + float(mount_extra.get("domeflat_az_offset", 0.0))
        at_screen_az = _angular_distance(env.dome_az_deg, float(domeflat_az)) <= tol and _angular_distance(env.mount_az_deg, mount_flat_az) <= tol
        if not at_screen_az:
            return self.OPEN
        flat_alt = mount_extra.get("domeflat_alt")
        if flat_alt is None:
            return None  # at the screen azimuth but no altitude target configured: flat or open is undecidable
        return self.FLAT if abs(env.mount_alt_deg - float(flat_alt)) <= tol else self.OPEN

    def validate(self, spec, components):
        problems: list[str] = []
        extra = spec.model_extra or {}
        _number(extra, "domeflat_az", problems)
        _number(extra, "slew_tolerance", problems, minimum=0.0)
        mount = next((c for c in components.values() if c.kind == "telescope"), None)
        if mount is not None:
            mount_extra = mount.model_extra or {}
            _number(mount_extra, "domeflat_az_offset", problems)
            _number(mount_extra, "domeflat_alt", problems)
        return problems


# --- the rest ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Splitter(Kind):
    """One input, every output at once. Ports are whatever downstream names, unless the config
    declares ``positions:``."""

    archetype: Archetype = field(default=Archetype.SPLITTER, init=False)

    def outputs(self, spec):
        return frozenset(spec.positions) if spec.positions is not None else None


@dataclass(frozen=True)
class Passive(Kind):
    """Transmits unchanged. ``promotable`` kinds become a gate selector when their config declares
    ``positions:`` with at least one ``dark: true`` position (a filter wheel with a dark slide)."""

    archetype: Archetype = field(default=Archetype.PASSIVE, init=False)
    promotable: bool = False

    def promoted(self, spec: OpticalComponentSpec) -> Selector | None:
        if not self.promotable or spec.positions is None:
            return None
        dark = frozenset(sym for sym in spec.positions if bool((spec.positions[sym].model_extra or {}).get("dark", False)))
        if not dark:
            return None
        return Selector(name=self.name, gate=True, dark_positions=dark)


@dataclass(frozen=True)
class Detector(Kind):
    archetype: Archetype = field(default=Archetype.DETECTOR, init=False)

    def outputs(self, spec):
        return frozenset()

    def transfer(self, spec, values):
        return {}


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
    intrinsic_positions=("open", "close"),
    dark_positions=frozenset({"close"}),
    aspects=(Aspect(name="calibrator", emits=str(SourceFamily.LAMP)),),
)


def default_registry() -> KindRegistry:
    reg = KindRegistry()
    reg.register(SkySource())
    reg.register(ConstantSource(name="flatscreen", emits=str(SourceFamily.FLATSCREEN)))
    reg.register(ConstantSource(name="lamp", emits=str(SourceFamily.LAMP), switchable=True))
    reg.register(ConstantSource(name="beamdump", emits=DARK))
    reg.register(DomeKind())
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

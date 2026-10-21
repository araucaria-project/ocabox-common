# Changelog
All notable changes to this project will be documented in this file.

## [Unreleased]

## [1.11.0]
### Changed
- One set of timing defaults, in `obcom.comunication.timing` (one line, one sentence each): T1
  `DEFAULT_REFRESH_AFTER` 0.5 s is now the wire default too (`ValueRequest` used 60 s when the
  caller gave none, so a bare client refreshed its cache every 0.5 s and was served values up to a
  minute old); T0 `delay` defaults to the request's T1 — a change is delivered at most once per
  refresh cadence — instead of a fixed 5 s (`DEFAULT_DELAY` is gone); `delay=0` is "no rate cap",
  a negative delay is a `ValueError`; T1 is no longer taken from `delay` when only `delay` is given
  (`BaseClientAPI.subscribe`, `send_cycle_multipart`). The T2 floor is derived
  (`T2_FLOOR = RTT_BUDGET / SERVER_MARGIN_FRACTION` = 5 s, unchanged); the long-poll window,
  renewal-credibility, starvation and catch-all constants are named there as well. The
  "delay does not fit the window" warning is raised only for an explicit `delay` (the derived
  default always fits). `PeriodicCycleQuery` clamps its period at `PERIODIC_MIN_DELAY` (0.5 s)
  instead of only warning. Wire unchanged; `ValueRequest.DEFAULT_*` and
  `BaseCycleQuery.DEFAULT_REQUEST_TIMEOUT` stay as names.

## [1.10.3]
### Fixed
- T0 (`delay` of a cycle query — "not more often than this") is kept by the client, counted from
  the delivery on its own monotonic clock: after a delivery the next request waits until `delay`
  has passed. The echoed `no_send_before = ts + delay` alone anchored it on the device read, so
  beside a faster subscriber the value served was already old and the server's gate opened early
  (jk15 M3: `delay=1` answered every 0.51 s; with this, every 1.01 s). After a 4004 renewal, an
  error, a timeout or a server-delivered None the next request still goes at once. The pause comes
  out of the long-poll window, never out of the truth bound: the first request after it gets the
  window left counted from the delivery (floored at `WINDOW_FLOOR`, 1 s), so its 4004 renewal still
  lands within T2 and `is_contact_fresh()` never drops; the pause is capped at the window less
  the floor (a warning when `delay` does not fit). The renewal-credibility and loop-starvation
  thresholds follow the window actually sent. Wire unchanged; the server's gate stays.

## [1.10.2]
### Fixed
- A cycle query whose callback runner died (an exception escaping the per-callback guard) looked
  alive forever: polling went on, `is_stopped()` stayed False, no callback fired again. The runner's
  death now stops the query at once with a loud `CALLBACK RUNNER DIED` line, `runner_failure` carries
  the exception, waiters and consumers see a stopped subscription.

## [1.10.1]
### Fixed
- A cycle query's callback without `__name__` (a `functools.partial`, a callable object) no longer
  kills the callback runner: the runner named it for its debug log outside its `try`, the task died
  with `AttributeError`, polling went on and no callback ever fired again.

## [1.10.0]
### Changed
- `schematic()` lays a telescope out as a tidy tree: the light straight on, a switch into branches
  of equal depth centred between them with a corridor for their labels (M3 between the camera and
  BESO), a short branch beside its switch on the outer side (a guider off its pick-off), a side
  feeder beside what it feeds (M5 beside M4), inputs meeting in one component side by side over it
  (the sky and the flat screen over the dome, the lamps over M5). Rows may be fractional. Into a
  fan-in a wire runs down, across halfway, down; out of a switch across at its level, then down.
- `look.svg.schematic_svg` places its labels with `place_labels`.
### Added
- `place_labels(layout, sizes, center, radius, bounds)`: every label beside, under or over its
  component where it covers least of the wires, components and other labels (by area), the most
  constrained first, then a few rounds of reconsidering; the renderer gives its scale and the sizes
  it measured.

## [1.9.0]
### Added
- `ProvenState.assumed` / `ProvenState.assuming()`: the positions of a what if, taken as given over
  telemetry, derivation and declarations (`Resolved.origin == "assumed"`) — the only way to put a
  derived axis (the sky, the dome) somewhere; `None` assumes an axis unknown.
- `published_state(selectors)`: what the optics service publishes as `self.selectors` as solver
  input — `sees`/`check` over it answer as the service does, and a what if starts from it.
- `OpticalGraph.state_axes`: every state axis with its vocabulary.
- `look` tokens `status`: the chip of a function's verdict and of the running switch (fill, ink) and
  the field it sits on, in both themes.
### Fixed
- `proven_state(..., overrides)` assumes the overrides, so `sky=day` is no longer silently ignored.
- A source that gives nothing is "off" in an "impossible" reason ("lamp.thar is not available now:
  ThAr lamp is off", was "gives dark").
- An "impossible" verdict names the unknown axes of an undefined terminal ("calibration lamp (no
  sensor)"), not the component they are part of ("mirror covers (unknown)" while the covers read open).

## [1.8.2]
### Changed
- "impossible" reasons say what each source gives instead of listing every light class anywhere
  on the telescope: "sky.science is not available now: sky gives sky.flat", "dark is not available
  now: ThAr lamp is not known; white lamp is not known"; a function whose every alternative is
  conditioned says "dark_strict applies only while sky.science is available".

## [1.8.1]
### Changed
- The solver's own causes of an undefined axis in plain words for operators: "no sensor" (was
  "no telemetry"), "its sensor reports no known position", "its sensor reading is out of date",
  "its sensor reports 'x', not a known position", "cannot be worked out".

## [1.8.0]
### Added
- `obcom.optics.labels`: what people call components and their aspects ("mirror covers",
  "calibration lamp", "M3"; a component sharing its kind keeps its own name); the config's
  `display.label` / `display.aspects` win.
- Undefined axes say why: `ProvenState.causes` (the reader's words, e.g. "shutter reports an
  error (ShutterStatus 4)") else the solver's own (moving, stale, no telemetry, "shutter state
  unknown" for the dome) — carried by `Resolved.cause`.
- `obcom.optics.telemetry`: Alpaca code names and `cover_cause` / `calibrator_cause` /
  `shutter_cause` ("shutter reports Error (ShutterState 4)"); `proven_state` fills the causes.
- `ProvenState.declared` / `Declaration`: a position a person vouches for fills an otherwise
  undefined axis (`Resolved.origin == "declared"`, `by`); a reading always wins.
- `obcom.optics.ribbon`: the path *to a detector*, from the emitter feeding it now (sky, lamp,
  flat screen) to the detector; when it sees dark or unknown the ribbon goes on past the blocker
  back to an emitter, choosing at each switch the proven input, else the target's (the selected
  function's moves), else the first authored one. Works from a `ProvenState` or from the positions
  a service publishes (`published_values`).
- `obcom.optics.schematic`: the telescope's optics on a grid for every renderer — authored
  `display: {x, y}` places, automatic layered layout elsewhere (main line straight, branches
  below, lamps beside their mirror), orthogonal wires; `Wire.taken`, `Schematic.light`.
- `obcom.optics.look`: one glyph sprite (`glyphs.svg`), the themes (`tokens.json`: `toi` —
  observatory white in TOI's palette, `holo` — owies), `glyph_of` (`display.glyph` or the kind's);
  `obcom.optics.look.svg.schematic_svg` draws the schematic as SVG (config previews, owies reference).
- `obcom.optics.emerging`: what leaves any component towards a downstream, over any row source
  (`ValuesOf`) — the walk of `sees`, which now shares it.
### Changed
- Verdict reasons are written for people, with labels and causes: "sky.science needs M3 andor,
  it is beso — held by beso-run"; "unknown: dome (shutter reports an error (ShutterStatus 4))".
- Requires datamodels >= 0.6.0 (`DisplayHint.label`, `DisplayHint.aspects`, `DisplayHint.glyph`).

## [1.7.1]
### Added
- `obcom.optics.Hold(position=None)`: a selector held at an unknown position (undefined
  readback) — no route that moves it is feasible, so `check` falls back to another route
  (broken dome shutter: dark via M3 instead of the dome) or reports `impossible` naming the hold.

## [1.7.0]
### Added
- `obcom.optics`: sources may declare `light:` (a state within the kind's family, e.g.
  `lamp.thar`); goals match by family (`lamp` accepts any lamp, `lamp.thar` that one).
- `obcom.optics.telemetry`: Alpaca readbacks (cover, calibrator, shutter, M3 port) →
  `ProvenState`, shared by the CLI and TIC's optics adapter.
### Changed
- Requires datamodels >= 0.4.0 (`OpticalComponentSpec.light`, `light_satisfies`).

## [1.6.0]
### Added
- `obcom.optics`: the Optical Path Model v4 reference solver — `parse_graph`
  (edge grammar, kind registry, load-time validation with reasons), `sees`,
  `check`, `resolve`, `compile_telescope`/`compile_observatory` (route table +
  conflict map, generated then verified) and the conformance-vector suite
  (`optics-conformance`). Pure functions over (graph, proven state), no I/O.
### Changed
- Python floor is 3.11 (was 3.10).
- New dependency: `datamodels` (optics vocabularies and result shapes).

## [1.5.0]
### Changed
- ErrorPolicy.DISPLAY stops on CRITICAL like every other preset (NOTIFY-and-heal removed).
### Added
- CycleQuery.stop_reason (the server error that stopped the query) and one ERROR log line per stop.
- Rollout constraint: deploy with ocabox-server >= 2.8.0 on TIC first (catch-all CRITICALs reclassified there).

## [1.4.0]
### Changed
- Default truth bound (T2) is `ValueRequest.default_max_age(t1) = max(2*T1,
  DEFAULT_MAX_AGE_FLOOR=5 s)`, computed in one place; `ValueRequest.
  effective_max_age` gives the bound in force. An explicit T2 is unchanged
- CQ: a timeout wake-up caused by local event-loop starvation no longer
  produces any verdict (the grace timer and reason code 4010 are gone) —
  the query polls again immediately and the outcome of that poll decides

## [1.3.4]
### Added
- `ConditionalCycleQuery`: exposed the T2 health clock publicly via
  `last_healthy_contact_age` (property) and `is_contact_fresh(max_age)`
  (method), read-only accessors over the existing `_last_contact_ts`. No
  behavior change — consumers building liveness watchdogs should use these
  instead of timing deliveries, since conditional deliveries are
  once-per-change and a stationary value is silent while perfectly healthy
  (#20)
- `ConditionalCycleQuery.truth_bound`: the contact silence a subscription can
  vouch for — `max(long-poll window, declared T2)`; `is_contact_fresh()` with
  no argument judges against it. For consumers judging a subscription-fed
  value (ocabox sync property reads) (#20)

## [1.3.3]
### Fixed
- CQ: replaced the lossy `set()`/`sleep(0)`/`clear()` event pulse with a
  delivery sequence number, so a value change or a stale-None is no longer
  silently dropped when the callback runner is busy inside a slow user
  callback, or has not parked in `wait()` yet (extreme first-poll
  starvation). `get_response()` keeps its contract; delivery is now
  loss-free for a consumer that is merely busy, not absent, and broadcast
  to any number of concurrent `get_response()` callers via a
  resolved-and-replaced delivery future rather than a shared "claimed"
  sequence; each waiter shields its own await on that shared future so one
  consumer's cancellation (a timeout, a closed widget) can no longer
  spuriously cancel every other consumer parked on the same delivery (#18)

## [1.3.2]
### Fixed
- `ConditionalCycleQuery`: starvation wake-ups now defer stale-None synthesis
  by one transport window and use the next poll as evidence, avoiding red-blink
  flapping under GUI/event-loop stalls while preserving punctual stale delivery
  when silence persists (`reason=4010`).

## [1.3.1]
### Fixed
- CQ: 4004 renewals feed the T2 contact clock only when they arrive after a
  real long-poll wait; an instant-renewal livelock now delivers the punctual
  rich stale-None (reason 4004) instead of starving the consumer (#13)

## [1.3.0]
### Added
- **Staleness Contract, phase 1**: `value_policy` axis (`RAISE`/`NONE`/`LAST_GOOD`)
  in `ErrorPolicy`, `DISPLAY` preset, cyclic-query `request_data` fragment,
  `ValueRequest.time_of_data_max_age` (T2 bound, default `2*tolerance`),
  client-side rich `Value(None)` synthesis when silence exceeds T2;
  `max_missed_msg` now follows the transport identity (stops only for
  NORMAL=STOP presets). Inert unless a client opts in. (ocabox-common#8, #9)

## [1.2.2]
### Fixed
- `TreeStructureError.__init__` accepted `severity` but never forwarded it to
  `super().__init__()`, silently demoting every `TreeStructureError` to
  `NORMAL`. Connectors raising `3002` ("method not implemented") as
  `CRITICAL` — a permanent condition that should stop client subscriptions —
  actually produced `NORMAL`, so `ErrorPolicy.SERVICE` clients retried forever
  against endpoints that can never succeed. Found 2026-06-20, regression test
  added (`test_coded_error.py`) covering all coded-error classes.

## [1.2.1]
### Added
- `TreeOtherError` code **4009 "Device reported an error"**. Distinguishes a
  fault *reported by an instrument/driver* (the server worked and faithfully
  relayed a device error) from a TIC-internal value-build failure (`2002`).
  Connectors should raise `TreeOtherError(code=4009, message=<device msg>,
  severity=NORMAL, device_errno=<numeric driver code, optional>)`. The
  `device_errno` rides in `kwargs` and round-trips through
  `ResponseError.from_coded_error` to the client unchanged, so clients no
  longer have to parse the message string or read server logs to learn what
  the device said.

## [1.2.0]
### Fixed
- `_ExponentialBackoff.delay()`: guard `OverflowError` from `float ** int` at
  ~1024 consecutive retries — now returns `ceiling` immediately on overflow
  instead of raising and escaping to the caller.
- `ConditionalCycleQuery` / `PeriodicCycleQuery` catch-all `except Exception`:
  under `ErrorPolicy.SERVICE` (and any policy whose `normal.action != STOP`)
  the handler now sleeps `_CATCH_ALL_RETRY_DELAY` (60 s) and continues the
  subscription loop instead of breaking permanently. `INTERACTIVE` and
  `FAIL_FAST` retain the existing stop-on-unknown behaviour.
- Catch-all log messages throttled via `_catch_all_log_state` (respects the
  `LogPolicy` from `error_policy.normal.log`) to prevent log flooding under
  SERVICE on persistent unexpected exceptions (~1.4 k/day → 3 loud + 1/hour).

## [1.1.1]
### Fixed
- `ConditionalCycleQuery._execute_callbacks`: silent callback-runner death
  on non-`TypeError` exceptions. The handler now catches `Exception` (not
  just `TypeError`) and logs with `logger.exception` for full traceback.
  `CancelledError` continues to propagate so task cancellation works.
  Pre-fix symptom: a buggy callback raising e.g. `IndexError` killed the
  callback runner task without logging — the producer kept polling, no
  `SUBSCRIPTION STOPPED` log appeared, and downstream consumers saw stale
  data forever (observed as `tic.status.jk15.camera.camerastate` stuck on
  NATS for 7+ hours while server-side direct GET returned the correct value).

## [1.1.0]
### Added
- `obcom.comunication.error_policy`: per-severity `ErrorPolicy` for cycle queries.
  Each subscription declares — once at subscribe time — what action
  (`RETRY` / `NOTIFY` / `STOP`), backoff (`Backoff.immediate/fixed/exponential/staged`),
  retry budget (`Budget`) and log throttle (`LogPolicy`) applies to
  `TEMPORARY` / `NORMAL` / `CRITICAL` errors. Three presets cover the
  common cases:
  - `ErrorPolicy.INTERACTIVE` (default) — GUI-friendly, `NORMAL` stops
    the subscription so the human can react.
  - `ErrorPolicy.SERVICE` — long-running daemons; `NORMAL` is auto-retried
    with a staged backoff (2s × 3 → 10s × 6 → 60s forever) and per-attempt
    warnings throttled to 3 loud + 1/hour.
  - `ErrorPolicy.FAIL_FAST` — short-lived tools / tests.
  Plumbed through `ConditionalCycleQuery`, `BaseClientAPI.subscribe`,
  and `BaseClientAPI.subscribe_with_callback`.
### Deprecated
- `ignore_errors=True` on cycle-query constructors and `subscribe*`
  helpers. Pass `error_policy=ErrorPolicy.SERVICE` (or another preset)
  instead. Still honoured for one release with a `DeprecationWarning`.

## [1.0.3]
### Added
- `TreeOtherError` code 4008: device busy with another operation in progress (e.g. camera mid-acquisition).

## [1.0.1]
### Changed
- Configuration file system `confuse` removed. The current configuration system will be a server-only feature 
and will be still in use in `ocabox-server`. Client (new `ocabox`) will get a new system.

## [1.0.0]
### Added
- Project core files added and initialized.
- The first version of the project after separating the server part from the [ocabox](https://github.com/araucaria-project/ocabox) project. 
The change history before the split can be found in the ocabox project change history to version 1.0.17 .



[Unreleased]: https://github.com/araucaria-project/ocabox-common

[1.0.0]: https://github.com/araucaria-project/ocabox-common
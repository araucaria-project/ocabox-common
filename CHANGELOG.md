# Changelog
All notable changes to this project will be documented in this file.

## [Unreleased]


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
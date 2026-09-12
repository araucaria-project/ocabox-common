# ocabox-common
Shared library of the ocabox family: ZMQ communication (`obcom.comunication`), the value/address
model and the Staleness Contract (`obcom.data_colection`), and the Optical Path Model reference
solver (`obcom.optics`).

## obcom.optics — Optical Path Model reference solver

One solver, many callers (TIC, obsplan, CLI, services; the browser visualizer replays its
conformance vectors). Pure functions over `(graph, proven state)`, no I/O:

```python
from obcom.optics import parse_graph, ProvenState, sees, check, resolve, compile_telescope

graph = parse_graph(components)                       # a telescope's `components:` block; GraphInvalid lists every reason
state = ProvenState.build({"dome": "open", "tertiary": "andor", "covercalibrator": "open", "covercalibrator.calibrator": "off"}, sun_alt_deg=-30)
sees(graph, state, "camera")                          # frozenset({SeesRecord(light_class='sky.science', terminal='sky', via=('dome', 'covercalibrator', 'tertiary', 'derotator', 'pickoff', 'filterwheel'))})
check(graph, state, "camera", "dark")                 # Active | Settable(moves=...) | Collision | Impossible
resolve(graph, state, "camera", "object")             # {selector: symbol} or None
compile_telescope(graph)                              # route table + conflict map, generated then verified
```

Vocabularies and result shapes come from the `datamodels` package (`datamodels.optics`); the
kind → archetype registry (`DEFAULT_REGISTRY`) is injectable. Proven state is keyed by selector,
or `selector.aspect` for a multi-axis device (`covercalibrator.calibrator`). An axis without
usable telemetry — unreported, stale, moving or unmapped — is `undefined` and validates nothing;
only the dome is derived (shutter and pointing) when no telemetry names its position.

```bash
poetry run python -m unittest discover -s test/optics -t .   # solver tests
poetry run optics-conformance optics_conformance.json         # golden vectors for other traversals
```

Spec: knowledge-base `Architecture/Optical Path Model.md`; epic araucaria-project/ocabox-server#27.

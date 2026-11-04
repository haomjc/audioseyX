# Python and command-line interfaces

The current API is experimental. Inspect version-specific documentation before building an integration.

## Data model

`audioseyx.model.Measurement` stores a channel, position, name, sample rate and either an original IR or frequency response. Optional phase, original start time, timing-reference description and metadata are retained. Invalid numerical vectors are rejected.

`audioseyx.model.Project` groups measurements, origin, model, tier, metadata and warnings. Origin is retained across project save/import. It is not a cryptographic certification of measurement provenance.

```python
import numpy as np
from audioseyx.model import Measurement, Project
from audioseyx.analysis import analyze_measurement
from audioseyx.dsp import design_project

ir = np.zeros(8192)
ir[240] = 1.0
measurement = Measurement("FL", "p0", "Synthetic delay", 48000, ir,
                          start_time_s=-0.001)
project = Project("Example", [measurement], origin="synthetic")
analysis = analyze_measurement(measurement)
design = design_project(project, "XT32", {
    "bass_db": 0,
    "tilt_db_oct": 0,
    "phase_mode": "minimum",
})
```

The pure-delay example has a peak time of 4 ms after accounting for the original start time. It must not be used as real receiver calibration.

## Useful entry points

| Module | Entry point | Purpose |
|---|---|---|
| `importers` | `import_file(path)` | Import supported measurement formats |
| `archive` | `save_archive(project, path)` / `load_archive(path)` | Compressed project interchange |
| `analysis` | `analyze_measurement(measurement)` | Plot-ready analysis with quality notices |
| `analysis` | `room_modes(length, width, height)` | Rectangular-room geometric prediction |
| `dsp` | `design_project(project, tier, settings)` | Offline filter design |
| `exporter` | `export_bundle(project, design, directory)` | Filter WAVs, measurements and reports |
| `benchmarks` | `run_benchmarks()` | Fixed-ground-truth numerical cases |
| `joint_benchmarks` | `run_joint_benchmarks(iterations=60)` | Full synthetic 7.1.4 joint-FIR validation |
| `native_joint` | `prepare_native_problem(project, tier, settings)` | Complete complex native-routing observations |
| `joint_fir` | `JointFIRProblem(spaces, observations, nfft)` | Full-tap/custom-routing solver with sparse native synthesis |
| `measurement_quality` | `frequency_dependent_response(measurement, frequencies, cycles)` | Per-frequency Gaussian windowing |
| `realization` | `load_realization(path, tier, model)` | Validate explicitly supplied native synthesis data |
| `compat` | `compatibility_status()` | Local optional-resource availability |

## Local web API

The web API is an implementation detail of the desktop workflow. It binds to loopback. Write requests require the current session token and an accepted origin. Receiver-changing operations additionally require review within the application. Do not embed the token in public examples or configure an external service around this local API.

## CLI

```text
audioseyx [--port PORT] [--no-browser] [--data-dir DIRECTORY]
audioseyx --version
audioseyx --self-test [--report FILE]
audioseyx --joint-benchmark [--benchmark-iterations 60] [--report FILE]
audioseyx --compat-status [--compat-dir DIRECTORY]
```

Self-test, joint-benchmark and compatibility-status commands do not connect to a receiver or play sound. The joint benchmark is substantially more expensive than the small self-test and reports convergence separately from feasible-filter assertions.

`tools/readthedocs.py` is a repository maintenance script for the official Read the Docs API, separate from the local desktop web API. See [release maintenance](releasing.md).

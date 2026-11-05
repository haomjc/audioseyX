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
| `listening_area` | `resolve_listening_area(project, plan, validation_positions)` | Explicit seat priority and missing-data resolution |
| `polarity` | `analyze_polarity(project, same_model_groups)` | Reusable same-model relative polarity diagnostics |
| `realization` | `load_realization(path, tier, model)` | Validate explicitly supplied native synthesis data |
| `compat` | `compatibility_status()` | Local optional-resource availability |
| `coefficient_transport` | `audit_coefficient_streams(packets, manifest, fixed=False)` | Reconstruct every reviewed native channel/curve/rate stream offline |
| `coefficient_transport` | `quantization_report(coefficients, fixed=False)` | Serialized coefficient precision, counts and value hash |
| `transfer` | `audit_transfer_plan(plan)` | Recheck the complete reviewed transaction before any connection |
| `receiver_calibration` | `calibrate_receiver(project, design, settings)` | Modeled native distance/level/crossover/polarity proposal |
| `auditory_balance` | `balance_groups(grid, groups, levels, positions)` | ERB-band robust native-grid relative level matching |
| `clock_sync` | `estimate_clock(recording, pilot, rate, times)` | Known-pilot affine drift from original recordings |
| `clock_sync` | `correct_recording(recording, analysis)` | Separate continuous-position raw recording correction |
| `temporal` | `temporal_profile(measurement)` | Original-IR temporal energy and quality-gated descriptive slopes |
| `temporal` | `compare_temporal_records(before, after)` | Every retained repeat in a common observed window |
| `temporal` | `predict_filter_temporal(project, design)` | Complete EQ-stage linear convolution with a frozen original reference |
| `feature_coverage` | `capability_assessment(project=None)` | Engineering checklist and current project evidence; no performance percentage |
| `calibration_curves` | `curve_designs(design)` | Complete Flat/Reference designs with common geometry validation |
| `receiver_calibration` | `evaluate_shared_curve_controls(project, design, primary)` | Alternate-target evaluation with primary-selected controls fixed |

## Local web API

The web API is an implementation detail of the desktop workflow. It binds to loopback. Write requests require the current session token and an accepted origin. Receiver-changing operations additionally require review within the application. Do not embed the token in public examples or configure an external service around this local API.

`GET /api/capabilities` returns the current development checklist without contacting the receiver. `POST /api/capabilities/export` creates a private downloadable assessment; `POST /api/compare` creates a separate verification JSON report with original-repeat temporal diagnostics. Download paths are `/download-capabilities` and `/download-verification`; changing the project invalidates both. See [temporal verification](temporal-verification.md) for reference, playback-chain, memory and support requirements.

For [independent calibration curves](calibration-curves.md), `POST /api/target-curve` accepts `curve_name="Flat"` (default) or `"Reference"`. Imports persist independently and invalidate the current bundle. `curve_profiles` in `/api/design` requests two complete designs. `/api/transfer/prepare` additionally accepts `initial_curve="Flat"` or `"Reference"`, committed to the reviewed byte transaction. No design or target import contacts a receiver.

## CLI

```text
audioseyx [--port PORT] [--no-browser] [--data-dir DIRECTORY]
audioseyx --version
audioseyx --self-test [--report FILE]
audioseyx --joint-benchmark [--benchmark-iterations 60] [--report FILE]
audioseyx --listening-area-benchmark [--benchmark-iterations 60] [--report FILE]
audioseyx --native-workflow-benchmark [--report FILE]
audioseyx --compat-status [--compat-dir DIRECTORY]
```

Self-test, joint-benchmark and compatibility-status commands do not connect to a receiver or play sound. The joint benchmark is substantially more expensive than the small self-test and reports convergence separately from feasible-filter assertions.

`tools/readthedocs.py` is a repository maintenance script for the official Read the Docs API, separate from the local desktop web API. See [release maintenance](releasing.md).

# Validation and support status

## Separate kinds of evidence

| Evidence | What it establishes | What it does not establish |
|---|---|---|
| Unit and workflow tests | Behavior for the tested files, numerical cases and fake receivers | Physical receiver support |
| Distribution audit | Expected assets and notices, excluded private data | Acoustic accuracy |
| Offline self-test | Identity design, available encoding checks and known frame construction | Successful hardware reconstruction |
| Numerical benchmark | Fixed synthetic truth and holdout behavior | Commercial-product equivalence or real-room improvement |
| Transfer acknowledgement | A device acknowledged observed commands | Correct magnitude/phase at every sample rate or improved sound |
| Normal-playback verification | Measured response through the actual playback chain | Generalization to every seat or firmware version |

## Numerical cases

The included benchmark uses a pure delay, known exponential decay, a truncated slow-decay recording, a repeatable deep null and a two-resonance synthetic system. The design uses three positions; the evaluated holdout strength is excluded from design. Each tier is evaluated using its actual truncated taps.

The benchmark has no microphone, ADC, clock drift, amplifier nonlinearities or physical room. It is a reproducible algorithm check rather than a listening or hardware test.

## Current receiver status

Read-only native information, exact-IP HEOS model identification and current speaker-setting reads have been checked on an X3700H. There is no fully validated acoustic correction path in this public prerelease. X3700H Float capture transport, including individual subwoofer addressing, has been checked. Native transfer remains experimental. Fixed capture, native reconstruction at every sample-rate slot, microphone compensation, physical multi-subwoofer validation and incremental composition with stock correction remain open work. Offline joint optimization supports explicitly confirmed independent subwoofer outputs; that computational support is not physical receiver certification.

Hardware reports should state the exact model, firmware, tier, layout, microphone, timing method and operation tested. Discovery, capture, transfer acknowledgement and normal-playback acoustic verification are different milestones.

## Reproducible local checks

```console
python -m unittest discover -s tests -v
audioseyx --self-test --report self-test.json
audioseyx --joint-benchmark --report joint-benchmark.json
audioseyx --listening-area-benchmark --report three-seat-benchmark.json
python -m sphinx -W --keep-going -b html docs docs/_build/html
python -m build
python tools/audit_distribution.py dist
python -m twine check --strict dist/*.whl dist/*.tar.gz
```

Public tests do not need legacy numerical tables. Simulated receiver tests use artificial addresses. No test connects to a physical AVR.

The native-workflow benchmark in 0.5.0a7 also exercises complete EQ-stage temporal convolution for all 96 synthetic original records, including holdouts. It checks retention and fixed temporal references/measured support. The packaged Windows build runs this path, so the installed temporal module is exercised as well as unit-tested. Short synthetic recordings correctly leave unsupported late-energy cutoffs unavailable.

Version 0.5.0a8 adds a complete independent Reference target with another 195,586 XT32 FIR taps and a fixed evaluation of the Flat-selected controls. Offline transport self-tests now serialize distinct Flat/Reference data across every tier, numeric format and rate slot. These remain computational and byte-stream checks, not hardware certification.

## Three-seat numerical fixture

`--listening-area-benchmark` uses abstract synthetic responses for three measured training seats and a separate held-out point. It checks seat priorities, full XT32 reference dimensionality, returned-filter gain feasibility and exact smooth-risk steps. Passing these assertions does not establish strict numerical convergence or performance in a real row. The recorded report keeps the convergence result explicitly, even when it is false.

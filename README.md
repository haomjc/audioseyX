# audioseyX

**A local acoustic measurement, analysis and filter-design workbench.**

audioseyX brings measurement files, REW integration, room analysis and Audyssey-aware filter design into one local interface. It preserves individual microphone positions and original response timing, evaluates the actual truncated filters, and keeps numerical predictions separate from hardware and acoustic verification.

**Status: 0.5.0a4, an experimental public preview.** PyPI publishing and the Read the Docs site are being configured; a local build does not establish that either service is live. Read-only information, speaker settings and Float capture transport have been checked on an X3700H. Microphone compensation, normal-playback reconstruction and transfer remain unverified. This project is independent of Audyssey, Denon, Marantz and Dirac.

## What works offline

- Import ADY, MQX, MDAT frequency data, mono impulse-response WAV and `.awb` projects.
- Preserve measurement positions, repeats, sample rates and original timing.
- Apply explicit seat priorities, keep repeated scans from multiplying spatial importance, and reserve measured positions for independent validation.
- Work with variable channel counts, zero to four subwoofer outputs and custom height layouts; report missing output/position coverage. Examples range from 2.0 to 9.4.6 without certifying a receiver's capacity.
- Read compatible receiver speaker settings and load reviewed per-channel crossovers, bass mode and LFE low-pass into the offline model. Select physical capture outputs explicitly.
- Inspect frequency response, group delay, impulse, ETC, Schroeder decay and a low-frequency STFT spectrogram.
- Compare confirmed same-model speakers for repeatable relative polarity, retaining inconclusive results and native receiver hints without automatically reversing a speaker.
- Report EDT/T20/T30 only when the current quality checks pass; do not invent time-domain measurements from magnitude-only files.
- Design minimum-phase filters within separate MultEQ, XT and XT32 budgets, with constrained boost and measured-band coverage.
- Jointly optimize complex main/subwoofer and LFE responses across positions and repeats, using every selected FIR parameter. Distinguish expanded references from actual native coefficients supplied through a synthesis model.
- Analyze the unchanged joint target with gain-limit and shared-filter lower bounds, keeping conditional model gaps separate from acoustic error and hardware verification.
- Apply per-frequency Gaussian windowing to correction goals, inspect repeat consistency, hold positions out for validation, and fully re-solve explicit crossover candidates.
- Export per-channel filter WAVs, source measurements and reports, even without native compatibility data.
- Run deterministic numerical benchmarks with a synthetic holdout position excluded from design.
- Save compressed projects with array checksums and download results with segment retries and a whole-file hash check.

## Installation

Requires Python 3.11 or newer. Until an actual PyPI release exists, install this checkout:

```console
python -m pip install .
audioseyx
```

For development:

```console
python -m pip install -e ".[dev,docs]"
python -m unittest discover -s tests -v
python -m sphinx -W --keep-going -b html docs docs/_build/html
```

After publication, the prerelease installation command will be:

```console
python -m pip install --pre audioseyX
```

The distribution name is `audioseyX`; the import module and command are `audioseyx`. The interface is served only on `127.0.0.1`. Python installations store user data in the operating system's per-user application-data directory. An explicit location is also supported:

```console
audioseyx --data-dir ./my-audio-projects
audioseyx --version
audioseyx --self-test --report self-test.json
audioseyx --joint-benchmark --report joint-benchmark.json
```

## Receiver integration

Experimental stock-microphone capture targets the observed Float receiver protocol. The microphone connects to the receiver; the computer reads the responses over the LAN. The checked X3700H capture path remains experimental; Fixed-device capture is pending and imported files remain usable.

X3700H Float capture transport, including individual subwoofer commands, has now been checked. **Version 0.5.0a1 used configured SWMIX names as measurement commands; affected subwoofer records must be treated as unverified.** Version 0.5.0a2 separates status and measurement identifiers and checks the returned speaker role. Original data can be retained for audit, with the affected subwoofer recordings excluded or reacquired.

**The public source and packages exclude legacy native coefficient tables and channel-address data whose redistribution permission has not been established.** General filter design and WAV export work without them. Native encoding is explicitly marked unavailable, and receiver transfer is not prepared from guessed data. See [native integration](docs/native-integration.md) for optional local-data configuration and current limitations.

The private 0.3 research prototype is separate from this public source tree. Its local compatibility vectors are not evidence of a license to redistribute them and are not included here.

This is not an implementation of Dirac ART's arbitrary cross-channel support matrix. It does not modify receiver firmware or add routing paths to the existing DSP.

See the [OCA replacement status](docs/oca-comparison.md) for implemented components and remaining native-workflow gaps.

The new joint mode is export-only. A feasible improving filter can be returned before numerical convergence, and expanded XT32 taps do not establish writable native realizability. See [joint design](docs/joint-design.md) for the model, full-layout benchmark, supplied native synthesis format and limits. No same-data OCA/GSonic comparison has been established.

## Documentation and contribution

The English documentation source is in `docs/`, with Sphinx, MyST Markdown and the Furo theme. `.readthedocs.yaml` is prepared for Read the Docs. GitHub Actions check tests, package contents and documentation; manual workflows prepare PyPI publishing and Windows artifacts.

Read [the documentation](docs/index.md), [contribution guide](CONTRIBUTING.md), [release guide](docs/releasing.md), and [third-party notices](THIRD_PARTY_NOTICES.md). Do not attach private calibration files or local credentials to public issues.

## License

New project code is MIT licensed, copyright 2026 haomjc. The adapted data-only Java stream reader and synthetic format fixtures retain their upstream GLWTS license. Dependency licenses remain separate. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

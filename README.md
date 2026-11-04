# audioseyX

**A local acoustic measurement, analysis and filter-design workbench.**

audioseyX brings measurement files, REW integration, room analysis and Audyssey-aware filter design into one local interface. It preserves individual microphone positions and original response timing, evaluates the actual truncated filters, and keeps numerical predictions separate from hardware and acoustic verification.

**Status: 0.4.0a1, an experimental public preview.** PyPI publishing and the Read the Docs site are being configured; a local build does not establish that either service is live. No physical receiver has been validated. This project is independent of Audyssey, Denon, Marantz and Dirac.

## What works offline

- Import ADY, MQX, MDAT frequency data, mono impulse-response WAV and `.awb` projects.
- Preserve measurement positions, repeats, sample rates and original timing.
- Inspect frequency response, group delay, impulse, ETC, Schroeder decay and a low-frequency STFT spectrogram.
- Report EDT/T20/T30 only when the current quality checks pass; do not invent time-domain measurements from magnitude-only files.
- Design minimum-phase filters within separate MultEQ, XT and XT32 budgets, with constrained boost and measured-band coverage.
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
```

## Receiver integration

Experimental stock-microphone capture targets the observed Float receiver protocol. The microphone connects to the receiver; the computer reads the responses over the LAN. The driver has not yet been validated on an X3700H or other physical receiver. Fixed-device capture is pending; imported files remain usable.

**The public source and packages exclude legacy native coefficient tables and channel-address data whose redistribution permission has not been established.** General filter design and WAV export work without them. Native encoding is explicitly marked unavailable, and receiver transfer is not prepared from guessed data. See [native integration](docs/native-integration.md) for optional local-data configuration and current limitations.

The private 0.3 research prototype is separate from this public source tree. Its local compatibility vectors are not evidence of a license to redistribute them and are not included here.

This is not an implementation of Dirac ART's arbitrary cross-channel support matrix. It does not modify receiver firmware or add routing paths to the existing DSP.

## Documentation and contribution

The English documentation source is in `docs/`, with Sphinx, MyST Markdown and the Furo theme. `.readthedocs.yaml` is prepared for Read the Docs. GitHub Actions check tests, package contents and documentation; manual workflows prepare PyPI publishing and Windows artifacts.

Read [the documentation](docs/index.md), [contribution guide](CONTRIBUTING.md), [release guide](docs/releasing.md), and [third-party notices](THIRD_PARTY_NOTICES.md). Do not attach private calibration files or local credentials to public issues.

## License

New project code is MIT licensed, copyright 2026 haomjc. The adapted data-only Java stream reader and synthetic format fixtures retain their upstream GLWTS license. Dependency licenses remain separate. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

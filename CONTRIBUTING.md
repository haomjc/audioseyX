# Contributing to audioseyX

Use English for code, public issues, pull requests and primary documentation. Keep changes focused and describe the user-visible behavior and the evidence behind it.

## Development

```console
python -m venv .venv
python -m pip install -e ".[dev,docs]"
python -m unittest discover -s tests -v
python -m sphinx -W --keep-going -b html docs docs/_build/html
python -m build
python tools/audit_distribution.py dist
python -m twine check --strict dist/*
```

Activate the virtual environment using the command appropriate to your shell before installing. The public test suite uses synthetic fixtures and simulated receivers; it does not initiate physical capture or transfer.

## Measurement changes

- Preserve raw samples, timing and data origin. Do not silently peak-align responses.
- Do not invent phase or IR data from magnitude-only measurements.
- Keep predicted results, simulated protocol checks and real hardware validation distinct.
- Document sample rates, units, normalization, common frequency coverage and uncertainty.
- Never use a synthetic fixture to bypass receiver-transfer checks.

## Tests and distribution

Add behavioral tests for meaningful new behavior. Native transfer tests must use explicit fake receiver addresses and mocked sockets. Legacy numerical resources and private calibration files are excluded from public source and packages; do not add them in a pull request.

The distribution audit rejects excluded compatibility files and checks the required web assets and license notices. The source archive is intended to rebuild the wheel without access to the original local prototype.

## Hardware reports

Report the receiver model, firmware version, Audyssey tier, layout and whether the test covered discovery, raw capture, transfer acknowledgement, normal-playback verification or actual acoustic results. Explain how the microphone and timing reference were used. Hardware verification is model- and firmware-specific.

## Release changes

Update `src/audioseyx/__init__.py` and `CHANGELOG.md`. Follow `docs/releasing.md`. A successful local build is not proof that a package or website is online.

# Changelog

## 0.4.0a1 — public-release candidate

- Adopt the audioseyX name and an English interface and documentation.
- Add an installable Python distribution and `audioseyx` / `python -m audioseyx` entry points.
- Keep installed-package user data outside `site-packages`.
- Exclude legacy native tables and address maps from public source and distribution artifacts.
- Preserve general filter WAV export when optional native compatibility data are absent.
- Distinguish numerical design self-tests from unexecuted native encoding checks.
- Add Sphinx/MyST documentation, Read the Docs configuration, issue templates and release workflows.

The 0.3 offline analysis and DSP core is retained. Actual receiver capture, native reconstruction, transfer and acoustic performance are still unverified.

## 0.3.0 — local research prototype

Added impulse/ETC, group-delay and decay analysis; low-frequency STFT; rectangular-room modes; deep-null boost protection; common measured-band constraints; compressed projects; and verified resumable downloads. This prototype includes private local compatibility research assets and is not the public distribution.

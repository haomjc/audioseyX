# Changelog

## 0.5.0a1 — full complex joint design preview

- Add variable layout summaries, completeness checks, examples from mono/stereo through 9.4.6, adjustable subwoofer counts and custom channel lists.
- Verify read-only X3700H native information, exact-IP HEOS identity and speaker settings; support receiver web URLs and reviewable per-channel crossover defaults.
- Preserve raw SWMIX identifiers, explicitly select capture outputs and record configured outputs omitted from physical wiring.
- Add full-tap, complex multi-output FIR optimization with shared main/subwoofer and LFE routing, complete position/repeat observations, ADMM/CG, position-risk weighting and feasible block refinements.
- Add per-frequency Gaussian windowing of correction goals, repeat consistency diagnostics and training-only references for held-out positions.
- Evaluate full original impulses directly on the solver grid, including different measurement rates; check complete-problem memory before grid allocation and retain integer-exact repeat scenario counts.
- Fully re-solve every explicit global crossover candidate without using held-out positions for selection.
- Support explicitly supplied sparse native synthesis models and optimize their native coefficient vectors directly; keep expanded XT32 reference variables separate from writable budgets.
- Show routed program-input sums, independent 0.4 comparisons, actual returned-filter gain gaps and numerical convergence independently; keep the new mixed-phase mode export-only.
- Add design cancellation, native-model archive validation and a reproducible full 7.1.4 synthetic benchmark.
- Add a Read the Docs API v3 maintenance CLI, using a locally configured token without accepting secrets on the command line.

Numerical and export workflows have no physical receiver validation. This release does not establish OCA/GSonic superiority, global optimality or ART-style arbitrary routing.

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

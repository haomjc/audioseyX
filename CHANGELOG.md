# Changelog

## 0.5.0a8 — independent native calibration targets

- Calculate complete independent Flat/Reference minimum-phase EQ targets, retain shared training/validation references, and independently persist imported targets. Add result-curve selection and separate WAV/report/native-candidate exports.
- Share the Flat-selected native distance/level/crossover/polarity proposal and forward-evaluate Reference with those controls fixed. Retain every repeat and routing model; held-out data never select either curve or the common controls.
- Reserve coefficient headroom across both curves using one scale per output, or one global scale when baseline trims are unknown. Apply one common trim compensation and preserve relative digital gain.
- Audit separate curve/slot coefficient hashes and calibration FIR associations. Bind the selected initial curve to transfer schema 4; reject stale/swapped/mislabeled data and designs from another measurement project before connecting.
- Exercise distinct curves in offline transport self-tests and both complete XT32 targets in the packaged native-workflow benchmark. Export only current generated files from reused directories.

## 0.5.0a7 — temporal evidence and capability coverage

- Retain every before/after original-IR repeat for observed-window temporal energy fractions and quality-gated descriptive EDT/T20/T30 slopes, including 20–150 Hz and octave-band analysis-filter ringing references.
- Separate gain changes, unknown timebases, shorter recordings and native calibration sweeps from evidence of improved corrected playback. Export immutable verification reports and invalidate them on project changes.
- Add opt-in complete FIR/IR linear-convolution diagnostics with a frozen original reference and original measured support; refuse guessed multirate reconstruction and insufficient memory.
- Expose a ten-row engineering capability checklist in the UI and offline API, including actual missing seats/outputs. Development counts are not Dirac performance percentages; native mixed-phase synthesis and ART support routes remain unverified/unavailable.
- Keep all filter-design coefficients and native control generation unchanged.

## 0.5.0a6 — cross-platform native workflow verification

- Keep exact held-out-invariance checks for every selected native distance, level, crossover and polarity, while comparing internal Float64 objective scores at numerical precision on Intel macOS.
- Preserve strict serialized-coefficient byte checks. Native-menu, auditory and raw-clock functionality from 0.5.0a5 is unchanged.

## 0.5.0a5 — integrated native menus and auditory/clock workflows

- Combine full-budget minimum-phase EQ with explicit native-menu distance, level, crossover and sub-filter-polarity proposals, retaining raw timing and every retained repeat.
- Evaluate every declared native distance step and linked crossover choice through exact conditional separability, with nested source/seat risk, optional routing-model ensembles and independent holdouts. The exactness claim is conditional on fixed FIRs and the proposed trim state.
- Add confirmed same-model ERB-band matching with robust Huber costs and exact zero-sum discrete trim optimization; preserve per-position priority across repeated scans.
- Separate physical sound speed from the X3700H legacy distance/delay model, preserve current main differences by default and expose explicit experimental relative-distance rescaling.
- Bind reviewed menu changes to the native byte transaction, normalize SWMIX control aliases, and quantize coefficient headroom to the tenth-dB compensation grid.
- Add known-pilot clock estimation and continuous-position correction of complete original recordings, with file-only reference generation, cancellation, preserved originals and timing maps. Deconvolved IRs are not treated as clock evidence.
- Add integrated UI, deterministic startup ordering and a full-layout synthetic native-workflow benchmark; document the boundary between independent methods and GSonic's proprietary engine.

## 0.5.0a4 — native calibration transport refactoring

- Separate observed receiver framing, native coefficient serialization and calibration planning while preserving existing protocol imports and wire conventions.
- Reconstruct and audit every output/curve/rate stream, including vector budgets, routing, sequence, checksums, Float32/Q31 quantization and complete coverage.
- Commit the reviewed plan to the target device, calibration, settings, levels, exact control frames and coefficient frames; recheck before any socket is opened.
- Require explicit ACK fields, reject ignored/duplicated design channels, and expose data coverage and coefficient precision in a separate transfer UI module.
- Retain a private transaction snapshot during execution and record Float32 precision in offline native-candidate exports.
- Extend packaged offline self-tests across all three tiers, both numeric formats and all observed rate slots without private compatibility assets.
- Document OCA component parity and the remaining settings-calibration/native-playback gaps. Retain Intel/Python 3.11 and ARM/Python 3.13 macOS test coverage using separate supported runner pools.

## 0.5.0a3 — conditional reachability analysis

- Analyze the unchanged joint target using annular main/subwoofer sum inequalities and shared-output residual duals across every training position and repeat.
- Report model-dependent objective lower bounds and remaining-gap upper bounds separately from numerical convergence, dB errors and hardware validity.
- Retain all frequency bins with bounded-memory diagnostic batches; account for different native Nyquist limits, declared gain envelopes, smooth worst-position risk and cancellation.
- Validate single-output limits, conflicting seats, multiple subs, native synthesis relaxations, arbitrary routing and independent small optimization references.
- Fingerprint and recheck the complete objective and constraint model. Add optional endpoint-preserving full-parameter refinement, disabled by default pending broader evidence of improvement.
- Retain complete joint iterates and reuse their full-vector/output-block directions in a monotonic feasible polishing pass. Preserve all parameters, targets and gain limits; report post-polish convergence separately.

## 0.5.0a2 — capture correctness and spatial optimization

- Add hierarchical seat priorities, position-balanced native magnitude statistics, explicit missing-seat guards and independent holdout evaluation without refitting or renormalization.
- Use exact smooth worst-position risk gradient/Hessian steps, exact Schur frequency preconditioning, consistent projection/feasibility bounds and residual-correction CG. Keep feasible output, numerical convergence and native hardware validity separate.
- Add offline saved-routing snapshots and a reproducible synthetic three-seat benchmark.
- Recheck download byte hashes even when a file retains its size and timestamp.
- Translate configured SWMIX aliases to individual SW measurement commands, while retaining both identifiers in provenance. Reject a main-speaker response returned for a configured subwoofer.
- Flag legacy native SWMIX recordings as unverified and prevent their use in filter design. Preserve original recordings; exclude or reacquire the affected subwoofer data.
- Add same-model relative-polarity diagnostics with repeated measurements, early-response reference sensitivity, three window lengths and small timing-reference fits. Native reverse hints and terminal wiring verification are separate findings.
- Add explicit/early-lobe FDW references to avoid centring a correction goal on a stronger late reflection.
- Export expanded joint references as WAV/report candidates rather than pretending that legacy compression establishes native mixed-phase realizability.
- Check Float capture transport on an X3700H, including individual subwoofer acquisition. Microphone compensation, normal-playback reconstruction and transfer remain unverified.

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

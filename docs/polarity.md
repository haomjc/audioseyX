# Relative polarity and reusable measurements

Use the Analysis page to compare original responses from speakers that are confirmed to be the same model. Enter groups such as `FL FR; SLA SRA; SBL SBR; TFL TFR TRL TRR`, confirm the groups and run **Check recorded responses**. Different models are not assumed to have the same driver/crossover phase.

The diagnostic finds an early significant lobe, checks reference sensitivity at three thresholds, evaluates three analysis-window lengths and retains every relevant repeat combination at each position. It fits only a small analysis timing-reference adjustment, then compares broadband normal and reversed hypotheses across supported spectral coverage. The original samples and timing are not changed. These are diagnostic heuristics, not a certified direct-path or electrical-wiring measurement.

A consistent broadband relative reversal is reported as a possible inversion of a pair. A pair alone does not establish which cabinet is wired incorrectly. Frequency-dependent differences, insufficient agreement, unstable references and missing coverage stay inconclusive. A unique centre speaker has no same-model peer unless one is explicitly supplied.

Native `Polarity` codes are retained separately. A reverse hint on a subwoofer does not by itself choose the best 0/180-degree setting for integration with the mains. The diagnostic never changes speaker polarity, receiver distances, levels or filters. Denon's [phase-error guidance](https://manuals.denon.com/AVRX3700H/NA/en/GFNFSYhqrqhiug.php) notes that some correctly connected speakers can still produce phase warnings. Power off and disconnect power before physically changing speaker connections, following the [manufacturer's wiring instructions](https://manuals.denon.com/AVRX3700H/NA/EN/DRDZSYrhoargxb.php).

## Reuse existing data

Saved ADY, MQX, original-IR WAV and AWB projects can be reused for supported analysis and new correction candidates. Re-running a different target curve or optimizer does not inherently require another sweep when the system and measurement coverage remain applicable.

Receiver distance, trim and crossover settings are useful model inputs, but are not a replacement for original per-position impulses. This project does not claim a reliable way to recover an AVR's complete previous multi-position raw measurements after calibration. A response cache with unknown time/position provenance must not silently enter a new calibration. Keep stock-app measurement files when available.

Repeated scans at one microphone location assess repeatability; they do not provide different seats or a held-out spatial validation point. For row-wide correction, measure the actual seat ear positions and additional useful nearby positions, keeping individual responses and explicit position weights.

## SWMIX capture provenance

On the checked X3700H protocol, configured `SWMIX` outputs are not individual sweep addresses. Version 0.5.0a2 uses individual `SW1`/`SW2` commands for measurement, records the original configured identifier, and verifies the returned subwoofer role. Native SWMIX records from 0.5.0a1 are blocked from filter design until excluded or reacquired. Their original arrays should remain in a separate backup rather than being overwritten or silently relabelled.

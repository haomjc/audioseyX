# EQ feedback, measurement robustness and saved runs

These offline functions extend the coupled native workflow. They do not add
receiver routing paths or establish equivalence with a commercial product.
No network receiver operation is needed to compute them from saved responses.

## Complete-response EQ and control feedback

Enable **Iterate complete minimum-phase EQ and receiver controls** in the
receiver calibration model. Complete phase/timing and comparable level/routing
references, one measured physical subwoofer, and a saved settings snapshot are
required. Independent and level-only workflows remain available separately.

The initial independent EQ design and coupled native search establish a feasible
starting point. Subsequent rounds:

1. Differentiate the current squared power-response error with respect to real
   log-amplitudes, including the position/source/model, curve and speaker-group
   smooth-risk derivatives. All Cartesian repeat pairs contribute.
2. Smooth those sensitivities in log frequency and propose a magnitude change.
   Reconstruct the complete native-length minimum-phase FIRs. Retain the
   configured correction bands, boost/cut limits and conservative spatial/deep
   null gain protections; evaluate the actual returned FIR envelope with a
   0.1 dB numerical/projection allowance. Existing seed-envelope departures
   are allowed but are not accumulated between rounds.
3. Refit shared main trims and exhaust the declared native sub-level,
   distance/polarity and linked crossover grid for that candidate. Check the
   coefficient compensation reserve and direct full-response consistency.
4. Accept only a feasible candidate with a strictly lower training objective
   at the **original frozen physical target references**. Try decreasing steps
   before stopping. Retain the last accepted result on rejected steps.

This is a bounded local block search. The proposal sensitivity does not include
the phase change introduced by minimum-phase reconstruction; the acceptance
evaluation does include it. Neither a global optimum nor stationarity is claimed.
Changing excluded-seat responses cannot change the selected filters or controls.
Their forward evaluations are refreshed without fitting or renormalizing targets.

The JSON report records all attempted steps, rejection reasons, accepted scores,
the stopping reason, frozen references and final complete-FIR fingerprints.
Each native-grid search remains conditional on its own fixed FIRs; its exact
finite-grid claim does not apply to the outer FIR search.

Python settings (opt-in, independent of static curve priorities):

```python
settings.update(
    receiver_control_engine="coupled",
    receiver_eq_refinement=True,
    receiver_refinement_rounds=3,             # 1..12
    receiver_refinement_step_db=0.5,          # 0.05..3
    receiver_refinement_smoothing_octave=1/3,
)
```

## Positive repeat confidence

`receiver_repeat_weighting="consistency"` assigns a positive probability to
every repeated record within each output/seat. The heuristic uses the
log-frequency-weighted RMS magnitude deviation from the repeat median.
With fewer than three repeats, all weights remain equal: two disagreeing
measurements cannot identify which one is accurate.

The unnormalized preference is `max(floor, 1 / (1 + (deviation / scale)**2))`,
with defaults `receiver_repeat_weight_floor=0.25` and
`receiver_repeat_scale_db=2`. Normalize separately within each output/seat.
Seat priorities remain independent of repeat counts. Confidence is computed
from raw responses, never from how closely a candidate matches the target.

Weights participate in both shared auditory-band trim selection and the exact
Cartesian second/fourth/complex-cross moments. They are reliability preferences,
not a measured SNR, cross-spectral coherence, absolute accuracy certificate or
clock correction. No original response is discarded, shifted or modified.

## Explicit gain and relative-timing uncertainty

Set nonnegative main/sub gain uncertainty radii and a relative sub timing radius:

```python
settings.update(
    receiver_repeat_weighting="consistency",
    receiver_main_uncertainty_db=0.25,
    receiver_sub_uncertainty_db=0.25,
    receiver_timing_uncertainty_ms=0.1,
)
```

Every nonzero axis contributes its negative endpoint, centre and positive
endpoint. All combinations participate (up to 27 cases), including both complete
EQ curves, routing models, positions, source channels, LFE and retained repeat
pairs. The existing nested smooth-risk criterion combines these rows. The
full native-delay table is budgeted before allocation; a memory limit stops the
calculation instead of removing cases or response bins.

These are **declared finite model scenarios**, not inferred measurement bounds
or a continuous worst-case guarantee. Higher risk priority does not change that
limit. The radii do not correct microphone compensation, unknown clock scale,
native reconstruction, amplifier/driver limits or firmware delay conversion.
Routed uncertainty scoring is unavailable for level-only proposals.

## Saved run receipts and recovery

The Results page lists successful, failed and cancelled local runs. Completed
runs retain their independently named export archives. Each atomic receipt
binds its software version, safe processing controls and FIR fingerprints to
the exact dataset/layout/saved receiver snapshot. Receipts and archives have
integrity hashes; edited receipts and changed archives are rejected.

**Restore controls** requires the same dataset and active imported targets.
It restores interface choices, clears timing/level/transfer/provisional-row
confirmations, and starts no calculation or hardware operation. Custom targets
are present in the verified design report and must match before a recipe can
be restored. A failed replacement retains the previous completed design.

Receipts are local reproducibility and recovery records. They are not backups
of the receiver's original complete EQ coefficients and cannot restore factory
calibration after a hardware write. No automatic retention deletion is performed.

Local HTTP endpoints:

| Endpoint | Function |
|---|---|
| `GET /api/design-history` | Recent run summaries for the active dataset |
| `GET /api/design-history/export?id=...` | Download an integrity-checked saved export |
| `POST /api/design-history/recipe` | Return safe controls after dataset/target/archive checks; requires the local session token |

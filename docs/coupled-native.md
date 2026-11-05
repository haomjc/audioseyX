# Coupled native bass planning

The UI's native receiver planner uses the coupled engine. It retains each complete minimum-phase EQ FIR, then chooses a common receiver state for the existing main/subwoofer bass-management paths. It does not add ART support routes, arbitrary cross-channel filters or native mixed-phase synthesis.

## Selected variables and objective

For one measured physical subwoofer, the engine searches every admissible declared **sub level × distance/delay × filter polarity** state. At each state it retains every declared linked crossover choice. Conditional group separability avoids enumerating the crossover Cartesian product without removing a choice.

With two static targets, **Balance both curves** selects controls using both Flat and Reference. Positive curve priorities and a mean/smooth-worst risk preference are explicit. **Use Flat for controls** remains available: Reference still reserves coefficient headroom and is forward-evaluated at the common state, but does not select controls.

The objective has a precise nesting: position/source/routing-model risk within each curve and linked group, then risk across curves within that group, then risk across groups. It is not the weighted average of the displayed whole-curve scores. Crossover-change penalties are added once per group; distance, polarity and sub-level penalties are added once per state.

Before the bass search, confirmed same-model main speakers receive one shared zero-sum native-grid trim solution. This exactly minimizes the weighted mean of per-curve ERB-band Huber costs. Those main trims and both FIRs remain fixed during the bass search. The result is a conditional finite-grid minimum, **not a joint global optimum of FIRs, main trims and bass controls**.

## LFE is an optimization input

LFE contributes a separate group, with a priority relative to one main program input. Its intentional low-pass response is retained in the response and target. Its objective covers the common measured band up to the declared LFE low-pass frequency; it is not flattened into a full-range channel. Declared capture/output gain and DSP order assumptions still need hardware verification.

LFE energy is independent of sub delay/polarity when no main path participates. Native ties prefer the smallest level/delay change and normal filter polarity, avoiding invented timing changes in an unobservable path.

## Complete data with bounded computation

For normalized main/sub responses `m` and `s`, sub gain `q` and delay factor `z`, squared power error is expanded as a fourth-degree gain polynomial with first/second delay harmonics. Full Cartesian-repeat second/fourth and complex cross moments reproduce the explicit average of every repeat combination. No coherent mean impulse is substituted, and repeated scans do not multiply seat priority.

The required frequency sums are evaluated at every declared native delay using [SciPy ZoomFFT](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.ZoomFFT.html), a Bluestein transform. All physical frequency bins contribute. Storing the finite native states instead of unqueried continuous-delay coefficients reduces memory and repeated arithmetic. It does not increase measurement resolution, shorten a FIR or discard a position. Floating-point error remains and is checked separately.

Curve/model/row dimensions, complete delay storage and state-batch work space are checked before allocation. Insufficient memory stops the calculation rather than dropping curves, models, samples or scenarios. Risk index layouts are cached and gain/delay state batches are vectorized.

## Gain reservation and physical limits

Planning and native preparation use the same tenth-dB coefficient-reserve rule. Both output curves contribute to the worst coefficient peak. Native sub-level candidates leave room for the common compensation; main-level matching also respects that reserve.

With optional encoder resources available, both actual native codec peaks are included. Otherwise the FIR peak supplies a necessary lower bound only, explicitly marked incomplete. Preparation still validates actual encoded data and final trims. The coefficient reserve is not a certificate of driver excursion, acoustic output, amplifier headroom or clipping.

Distance conversion remains a declared model distinct from physical sound speed. Main differences default to Preserve. Missing seats, incomplete configured outputs, unverified sweep addresses and unknown timing/gain context are not repaired by this optimizer.

## Independent forward check and validation

After selecting controls, the original complete complex responses are summed directly at that state. Every retained repeat, curve and routing model participates. The directly squared physical objective must agree with the transformed power moments within the reported numerical tolerance; disagreement stops activation. An admissible baseline is also checked.

Held-out positions never select FIRs, priorities, trims, gain, delay, polarity, crossovers or target references. Their original controls and selected controls are evaluated with training-derived references frozen. Reference reuses its existing joint evaluation instead of running another fit; primary-only mode directly evaluates the alternative with controls fixed.

## Software state and restart behavior

Processing uses a separate analysis context; it does not rewrite original measurement timing metadata. Cancelled or failed replacements preserve the last completed result and saved controls while invalidating any old transfer review. The UI labels the retained result as previous output.

Successful interface settings are saved for the exact original measurement data, model/tier, listening plan and snapshot. Another dataset does not receive stale defaults. Timing/level, provisional-row and transfer confirmations are reset; relative distances default to Preserve. Optional external native models must be reselected. No job, playback, connection or transfer starts on restore.

## Python interface

```python
settings = {
    "optimizer": "native_calibration",
    "receiver_control_engine": "coupled",
    "receiver_curve_control_mode": "joint",
    "receiver_curve_weights": {"Flat": 0.7, "Reference": 0.3},
    "receiver_curve_risk": 0.35,
    "receiver_sub_level_search": True,
    "receiver_sub_level_min_db": -12,
    "receiver_sub_level_max_db": 12,
    "receiver_lfe_priority": 1,
    "curve_profiles": {"Reference": {"bass_db": 5, "target_curve": None}},
    # Also supply confirmed references, an explicit capture context and
    # a complete saved receiver snapshot as described in native integration.
}
```

One static target also supports the coupled engine. Without the new engine/mode settings, Python clients retain the legacy primary-target behavior. The UI explicitly requests the new engine. All native playback and acoustic claims remain unverified.

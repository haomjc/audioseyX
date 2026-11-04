# Full complex joint FIR

The experimental joint designer solves the coupled loudspeaker/subwoofer problem using complex responses at every retained position. It has a different objective and implementation from the independent 0.4 magnitude designer.

## Inputs and native routing

Choose **Full complex joint FIR** on the Design page. Confirm common timing, consistent relative levels, and that each measurement represents an individual full-range output. A response measured through an existing crossover must not be treated as that output's uncrossed response. Missing phase, missing output/position entries, or unconfirmed references stop this mode.

Each main program input has its own main output and the shared subwoofer path. LFE has a separate row and its intentional low-pass response remains in the target. All measured speakers, including surrounds and height speakers, participate through these shared bass paths. Select the crossover model, LFE or LFE+Main mode, and any Large speakers explicitly. These models describe assumptions to validate through normal playback, rather than a certified reconstruction of an AVR's internal routing order.

The X3700H's documented crossover choices are 40, 60, 80, 90, 100, 110, 120, 150, 180, 200 and 250 Hz. Its menu supports All or Individual crossover settings. See the [Denon crossover manual](https://manuals.denon.com/AVRX3700H/NA/EN/GFNFSYitmloggd.php). A global candidate list optionally re-solves the entire joint FIR problem at every listed frequency. Selection uses training risk; held-out positions never select the crossover. This search is exhaustive over the list, rather than all possible per-speaker combinations. Apply and verify the selected receiver settings separately.

Multiple independently controllable subwoofers can be included after explicit confirmation. Multiple jacks carrying one shared filter do not establish independent output control. The designer does not add main-to-main support paths that the receiver lacks, and does not implement ART's arbitrary cross-channel control matrix.

## Full variables and measurements

No PEQ, spline-knot or short-tap reduction is used. All selected FIR parameters remain variables. Every position and every Cartesian combination of relevant main/subwoofer repeats contributes its own complex observation, with normalized weights so that duplicate recordings do not increase a position's importance. Sufficient statistics retain the complex off-diagonal cross terms; raw positions are not averaged into one synthetic response.

Original impulses use their full recorded data. Equal-rate measurements are transformed directly on the solver FFT grid; different measurement rates use a full-record zoom FFT on that grid, preserving original start time and limiting use to measured Nyquist coverage. No raw-response peak alignment or phase interpolation is introduced for these impulses. Frequency-only observations still require interpolation of their recorded data and explicit measured phase.

The implementation uses matrix-free normal equations and conjugate gradients, an ADMM gain-bound step and feasible full-tap line/block refinements. Multi-position smooth worst-risk steps use the exact log-sum-exp gradient and its positive low-rank Hessian term, solved by Newton-CG with a true-objective line search. Single-position quadratic steps use residual-correction CG so small late updates are not hidden by a large absolute right-hand side. Default lower gain bounds use successive supporting half-planes inside the magnitude annuli. An experimental annular projection is also available through the Python API. Phase anchors change between subproblems, so convexity of an individual subproblem is not proof of global optimality. Returned-filter feasibility and numerical convergence are reported separately. The iteration budget can expire with a feasible improving candidate but without a converged optimum.

Native-topology frequency preconditioning uses an exact main/subwoofer Schur complement when the observation graph proves that main/main cross blocks are zero. Arbitrary graphs fall back to full complex blocks. No optimization variables or observations are removed by this factorization. The native wrapper defaults to a positive root-data spectral ADMM metric; other metrics remain explicit research options.

An optional Python setting `joint_endpoint_refinement=true` adds full-parameter search directions that preserve the current feasible DC and Nyquist values. It uses the actual native synthesis adjoint when supplied, and still checks all native FFT bins. It only constructs extra refinement directions; the main optimization keeps all endpoint degrees of freedom. This research option defaults to false: analytical blocked-direction tests pass, but the first full-size comparison did not establish a consistent improvement.

The memory check estimates the complete problem and stops when the explicit budget is insufficient. It does not remove positions, repeats or taps to make the problem fit. Computation can be cancelled from the progress panel.

The native wrapper retains four complete joint iterates for one final candidate-polishing pass. It reuses full-vector and individual-output-block directions, including endpoint-preserving variants, under the **original** risk objective and every original gain bound. It accepts only strictly improving feasible candidates. Different proposals do not acquire their own targets or looser constraints. All FIR/native parameters remain present, and retained-vector memory is included in the budget. Python settings `joint_polish_pool_size` and `joint_polish_passes` accept 0–8, with defaults 4 and 1. The lower-level `polish_candidates` API can compare other complete vectors under the same rules. This is a feasible local improvement procedure, not a global optimality claim. If polishing changes the returned candidate after ADMM stopping, numerical convergence of that new candidate is not inferred.

The declared gain-bound numerical envelope is applied consistently to both projection and returned-filter feasibility. It is explicit in the UI and report; choose zero to request nominal limits rather than an extra dB envelope. Phase anchors and their supporting subproblems use the same admissible bounds.

Gain constraints and returned-filter checks cover every native FFT bin. They are not a continuous-frequency certificate between bins or a measurement of hardware headroom.

## Frequency-dependent windows and phase

An optional reference-centred Gaussian window is evaluated separately at each frequency. Its amplitude FWHM is `cycles / frequency`; this is a precise convention, not an assertion that another product uses the same window. Window contributions below the stated amplitude floor are omitted with a reported absolute error bound. Use the largest peak or an explicitly selected early-lobe reference. An early-lobe detector is a heuristic and reports threshold sensitivity; it is not proof of a direct arrival. Original samples and timing are preserved. The full recorded response remains the physical forward model; the windowed response only changes the high-frequency correction goal.

The default phase target includes the causal minimum-phase contribution of the requested magnitude correction. Preserving the routed phase instead asks for magnitude EQ with unchanged phase and may need substantially more design delay. The experimental arrival target uses explicit arrival metadata or an IR peak reference; the peak is not proof of a direct-sound arrival. The requested design delay is a target reference, not a measured constant delay of the returned mixed-phase FIR.

Repeat diagnostics report peak timing spread, level spread and complex repeat agreement. Agreement is not a magnitude-squared coherence measurement. Clock-drift correction requires original recordings and independent timing markers and is not inferred from one already-deconvolved IR.

These are independent implementations of the described methods. GSonic's public feature descriptions are a research reference; proprietary GSonic DSP code is not included.

## Expanded reference versus writable coefficients

XT32's observed expanded lengths are **16,321 main taps** and **16,055 subwoofer taps**. They are not independent writable hardware parameters. A 7.1.4 expanded reference therefore has **195,586 variables**, while the observed native vectors total **11,968 values**: eleven 1,024-value main vectors and one 704-value subwoofer vector.

Without a supplied synthesis model, this mode produces an expanded causal FIR reference. Optional legacy packing tables do not prove that the reference phase and magnitude will survive native reconstruction.

A user-supplied synthesis model can instead make the actual native coefficients the optimization variables. It supplies sparse matrices `expanded_FIR = B @ native_coefficients` and neutral native vectors. The solver uses the full matrices and their adjoints. The manifest must match the tier and receiver model; files, dimensions, array headers, primitive types, checksums and memory limits are validated. Unused columns must have zero neutral values. Supplied evidence is retained as unverified evidence, and never automatically grants hardware-verified status.

This mode remains **export-only**, including when a synthesis model is supplied. Native reconstruction, routing order and mixed-phase transfer have not been validated on physical hardware. It does not flash firmware.

## Native synthesis manifest

Keep local models outside the published tree, or under ignored `.native-profiles/`. NPZ/NPY models are excluded from distributions. Each NPZ contains exactly the CSR arrays `data`, `indices`, `indptr`, `shape`, plus `identity` for the neutral native coefficient vector.

```json
{
  "schema": "audioseyx-native-synthesis-1",
  "tier": "XT32",
  "model": "AVR-X3700H",
  "firmware": "exact firmware under test",
  "evidence": {"description": "source and measurements for this model"},
  "profiles": {
    "main": {"file": "main.npz", "sha256": "SHA256_OF_MAIN_FILE"},
    "sub": {"file": "sub.npz", "sha256": "SHA256_OF_SUB_FILE"}
  }
}
```

No receiver synthesis model is bundled. The sparse identities used in tests are artificial fixtures, not an X3700H implementation.

## Validation

### Reachability under the original target

Each design reports an optimistic lower bound on its **unchanged complex risk objective** and, when the returned filters meet the declared gain envelope, an upper bound on their remaining objective gap. These dimensionless values are neither magnitude-error dB nor measured acoustic improvement. The bounds are conditional on the supplied measurements, routing assumptions, FFT grid, targets and numerical envelope. The analysis never edits the requested target, refits a held-out position or changes the returned filters.

A reproducible SHA-256 fingerprint includes the objective weights, targets, complex routing terms, gain bounds, regularization and any supplied native synthesis matrices. It is checked before and after the analysis. Comparisons with different fingerprints need an explicit explanation of the changed problem.

Two relaxations complement one another. An observation-wise annular bound uses the triangle inequalities for every main/subwoofer term, including different native Nyquist limits. A shared-output disk relaxation retains all positions, sources and repeats coupled to the same output gains at each bin, while dropping lower gain limits. Residual duals provide lower bounds even when the diagnostic iteration budget expires. Bounded-memory batches evaluate every frequency bin; they do not remove observations or shorten any designed FIR.

For residual dual `y`, weight matrix `W`, routing matrix `H`, target `t` and gain limits `u`, the disk lower bound is

```text
- ||y||_W^2 - 2 Re(y* W t) - 2 sum_i u_i |(H* W y)_i|.
```

The implementation maximizes over a nonnegative scale of each residual at each bin. For smooth worst-position risk, a probability mixture gives a weighted least-squares lower bound minus the appropriate relative-entropy term. It evaluates both the position masses and the returned-filter soft-worst mixture, then combines valid bounds with the annular contribution. This uses the [weak-duality principle described by Boyd and Vandenberghe](https://web.stanford.edu/~boyd/cvxbook/).

Finite causal support, real endpoint gains, supplied native synthesis constraints and nonnegative regularization are relaxed. Floating-point safety margins are applied, but this is not a formal directed-rounding certificate. A small conditional objective gap does not establish hardware reconstruction, continuous-frequency headroom or independent acoustic performance. Diagnostic controls in the Python settings use `joint_reachability_iterations` (default 40; 0–500). Zero iterations still evaluates a valid residual dual at the initial spectra.

Specify position IDs in **Held-out positions**, then inspect the Results page. Plots show routed program-input sums. The JSON report retains per-position errors, repeat diagnostics, gain gaps, convergence history, model assumptions and independent-reference results. The reference is **this project's 0.4 algorithm**, rather than an OCA/GSonic run.

Run the fixed full-layout synthetic benchmark without connecting to any receiver:

```console
audioseyx --joint-benchmark --report joint-benchmark.json
```

The [recorded 0.5.0a3 result](https://github.com/haomjc/audioseyX/blob/main/examples/benchmarks/joint-0.5.0a3.json) includes settings, measurement fingerprint, dependency versions, candidate-polishing history and conditional reachability bounds. Its full 195,586-parameter fixture improves both training and excluded-position errors relative to this project's independent reference; the solver still reports numerical convergence as unestablished. A separate [three-seat synthetic result](https://github.com/haomjc/audioseyX/blob/main/examples/benchmarks/three-seat-0.5.0a3.json) retains centre/left/right priorities and an excluded spatial holdout. These are abstract generated rooms. Same-data OCA exports and normal-playback measurements are still required to establish comparative real-room performance.

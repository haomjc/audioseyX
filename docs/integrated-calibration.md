# Integrated native calibration and recording tools

Version 0.5.0a5 adds a minimum-phase EQ and receiver-menu planning workflow, auditory-band level matching, and independent raw-recording clock tools. These are independently written implementations of public methods. They do not include GSonic binaries, source or proprietary DSP. See GSonic's [feature description](https://github.com/ObsessiveCompulsiveAudiophile/GSonic/blob/master/README.md) and [license](https://github.com/ObsessiveCompulsiveAudiophile/GSonic/blob/master/LICENSE).

## Native EQ and menu planning

Select **Native EQ + distance / level / crossover planning** in Design. The workflow first designs every full native-budget minimum-phase FIR, then plans the selected receiver controls. A saved speaker-settings snapshot works offline; no receiver is contacted by the design calculation.

Choose the measurement context explicitly. The native-calibration model assumes that the receiver's separate-output calibration responses bypass normal output level and distance controls. The uncrossed/EQ-off playback model assumes those controls are already present in the recording. Both require comparable measurement gain; time search additionally requires a common timing reference and measured phase. Normal playback with unknown existing EQ or an already bass-managed sum must not be treated as an uncrossed response.

Gain order, crossover slopes and native reconstruction have not been physically verified. The default routing ensemble evaluates both the stated LR4 and BW2-high-pass/LR4-low-pass assumptions. It protects against those specific model differences, not arbitrary hardware behavior. Every supplied model and observation is retained. Increase the explicit memory budget when the complete problem does not fit; models, taps, positions and frequency bins are not removed to make it fit.

### Auditory-band relative levels

Confirmed same-model speakers are compared in ERB-spaced triangular energy bands, normally 200–5000 Hz. Every supported original FFT bin contributes. Repeat powers are averaged within a position so extra scans do not multiply its spatial priority. A robust Huber objective finds small native-grid level changes, subject to level bounds and zero total dB change within the group. Dynamic programming gives the exact minimum of this **discrete level-matching objective**.

The ERB scale follows the published auditory-frequency-resolution model discussed by [Moore](https://pmc.ncbi.nlm.nih.gov/articles/PMC2606789/). Our triangular response-energy bank is an engineering approximation, not a physiological filter model, ISO loudness calculation or verification of GSonic's perceptual engine. Different speaker models are not silently matched. An unmatched centre speaker retains its level.

When matching is enabled, confirmed same-model groups share a target reference derived from the training baseline. Those references are frozen before gains, timing or crossovers are selected, and remain frozen for excluded-position evaluation. This avoids treating the old per-speaker level differences as the intended calibration target.

### Sub timing and linked crossovers

One measured physical subwoofer supports a shared timing/filter-polarity search together with all supplied native crossover choices. Linked speaker groups keep their menu coupling. Existing relative main distances are preserved by default. Stereo without a subwoofer uses level-only planning; systems with more than one subwoofer may explicitly disable time/crossover search for level-only analysis. Independently timed multi-sub full-FIR reference design remains a separate workflow.

The time/crossover objective is normalized squared **power-response error**, with stated change penalties and smooth worst-source/position risk. Original impulses use complete-record FFT/zoom-FFT evaluation. Every Cartesian main/sub repeat contributes its complex cross terms. A power-error observation contains only a constant plus first and second delay harmonics. This permits exact evaluation at every declared native distance grid point and both polarities.

For a fixed shared sub distance/polarity, linked crossover groups are independent under a monotone nested risk function. Selecting each group's best crossover is therefore equivalent to enumerating the full Cartesian product. The result is an exact finite-grid minimum **conditional on the fixed FIRs, proposed level state, references and declared model**. It is not simultaneous global optimization of every FIR, gain and physical acoustic parameter. Auditory matching and native power fitting have distinct objectives; inspect both results and the original/proposed model scores.

Small speakers can receive a proposed crossover. Large speakers in LFE mode keep their full-band path and do not acquire a fictitious crossover or bass-timing observation. Large/LFE+Main modeling requires a known extraction cutoff. The workflow does not invent a bass-extraction setting or change speaker types, bass mode or amplifier assignment.

### Distance units and legacy conversion

The X3700H profile uses its documented distance range and increment choices: see [Distances](https://manuals.denon.com/AVRX3700H/NA/EN/GFNFSYwwqdpazi.php) and the [metric step description](https://manuals.denon.com/avrx3700h/ap/zh/DRDZSYyvwvohgk.php). The documented distance spread is respected, rather than automatically using an extended-distance workaround. Crossover candidates come from the [documented menu](https://manuals.denon.com/AVRX3700H/NA/EN/GFNFSYitmloggd.php).

**The receiver's distance/delay conversion is separate from air sound speed.** The supplied reference package assigns the X3700H a 300 m/s legacy conversion model. That is the default hypothesis here; it has not been independently verified against the user's current firmware. The model is explicit and adjustable.

Current main distance differences remain unchanged by default. The optional physical-distance rescale assumes the saved differences are physical measurements, converts their differences using the receiver/physical-speed ratio, and rounds to the actual menu grid. It is an explicit hypothesis, not an automatic correction of every existing calibration. Previously corrected distances must not be reinterpreted as uncorrected physical measurements without evidence. A common legal distance shift can be introduced while preserving relative delays.

### Applying the proposal

The native proposal remains offline until included in a transfer review. The receiver snapshot must still match when preparation/execution refreshes the connected device. Existing complete-channel, real-measurement and missing-seat guards remain active.

Reviewed values become native `Distance`, `ChLevel` and `Crossover` data. Status names such as SWMIX1 become individual SW1 control identifiers. A sub polarity choice changes the sign of its minimum-phase EQ filter; it is not a reversed-terminal diagnosis. The review digest covers all control and coefficient bytes, and execution uses a private immutable transaction snapshot.

Coefficient headroom attenuation is quantized to the native tenth-dB compensation grid. When a modeled plan supplies known absolute levels, compensating trims preserve the intended gains. A legacy settings-only plan without those levels still retains existing trims and reports its uncompensated common attenuation. Trim-budget violations stop preparation.

The report includes a conservative coherent-input bass-routing/FIR gain proxy. This does not measure amplifier headroom, nonlinear distortion, available acoustic output, unknown internal AVR attenuation or the actual LFE gain order. It is not a clipping or sound-pressure certificate. Native coefficient reconstruction and normal-playback response still require physical verification.

## Original-recording clock correction

The Analysis page can generate a continuous reference WAV and known pilot-template WAV. **Creating the files does not play sound.** An independent recording workflow must capture a stationary acoustic path. Supply the original recording, pilot template and at least three known pilot times.

The tool uses normalized matching and fractional peak fits to estimate a stable affine clock ratio. It rejects weak/competing matches, insufficient timing span and inconsistent nonlinear timing. Digital-silence roundoff is not accepted as a pilot. A fitted offset can contain device latency or a consistent room reflection; it does not identify a direct arrival.

A separate complete selected-channel recording is corrected with continuous-position, Kaiser-windowed sinc interpolation. The report retains the exact affine sample-coordinate anchor, new timing origin, finite filter precision, edge padding and original-file hashes. Original uploads remain unchanged. Additional recording channels are retained in the original file; only the chosen channel is exported.

This workflow requires **original audio plus known pilots**. Existing Audyssey or REW deconvolved impulses cannot establish the clock ratio. The corrected output is still a raw recording, not an IR or AVR filter. Proper deconvolution remains necessary before using a raw sweep recording as a measured impulse. There is no automatic ASIO/WASAPI capture or inferred clock recovery from arbitrary IRs in this release.

## Reproducible offline benchmark

```console
audioseyx --native-workflow-benchmark --report native-workflow.json
```

The fixture retains the complete 195,586-tap XT32 7.1.4 EQ space, repeated scans, three training seats and an excluded position. Its known solution includes a five-millisecond sub correction and native-grid level matching. A separate continuous-recording fixture has known clock drift and verifies correction without audio playback. These are abstract numerical checks, not OCA/GSonic acoustic comparisons or hardware certification.

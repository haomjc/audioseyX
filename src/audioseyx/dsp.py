from __future__ import annotations

from fractions import Fraction
import numpy as np
from scipy import signal, ndimage
from scipy.fft import next_fast_len, rfft, irfft

from .model import Measurement, Project, CHANNELS
from .capabilities import capability
from .curves import target_offset


def resample_ir(ir, source_rate, target_rate):
    if source_rate == target_rate:
        return np.asarray(ir, dtype=float).copy()
    ratio = Fraction(int(target_rate), int(source_rate)).limit_denominator(4096)
    return signal.resample_poly(ir, ratio.numerator, ratio.denominator) * source_rate / target_rate


def spectrum(measurement, frequencies):
    if measurement.ir is not None:
        nfft = next_fast_len(max(len(measurement.ir) * 2, 65536))
        response = rfft(measurement.ir, nfft)
        grid = np.fft.rfftfreq(nfft, 1 / measurement.sample_rate)
        magnitude = np.interp(frequencies, grid, np.abs(response))
        phase = np.interp(frequencies, grid, np.unwrap(np.angle(response)))
        return magnitude * np.exp(1j * phase - 2j * np.pi * frequencies * measurement.start_time_s)
    magnitude = 10 ** (np.interp(frequencies, measurement.frequency, measurement.magnitude_db) / 20)
    phase = np.interp(frequencies, measurement.frequency, np.unwrap(np.radians(measurement.phase_deg))) if measurement.phase_deg is not None else np.zeros_like(frequencies)
    return magnitude * np.exp(1j * phase)


def smooth_log(values, frequencies, fraction_octave=1 / 6):
    valid = frequencies > 0
    if valid.sum() < 3:
        return values.copy()
    low = max(frequencies[valid][0], 1.0)
    high = frequencies[-1]
    log_grid = np.geomspace(low, high, 2048)
    curve = np.interp(log_grid, frequencies, values)
    spacing = np.log2(high / low) / (len(log_grid) - 1)
    smoothed = ndimage.gaussian_filter1d(curve, max(fraction_octave / spacing / 2.355, 0.1))
    return np.interp(frequencies, log_grid, smoothed)


def band_weight(frequencies, low, high):
    f = np.maximum(frequencies, 1e-12)
    lower = np.clip(np.log2(f / max(low / 2, 0.1)), 0, 1)
    upper = np.clip(np.log2(max(high * 2, 1) / f), 0, 1)
    return (0.5 - 0.5 * np.cos(np.pi * lower)) * (0.5 - 0.5 * np.cos(np.pi * upper))


def min_phase_spectrum(magnitude):
    size = (len(magnitude) - 1) * 2
    cepstrum = irfft(np.log(np.maximum(magnitude, 1e-12)), size)
    causal = np.zeros(size)
    causal[0] = cepstrum[0]
    causal[1:size // 2] = 2 * cepstrum[1:size // 2]
    causal[size // 2] = cepstrum[size // 2]
    return np.exp(rfft(causal))


def design_channel(measurements, tier, channel, settings):
    cap = capability(tier)
    length, fs, packed = cap.shape(channel)
    nfft = next_fast_len(max(65536, length * 4))
    frequencies = np.fft.rfftfreq(nfft, 1 / fs)
    responses = np.asarray([spectrum(m, frequencies) for m in measurements])
    db = 20 * np.log10(np.maximum(np.abs(responses), 1e-12))
    statistics_mode=settings.get('spatial_statistics','legacy_measurement_median')
    measurement_weights=np.full(len(measurements),1/len(measurements))
    if statistics_mode=='position_balanced':
        from .listening_area import weighted_median
        positions=sorted({m.position for m in measurements})
        weights=np.array([float(settings.get('position_weights',{}).get(p,1)) for p in positions])
        if not np.isfinite(weights).all() or np.any(weights<=0):raise ValueError('Spatial weights must be finite and positive')
        weights/=weights.sum()
        position_curves=np.array([np.median(db[[i for i,m in enumerate(measurements) if m.position==p]],axis=0) for p in positions])
        centre=weighted_median(position_curves,weights)
        spread=1.4826*weighted_median(np.abs(position_curves-centre),weights)
        robust=centre
        for position,weight in zip(positions,weights):
            indices=[i for i,m in enumerate(measurements) if m.position==position]
            measurement_weights[indices]=weight/len(indices)
    elif statistics_mode=='legacy_measurement_median':
        robust = np.median(db, axis=0)
        spread=1.4826*np.median(np.abs(db-robust),axis=0)
    else:raise ValueError('Unknown spatial statistics mode')
    robust = smooth_log(robust, frequencies, float(settings.get("smoothing_octave", 1 / 6)))
    covered_low = max(0 if m.ir is not None else float(m.frequency[0]) for m in measurements)
    covered_high = min(m.sample_rate / 2 if m.ir is not None else min(m.sample_rate / 2, float(m.frequency[-1])) for m in measurements)
    requested_low = float(settings.get("low_hz", 20))
    requested_high = float(settings.get("sub_high_hz", 200) if channel.startswith("SW") else settings.get("high_hz", 500))
    low, high = max(requested_low, covered_low), min(requested_high, fs * .42, covered_high)
    boost = float(settings.get("max_boost_db", 3))
    cut = float(settings.get("max_cut_db", 12))
    if not (0 <= boost <= 12 and 0 <= cut <= 30 and 0 < requested_low < requested_high and low < high):
        raise ValueError('Invalid correction range or gain limits, or no overlap with the common measured band')
    limitations = []
    if covered_low > requested_low or covered_high < min(requested_high, fs * .42):
        limitations.append('Correction is limited to the band covered by every measurement. Missing frequency data are not extrapolated for correction.')
    reference_band = (frequencies >= (35 if channel.startswith("SW") else 300)) & (frequencies <= (100 if channel.startswith("SW") else 1200))
    reference_band &= (frequencies >= covered_low) & (frequencies <= covered_high)
    if not np.any(reference_band):
        reference_band = (frequencies >= low) & (frequencies <= high)
        limitations.append('The usual reference band is unavailable; normalization uses the effective design band.')
    reference = float(np.median(robust[reference_band]))
    bass = float(settings.get("bass_db", 3)) / (1 + (frequencies / 100) ** 4)
    tilt = float(settings.get("tilt_db_oct", -0.5)) * np.log2(np.maximum(frequencies, 1000) / 1000)
    target = reference + target_offset(settings["target_curve"], frequencies) if settings.get("target_curve") else reference + bass + tilt
    weights = band_weight(frequencies, low, high)
    supported = np.clip(np.log2(covered_high / np.maximum(frequencies, 1e-12)) / .25, 0, 1)
    if covered_low > 0:
        supported *= np.clip(np.log2(np.maximum(frequencies, 1e-12) / covered_low) / .25, 0, 1)
    weights *= supported
    boost_trust = np.clip(1 - spread / 12, 0, 1)
    # A repeatable deep null is still a poor boost target. This conservative
    # gain gate also avoids trying to extend a driver far below its roll-off.
    depth_trust = np.clip((robust - reference + 18) / 12, 0, 1)
    boost_trust *= depth_trust
    wanted = np.clip(target - robust, -cut, boost)
    correction_db = np.where(wanted > 0, wanted * boost_trust, wanted) * weights
    base = min_phase_spectrum(10 ** (correction_db / 20))
    requested_phase = settings.get("phase_mode", "minimum") == "mixed"
    phase_used = False
    delay_samples = 0
    if requested_phase:
        if any(m.ir is None for m in measurements):
            limitations.append('Real impulse responses are missing; using magnitude and minimum-phase design.')
        elif not settings.get("timing_confirmed", False):
            limitations.append('The timing reference is unconfirmed; using minimum-phase design.')
        else:
            delay_samples = min(round(float(settings.get("delay_ms", 5)) * fs / 1000), max(0, length // 4))
            if delay_samples < round(float(settings.get("delay_ms", 5)) * fs / 1000):
                limitations.append("The phase-design delay was shortened to fit this tier's filter length.")
            arrivals = [m.start_time_s + np.argmax(np.abs(m.ir)) / m.sample_rate for m in measurements]
            desired = 10 ** (target / 20) * np.exp(-2j * np.pi * frequencies[None, :] * (np.asarray(arrivals)[:, None] + delay_samples / fs))
            power = np.mean(np.abs(responses) ** 2, axis=0)
            regularization = np.maximum(power * 1e-3, np.max(power) * 1e-10)
            least_squares = np.mean(np.conj(responses) * desired, axis=0) / (power + regularization)
            delayed_base = base * np.exp(-2j * np.pi * frequencies * delay_samples / fs)
            phase_delta = np.unwrap(np.angle(least_squares / np.where(np.abs(delayed_base) > 1e-12, delayed_base, 1)))
            phase_delta -= phase_delta[0]
            phase_weight = band_weight(frequencies, low, min(float(settings.get("phase_high_hz", 150)), high))
            aligned = responses * np.exp(2j * np.pi * frequencies[None, :] * np.asarray(arrivals)[:, None])
            coherence = np.abs(np.mean(aligned / np.maximum(np.abs(aligned), 1e-12), axis=0))
            notch_trust = np.clip((robust - reference + 18) / 12, 0, 1)
            phase_weight *= coherence ** 2 * boost_trust * notch_trust * supported * 0.5
            base = delayed_base * np.exp(1j * phase_delta * phase_weight)
            phase_used = True
    base[0] = base[0].real
    base[-1] = base[-1].real
    full = irfft(base, nfft)
    taps = full[:length].copy()
    fade = min(64, max(4, length // 16))
    taps[-fade:] *= np.linspace(1, 0, fade) ** 2
    lost_energy = float(np.sum(full[length:] ** 2) / max(np.sum(full ** 2), 1e-30))
    if lost_energy > 0.01:
        limitations.append('Some design energy exceeds the native time-domain budget. The report evaluates the actual truncated filter.')
    actual = rfft(taps, nfft)
    evaluation = (frequencies >= low) & (frequencies <= high)
    target_error_before = db[:, evaluation] - target[evaluation]
    target_error_after = target_error_before + 20 * np.log10(np.maximum(np.abs(actual[evaluation]), 1e-12))
    grid = np.geomspace(max(10, covered_low), min(20000, fs * .45, covered_high), 360)
    averaged_db = measurement_weights@db
    metrics = {"rms_before_db": float(np.sqrt(np.mean(measurement_weights@(target_error_before ** 2)))),
               "rms_after_db": float(np.sqrt(np.mean(measurement_weights@(target_error_after ** 2)))),
               "truncated_energy_fraction": lost_energy,
               "effective_delay_ms": delay_samples * 1000 / fs,
               "filter_peak": float(np.max(np.abs(taps))), "coefficient_l1_gain_bound": float(np.sum(np.abs(taps))),
               "phase_mode": "limited_mixed" if phase_used else "minimum"}
    metrics["spatial_boost_trust_mean"] = float(np.mean(boost_trust[evaluation]))
    metrics["deep_null_boost_protection_fraction"] = float(np.mean(depth_trust[evaluation] < .5))
    metrics["effective_correction_band_hz"] = [low, high]
    metrics["common_measurement_band_hz"] = [covered_low, covered_high]
    metrics["reference_level_db"] = reference
    metrics['spatial_statistics']=statistics_mode
    if np.any((depth_trust < .5) & (wanted > 0) & (weights > .5)):
        limitations.append('Boost is reduced in deep nulls or substantial roll-off. This conservative gain protection does not identify the physical cause of the dip.')
    return {"channel": channel, "sample_rate": fs, "design_length": length, "packed_length": packed,
            "taps": taps, "metrics": metrics, "limitations": limitations,
            "chart": {"frequency": grid.tolist(), "before": np.interp(grid, frequencies, averaged_db).tolist(),
                      "after": np.interp(grid, frequencies, averaged_db + 20 * np.log10(np.maximum(np.abs(actual), 1e-12))).tolist(),
                      "target": np.interp(grid, frequencies, robust * (1 - weights) + target * weights).tolist()}}


def evaluate_channel_filter(measurements,item,settings):
    if not measurements:return {'channel':item['channel'],'available':False,'reason':'No validation response for this output'}
    fs=item['sample_rate'];nfft=next_fast_len(max(65536,len(item['taps'])*4))
    f=np.fft.rfftfreq(nfft,1/fs)
    band=item['metrics']['effective_correction_band_hz']
    low=max(band[0],max(float(m.frequency[0]) if m.ir is None else 0 for m in measurements))
    high=min(band[1],min(float(m.frequency[-1]) if m.ir is None else m.sample_rate/2 for m in measurements))
    selected=(f>=low)&(f<=high)
    if not selected.any():return {'channel':item['channel'],'available':False,'reason':'No common validation-band coverage'}
    frequencies=f[selected]
    response=np.array([20*np.log10(np.maximum(abs(spectrum(m,frequencies)),1e-15)) for m in measurements])
    reference=item['metrics']['reference_level_db']
    target=reference+(target_offset(settings['target_curve'],frequencies) if settings.get('target_curve') else
                      float(settings.get('bass_db',3))/(1+(frequencies/100)**4)+float(settings.get('tilt_db_oct',-.5))*np.log2(np.maximum(frequencies,1000)/1000))
    gain=20*np.log10(np.maximum(abs(rfft(item['taps'],nfft)[selected]),1e-15))
    positions=sorted({m.position for m in measurements});weights=np.zeros(len(measurements))
    for position in positions:
        indices=[i for i,m in enumerate(measurements) if m.position==position]
        weights[indices]=1/(len(positions)*len(indices))
    return {'channel':item['channel'],'available':True,'measurements':len(measurements),'positions':positions,
            'rms_before_db':float(np.sqrt(np.mean(weights@((response-target)**2)))),
            'rms_after_db':float(np.sqrt(np.mean(weights@((response+gain-target)**2)))),
            'reference_level_from_training_db':reference,'used_for_design':False,'renormalized_after_design':False}


def design_project(project, tier, settings, progress=None, *, cancel=None):
    from .model import unsafe_capture_indices
    if unsafe_capture_indices(project):
        raise ValueError("Some subwoofer records used unverified SWMIX sweep addressing. Preserve them, then exclude or remeasure before filter design.")
    if settings.get('curve_profiles') is not None:
        from .calibration_curves import design_calibration_curves
        return design_calibration_curves(project, tier, settings, progress, cancel=cancel)
    settings=dict(settings)
    validation=settings.get('validation_positions',[])
    if isinstance(validation,str):validation=[p.strip() for p in validation.split(',') if p.strip()]
    all_positions={m.position for m in project.measurements}
    if not isinstance(validation,list) or set(validation)-all_positions or (all_positions and set(validation)==all_positions):
        raise ValueError('Validation positions must be known and leave at least one training position')
    settings['validation_positions']=validation
    area=settings.get('listening_area',project.metadata.get('listening_area'))
    if area:
        from .listening_area import resolve_listening_area
        validation=settings.get('validation_positions',[])
        if isinstance(validation,str):validation=[p.strip() for p in validation.split(',') if p.strip()]
        resolved=resolve_listening_area(project,area,validation)
        if resolved['missing_training_seats'] and not settings.get('allow_partial_listening_area',False):
            raise ValueError('Row correction is missing measured seats: '+', '.join(resolved['missing_training_seats'])+'. Existing points can only produce an explicitly provisional design.')
        if settings.get('position_weights'):raise ValueError('Choose listening-area priorities or direct position weights, not both')
        settings.update(listening_area=area,listening_area_resolution=resolved,position_weights=resolved['position_weights'],spatial_statistics='position_balanced')
    elif settings.get('position_weights') and 'spatial_statistics' not in settings:
        settings['spatial_statistics']='position_balanced'
    numeric = ["low_hz", "high_hz", "sub_high_hz", "phase_high_hz", "delay_ms", "max_boost_db", "max_cut_db", "bass_db", "tilt_db_oct", "crossover_hz", "smoothing_octave"]
    for key in numeric:
        if key in settings and not np.isfinite(float(settings[key])):
            raise ValueError('Invalid numerical correction parameter: ' + key)
    if not 0 <= float(settings.get("delay_ms", 5)) <= 30:
        raise ValueError('Design delay must be between 0 and 30 ms')
    if settings.get("phase_mode", "minimum") not in ["minimum", "mixed"]:
        raise ValueError('Invalid phase mode')
    if not 0.02 <= float(settings.get("smoothing_octave", 1 / 6)) <= 1:
        raise ValueError('Invalid smoothing width')
    unknown = [channel for channel in project.channels if channel not in CHANNELS]
    if unknown:
        raise ValueError('Check channel mappings in the measurement list first: ' + ", ".join(unknown))
    optimizer = settings.get("optimizer", "independent")
    if optimizer not in ["independent", "native_joint", "native_calibration"]:
        raise ValueError("Unknown filter-design optimizer")
    if optimizer == 'native_calibration' and settings.get('phase_mode', 'minimum') != 'minimum':
        raise ValueError('Native-menu calibration uses a minimum-phase EQ base; arbitrary mixed-phase references remain separate')
    if optimizer == "native_joint":
        from .native_joint import design_native_joint
        result = design_native_joint(project, tier, settings, progress, cancel=cancel)
        return attach_temporal_prediction(project, result, settings, cancel=cancel)
    output = []
    for index, channel in enumerate(project.channels):
        if cancel is not None and cancel.is_set():
            raise InterruptedError("Filter design cancelled")
        group = [m for m in project.measurements if m.channel == channel and m.position not in validation]
        if not group:raise ValueError('No training response for output '+channel)
        output.append(design_channel(group, tier, channel, settings))
        if progress:
            progress(round((index + 1) / len(project.channels) * 85), f"Designed {channel}（{index + 1}/{len(project.channels)}）")
    result={"tier": capability(tier).tier, "project_id": project.id, "origin": project.origin,
            "settings": settings, "channels": output, "hardware_verified": False,
            "receiver_upload_ready": False,
            "notice": 'Curves are offline predictions. Native candidate encoding and actual hardware responses still need validation.'}
    result.update(training_positions=sorted(all_positions-set(validation)),validation_positions=validation)
    if validation:
        result['independent_validation']={'positions':validation,'used_for_design':False,
            'channels':[evaluate_channel_filter([m for m in project.measurements if m.channel==item['channel'] and m.position in validation],item,settings) for item in output]}
    if optimizer == 'native_calibration':
        from .receiver_calibration import calibrate_receiver
        result['receiver_calibration'] = calibrate_receiver(project, result, settings, progress=progress, cancel=cancel)
        charts=result['receiver_calibration'].pop('routed_charts',[])
        if charts:result['routed_charts']=charts
        else:
            for item in result['channels']:
                item['limitations'].append('This output plot shows the EQ FIR stage only; the separate receiver proposal lists any additional native level/distance changes.')
        if result.get('independent_validation'):
            result['independent_validation']['stage']='EQ_FIR_before_receiver_controls; native-menu validation is reported separately'
    return attach_temporal_prediction(project, result, settings, cancel=cancel)


def attach_temporal_prediction(project, result, settings, *, cancel=None):
    if settings.get('temporal_diagnostics', False):
        from .temporal import predict_filter_temporal
        result['temporal_prediction'] = predict_filter_temporal(project, result,
            memory_mib=float(settings.get('temporal_memory_mib', 128)), cancel=cancel)
    return result


def joint_sub_alignment(project, designs, crossover=80.0):
    if not project.metadata.get("timing_confirmed") and project.origin != "synthetic":
        return {"available": False, "reason": 'Joint main/subwoofer alignment requires a common timing reference. Confirm measurement timing first.'}
    if any(m.ir is None and m.phase_deg is None for m in project.measurements):
        return {"available": False, "reason": 'Measured phase or impulse responses are missing. Assumed zero phase cannot establish common alignment.'}
    subs = [channel for channel in project.channels if channel.startswith("SW")]
    if len(subs) != 1:
        return {"available": False, "reason": 'The current shared-subwoofer alignment module supports one independent subwoofer. Multi-subwoofer optimization is pending.'}
    lookup = {item["channel"]: item for item in designs["channels"]}
    frequencies = np.geomspace(crossover / 1.5, crossover * 1.5, 72)
    pairs = []
    sub_rows = {m.position: m for m in project.measurements if m.channel == subs[0]}
    for main in project.measurements:
        if main.channel.startswith("SW") or main.position not in sub_rows:
            continue
        s = sub_rows[main.position]
        main_design, sub_design = lookup[main.channel], lookup[subs[0]]
        def filt(item):
            return signal.freqz(item["taps"], worN=frequencies, fs=item["sample_rate"])[1]
        highpass = signal.sosfreqz(signal.butter(2, crossover, "highpass", fs=48000, output="sos"), worN=frequencies, fs=48000)[1]
        lowpass = signal.sosfreqz(signal.butter(2, crossover, "lowpass", fs=48000, output="sos"), worN=frequencies, fs=48000)[1] ** 2
        pairs.append((spectrum(main, frequencies) * filt(main_design) * highpass,
                      spectrum(s, frequencies) * filt(sub_design) * lowpass))
    if not pairs:
        return {"available": False, "reason": 'No main speaker and subwoofer measurements share matching positions.'}
    best = None
    for invert in [False, True]:
        for delay in np.linspace(-0.015, 0.015, 301):
            phase = np.exp(-2j * np.pi * frequencies * delay) * (-1 if invert else 1)
            ratios = [np.abs(main + sub * phase) / np.maximum(np.abs(main) + np.abs(sub), 1e-12) for main, sub in pairs]
            score = float(np.mean(20 * np.log10(np.maximum(ratios, 1e-6))))
            if best is None or score > best[0]:
                best = (score, delay, invert)
    return {"available": True, "relative_sub_delay_ms": round(best[1] * 1000, 2), "invert_sub": best[2],
            "mean_cancellation_db": round(best[0], 2), "measured_pairs": len(pairs), "crossover_hz": crossover,
            "notice": 'A common alignment suggestion using the stated crossover model. It is not written into the filters; the actual bass-management path must be checked.'}


def demo_project(layout=None,positions=3):
    from .layouts import preset_channels, validate_channels, summarize_layout
    channels=(preset_channels(layout) if isinstance(layout,str) else validate_channels(layout)) if layout is not None else ["FL", "FR", "C", "SLA", "SRA", "SBL", "SBR", "TFL", "TFR", "TRL", "TRR", "SW1"]
    if isinstance(positions,bool) or not isinstance(positions,int) or not 1<=positions<=20:
        raise ValueError("Demo positions must be an integer from 1 to 20")
    fs = 48000
    rows = []
    for c, channel in enumerate(channels):
        for position in range(positions):
            ir = np.zeros(16384)
            arrival = 170 + c * 7 + position * 4
            ir[arrival] = 0.6 if channel.startswith("SW") else 0.8
            ir[arrival + 530 + position * 9] = 0.32
            ir[arrival + 1220] = -0.15
            time = np.arange(len(ir) - arrival) / fs
            ir[arrival:] += 0.006 * np.exp(-time / (0.07 + 0.008 * position)) * np.sin(2 * np.pi * (57 + c * 1.3) * time)
            if channel.startswith("SW"):
                ir = signal.sosfilt(signal.butter(4, 200, fs=fs, output="sos"), ir)
            rows.append(Measurement(channel, f"p{position}", f"{channel} / position {position + 1}", fs, ir, timing_reference="synthetic_common"))
    signature=summarize_layout(channels)["signature"]
    return Project(signature+' synthetic demo', rows, "synthetic", "AVR-X3700H" if layout is None else "SYNTHETIC-LAYOUT", "XT32", {"timing_confirmed": True},
                   ['Artificial demonstration data for functional checks only. They must not be uploaded as real room calibration.'])

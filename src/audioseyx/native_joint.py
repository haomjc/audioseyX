"""Native-topology joint FIR design with every output tap and measurement kept."""
from __future__ import annotations

from collections import defaultdict
from itertools import product
from math import prod
import hashlib
import numpy as np
from scipy import signal
from scipy.fft import rfft

from .capabilities import capability
from .curves import target_offset
from .dsp import band_weight, spectrum, smooth_log, design_channel, min_phase_spectrum
from .joint_fir import FilterSpace, Observation, JointFIRProblem, parseval_energy
from .measurement_quality import frequency_dependent_response, repeat_diagnostics
from .model import Project
from .realization import load_realization
from .reachability import analyze_reachability
from .routing import crossover_responses, X3700H_CROSSOVERS
from .spectral_grid import record_response as measured_grid_response

def _position_weights(positions, supplied):
    supplied = supplied or {}
    if not isinstance(supplied, dict) or set(supplied) - set(positions):
        raise ValueError("Position weights must refer only to known training positions")
    result = {p: float(supplied.get(p, 1)) for p in positions}
    if any(not np.isfinite(value) or not 0 < value <= 100 for value in result.values()):
        raise ValueError("Position weights must be finite, positive and at most 100")
    total = sum(result.values())
    return {p: value / total for p, value in result.items()}


def prepare_native_problem(project, tier, settings, *, references=None, progress=None, realization_profile=None, cancel=None):
    if settings.get("timing_confirmed") is not True or settings.get("relative_levels_confirmed") is not True:
        raise ValueError("Full complex joint FIR design requires confirmed common timing and relative measurement levels")
    if settings.get("routing_measurements_confirmed") is not True:
        raise ValueError("Confirm that the measurements describe separate full-range outputs, not an already bass-managed sum")
    if any(m.ir is None and m.phase_deg is None for m in project.measurements):
        raise ValueError("Joint FIR design requires measured phase or original IRs for every observation")
    channels = project.channels
    if not channels or not project.measurements:
        raise ValueError("A complete measurement project is required")
    mains = [channel for channel in channels if not channel.startswith("SW")]
    subs = [channel for channel in channels if channel.startswith("SW")]
    if len(subs) > 1 and settings.get("independent_subs_confirmed") is not True:
        raise ValueError("Multiple subwoofer filters require confirmation that the outputs are independently controllable")
    positions = sorted({m.position for m in project.measurements})
    weights = _position_weights(positions, settings.get("position_weights"))
    groups = defaultdict(list)
    for index, measurement in enumerate(project.measurements):
        groups[(measurement.channel, measurement.position)].append(index)
    missing = [(channel, position) for channel in channels for position in positions if not groups[(channel, position)]]
    if missing:
        raise ValueError("Incomplete output-by-position matrix: " + ", ".join(f"{c}/{p}" for c, p in missing))
    cap = capability(tier)
    rate = max(cap.shape(channel)[1] for channel in channels)
    duration = max([len(m.ir) / m.sample_rate for m in project.measurements if m.ir is not None] or [0])
    filter_duration = max(cap.shape(channel)[0] / cap.shape(channel)[1] for channel in channels)
    nfft = 1 << int(np.ceil(np.log2(max(256, (duration + filter_duration) * rate + 1, filter_duration * rate * 2))))
    bins = nfft//2+1
    source_ids = mains + (["LFE"] if subs else [])
    combinations = []
    for position in positions:
        for source in source_ids:
            used = ([source] if source != "LFE" else []) + subs
            count = prod(len(groups[(channel, position)]) for channel in used)
            combinations.append((position, source, used, count))
    scenario_count = sum(row[3] for row in combinations)
    reachability_iterations = settings.get("joint_reachability_iterations", 40)
    if not isinstance(reachability_iterations, int) or not 0 <= reachability_iterations <= 500:
        raise ValueError("Joint reachability iterations must be 0-500")
    if not isinstance(settings.get("joint_endpoint_refinement", False), bool):
        raise ValueError("Joint endpoint refinement must be a boolean")
    for option,default in [("joint_polish_pool_size",4),("joint_polish_passes",1)]:
        value=settings.get(option,default)
        if not isinstance(value,int) or not 0<=value<=8:
            raise ValueError("Joint polishing pool size and passes must be 0-8")
    # Estimate measured spectra, cached routing terms, targets and Gram blocks.
    estimated_bytes = bins * (16 * (len(project.measurements) * (len(source_ids) + 1) + scenario_count + len(channels) ** 2) + 8 * 16)
    # The diagnostic retains every scenario's annular error, full native gain
    # envelopes and a shared spectral starting point, with bounded Gram batches.
    estimated_bytes += bins * (8 * scenario_count + 64 * len(channels)) + 8 * 1024 ** 2
    estimated_bytes += 8 * int(settings.get('joint_polish_pool_size',4)) * sum(cap.shape(c)[0] for c in channels)
    if settings.get('joint_exact_spatial_risk',True) and float(settings.get('position_risk',.35))>0 and len(positions)>1:
        sparse_blocks=len(mains)+len(subs)**2+2*len(mains)*len(subs)
        parameters=sum(cap.shape(channel)[0] for channel in channels)
        estimated_bytes+=len(positions)*(bins*16*sparse_blocks+3*parameters*8)
    acceleration_depth=int(settings.get('joint_anderson_depth',0))
    if not 0<=acceleration_depth<=8:raise ValueError('Anderson history depth must be 0-8 before allocating the complete problem')
    if acceleration_depth:
        native_bins=sum(nfft*cap.shape(c)[1]//rate//2+1 for c in channels)
        estimated_bytes+=native_bins*4*8*(4*acceleration_depth+8)
    estimated_bytes+=max([128*(len(m.ir)+bins) if m.sample_rate!=rate else 32*nfft for m in project.measurements if m.ir is not None] or [0])
    memory_mib = float(settings.get("joint_memory_mib", 1536))
    if not np.isfinite(memory_mib) or not 128 <= memory_mib <= 32768:
        raise ValueError("Joint solver memory budget must be 128-32768 MiB")
    if estimated_bytes > memory_mib * 1024 ** 2:
        raise ValueError(f"The complete problem needs about {estimated_bytes / 1024**2:.0f} MiB; increase the explicit memory budget or provide fewer recorded scenarios. No measurements were silently removed.")
    if realization_profile is None and settings.get("native_synthesis_path"):
        realization_profile = load_realization(settings["native_synthesis_path"], tier, project.model,
                                                max(16, memory_mib - estimated_bytes / 1024**2))
    if realization_profile:
        estimated_bytes += realization_profile["loaded_bytes"]
        if estimated_bytes > memory_mib * 1024**2:
            raise ValueError("The full problem and supplied native synthesis model exceed the memory budget")
    if progress:
        progress(3, f"Preparing {len(channels)} outputs, {len(positions)} positions and {scenario_count} repeat scenarios")
    f = np.fft.rfftfreq(nfft, 1 / rate)
    measured = [measured_grid_response(m,f,nfft,rate) for m in project.measurements]
    neutral = {}
    for channel in channels:
        value = np.ones(bins, dtype=complex)
        if realization_profile:
            role = "sub" if channel in subs else "main"
            entry = realization_profile["profiles"].get(role)
            if entry is None:
                raise ValueError("The native synthesis profile does not cover every output role")
            own_nfft = nfft * cap.shape(channel)[1] // rate
            own = rfft(entry["identity"],own_nfft)
            value[:] = 0
            value[:len(own)] = own
        neutral[channel] = value
    coverage_low = max([float(m.frequency[0]) for m in project.measurements if m.ir is None] or [0])
    coverage_high = min([float(m.frequency[-1]) if m.ir is None else m.sample_rate / 2 for m in project.measurements])
    low = max(float(settings.get("low_hz", 20)), coverage_low)
    high = min(float(settings.get("high_hz", 500)), rate * .42, coverage_high)
    if not 0 < low < high:
        raise ValueError("The requested joint correction has no common measured frequency band")
    sub_low=max(float(settings.get("sub_low_hz",min(float(settings.get("low_hz",20)),20))),coverage_low)
    sub_high=min(float(settings.get("sub_high_hz",200)),coverage_high,rate*.42)
    if subs and not 0 < sub_low < sub_high:
        raise ValueError("The subwoofer correction band has no common measured coverage")
    objective_low=min(low,sub_low) if subs else low
    boost, cut = float(settings.get("max_boost_db", 3)), float(settings.get("max_cut_db", 12))
    if not 0 <= boost <= 12 or not 0 <= cut <= 30:
        raise ValueError("Invalid joint gain bounds")
    model = settings.get("crossover_model", "lr4")
    mode = settings.get("bass_mode", "lfe")
    if mode not in ["lfe", "lfe_plus_main"]:
        raise ValueError("Invalid native bass-management mode")
    large = settings.get("large_channels", [])
    if isinstance(large, str):
        large = [value.strip().upper() for value in large.split(",") if value.strip()]
    if not isinstance(large, list) or set(large) - set(mains):
        raise ValueError("Large-speaker entries must identify measured main outputs")
    crossovers = {channel: float(settings.get("crossovers", {}).get(channel, settings.get("crossover_hz", 80))) for channel in mains}
    if "3700" in str(project.model) and any(value not in X3700H_CROSSOVERS for value in crossovers.values()):
        raise ValueError("X3700H crossover candidates must use one of its documented menu frequencies")
    gains = settings.get("sub_route_gains", {})
    gains = {channel: float(gains.get(channel, 1)) for channel in subs}
    if any(not np.isfinite(value) or not 0 < value <= 4 for value in gains.values()):
        raise ValueError("Invalid subwoofer routing gain")
    routes = {}
    for channel in mains:
        hp, lp = crossover_responses(f, crossovers[channel], model, rate)
        routes[(channel, channel)] = np.ones(bins, dtype=complex) if channel in large or not subs else hp
        if channel not in large or mode == "lfe_plus_main":
            for sub in subs:
                routes[(channel, sub)] = lp * gains[sub]
    if subs:
        lfe_hz = float(settings.get("lfe_lowpass_hz", 120))
        if not 80 <= lfe_hz <= 250:
            raise ValueError("LFE low-pass model must be between 80 and 250 Hz")
        lfe_lp = signal.sosfreqz(signal.butter(4, lfe_hz, fs=rate, output="sos"), worN=f, fs=rate)[1]
        for sub in subs:
            routes[("LFE", sub)] = lfe_lp * gains[sub]
    for key in routes:
        native_rate = cap.shape(key[1])[1]
        routes[key] = routes[key].copy()
        routes[key][f > native_rate * .45] = 0
    references = dict(references or {})
    for channel in channels:
        if channel in references:
            continue
        ref_band = (f >= (35 if channel.startswith("SW") else 300)) & (f <= (100 if channel.startswith("SW") else 1200))
        ref_band &= (f >= coverage_low) & (f <= min(rate * .45, coverage_high))
        if not ref_band.any():
            ref_band = (f >= low) & (f <= high)
        values = []
        for position in positions:
            levels = [float(np.median(20 * np.log10(np.maximum(np.abs((measured[i]*neutral[channel])[ref_band]), 1e-15)))) for i in groups[(channel, position)]]
            values.append(weights[position] * float(np.median(levels)))
        references[channel] = float(sum(values))
    if subs and "LFE" not in references:
        references["LFE"] = float(np.mean([references[channel] for channel in subs]))
    delay = float(settings.get("joint_delay_ms", 5)) / 1000
    if not np.isfinite(delay) or not 0 <= delay <= .1:
        raise ValueError("Joint design delay must be between 0 and 100 ms")
    spaces = []
    for channel in channels:
        length, native_rate, _ = cap.shape(channel)
        native_nfft = nfft * native_rate // rate
        delay_samples = round(delay * native_rate)
        if delay_samples >= length:
            raise ValueError(f"Requested delay does not fit the {channel} native FIR budget; choose a smaller delay. It was not silently shortened.")
        identity = np.zeros(length)
        identity[delay_samples] = 1
        own_f = np.fft.rfftfreq(native_nfft, 1 / native_rate)
        own_high = min(float(settings.get("sub_high_hz", 200)) if channel in subs else high, native_rate * .42, coverage_high)
        own_low=sub_low if channel in subs else low
        band = band_weight(own_f, own_low, own_high)
        level_curves = []
        for position in positions:
            curves = [20 * np.log10(np.maximum(np.abs(measured[i]*neutral[channel]), 1e-15)) for i in groups[(channel, position)]]
            level_curves.append(np.median(curves, axis=0))
        centre = smooth_log(np.median(level_curves, axis=0), f, float(settings.get("smoothing_octave", 1/6)))
        spread = 1.4826 * np.median(np.abs(level_curves - np.median(level_curves, axis=0)), axis=0)
        trust = np.clip((centre - references[channel] + 18) / 12, 0, 1) * np.clip(1 - spread / 12, 0, 1)
        trust = np.interp(own_f, f, trust)
        allowance = float(settings.get("outside_band_tolerance_db", .25))
        if not 0 <= allowance <= 3:
            raise ValueError("Out-of-band magnitude tolerance must be 0-3 dB")
        upper = 10 ** ((boost * trust * band + allowance * (1-band)) / 20)
        lower = 10 ** ((-cut * band - allowance * (1-band)) / 20)
        regularization = float(settings.get("joint_regularization", .002))
        if not np.isfinite(regularization) or not 1e-6 <= regularization <= 10:
            raise ValueError("Invalid joint FIR regularization strength")
        time = np.arange(length) / native_rate
        q = regularization * (1 + 8 * np.maximum(time - delay, 0) ** 2 / max(length/native_rate, 1e-9) ** 2)
        q[time < delay] *= float(settings.get("pre_ringing_penalty", 4))
        realized = realization_profile["profiles"].get("sub" if channel in subs else "main") if realization_profile else None
        if realization_profile and realized is None:
            raise ValueError("The supplied native synthesis model does not cover every output role")
        if realized:
            identity = realized["identity"]
            neutral_magnitude = np.abs(rfft(identity, native_nfft))
            lower, upper = lower * neutral_magnitude, np.maximum(upper * neutral_magnitude, 1e-12)
            spaces.append(FilterSpace(channel, native_rate, length, native_nfft, identity.copy(), identity,
                                      lower, upper, q, realized["matrix"], realized["identity_parameters"], realized["identity_parameters"]))
        else:
            spaces.append(FilterSpace(channel, native_rate, length, native_nfft, identity.copy(), identity, lower, upper, q))
    phase_target = settings.get("joint_phase_target", "minimum")
    if phase_target not in ["minimum", "preserve", "arrival"]:
        raise ValueError("Invalid joint phase target")
    fdw_cycles = float(settings.get("fdw_cycles", 0))
    fdw_start = float(settings.get("fdw_start_hz", 500))
    if not np.isfinite(fdw_cycles) or not (fdw_cycles == 0 or .5 <= fdw_cycles <= 100) or not np.isfinite(fdw_start) or not 20 <= fdw_start <= 20000:
        raise ValueError("Invalid frequency-dependent window settings")
    fdw = {}
    fdw_reports = []
    fdw_reference=settings.get('fdw_reference','peak')
    if fdw_reference not in ['peak','early_lobe']:raise ValueError("Choose peak or early_lobe for the FDW reference")
    if fdw_cycles:
        selection = (f >= fdw_start) & (f <= high * 2) & (f <= min(rate * .45,coverage_high))
        for index, measurement in enumerate(project.measurements):
            if cancel is not None and cancel.is_set():
                raise InterruptedError("Joint design cancelled during frequency-dependent windowing")
            if measurement.channel in subs or not selection.any():
                continue
            if progress:
                progress(5, f"Frequency-dependent response: {measurement.channel}/{measurement.position}")
            reference_sample=None
            reference_spread=None
            if fdw_reference=='early_lobe':
                from .polarity import early_reference
                reference_sample,reference_spread=early_reference(measurement)
            values, info = frequency_dependent_response(measurement, f[selection], fdw_cycles,reference_sample=reference_sample)
            info.update(reference_selection=fdw_reference,reference_threshold_spread_ms=reference_spread)
            fdw[index] = (selection, values)
            fdw_reports.append({"measurement_index": index, **info})
    coefficients = {}
    observations = []
    input_count = len(source_ids)
    quadrature = 1 / np.maximum(f, objective_low)
    covered = (f >= max(objective_low, coverage_low)) & (f <= min(rate * .45, coverage_high, 20000))
    quadrature *= covered
    quadrature *= nfft / max(2 * np.sum(quadrature) - quadrature[0] - quadrature[-1], 1e-30)
    for position, source, used, count in combinations:
        if cancel is not None and cancel.is_set():
            raise InterruptedError("Joint design cancelled while preparing observations")
        for combination in product(*(groups[(channel, position)] for channel in used)):
            terms = []
            main_index = None
            for channel, measurement_index in zip(used, combination):
                if (source, channel) not in routes:
                    continue
                if channel == source:
                    main_index = measurement_index
                key = (source, channel, measurement_index)
                if key not in coefficients:
                    coefficients[key] = measured[measurement_index] * routes[(source, channel)]
                terms.append((channels.index(channel), coefficients[key]))
            baseline = sum(value * neutral[channels[output]] for output, value in terms)
            upper = min(float(settings.get("sub_high_hz", 200)), rate * .42) if source == "LFE" else high
            target_low=sub_low if source=="LFE" else objective_low
            correction = band_weight(f, target_low, upper)
            reference = references[source]
            offset = target_offset(settings["target_curve"], f) if settings.get("target_curve") else (
                float(settings.get("bass_db", 3)) / (1 + (f / 100) ** 4)
                + float(settings.get("tilt_db_oct", -.5)) * np.log2(np.maximum(f, 1000) / 1000))
            target_magnitude = 10 ** ((reference + offset) / 20)
            if source == "LFE":
                # Keep the intentional program-channel LPF in the target;
                # an optimizer must not spend boost trying to undo it.
                target_magnitude *= np.abs(lfe_lp)
            magnitude = np.abs(baseline)
            wanted = (1 - correction) * magnitude + correction * target_magnitude
            if main_index in fdw:
                selection, early = fdw[main_index]
                desired_early_gain = target_magnitude[selection] / np.maximum(np.abs(early), 1e-12)
                early_goal = magnitude[selection] * desired_early_gain
                blend = np.clip(np.log2(f[selection] / fdw_start), 0, 1)
                wanted[selection] = wanted[selection] * (1-blend) + ((1-correction[selection])*magnitude[selection] + correction[selection]*early_goal) * blend
            phase = np.unwrap(np.angle(baseline))
            if phase_target == "minimum":
                # A magnitude correction needs its causal phase. Keeping phase
                # unchanged would instead demand linear-phase equalization,
                # often requiring far more delay than the requested budget.
                ratio=np.maximum(wanted,1e-20)/np.maximum(magnitude,1e-20)
                correction_phase=np.unwrap(np.angle(min_phase_spectrum(np.clip(ratio,1e-6,1e6))))
                phase+=correction_phase
            if phase_target == "arrival":
                index = main_index if main_index is not None else combination[0]
                measurement = project.measurements[index]
                arrival = measurement.metadata.get("direct_arrival_s")
                if arrival is None:
                    if measurement.ir is None:
                        raise ValueError("An arrival phase target requires an original IR or explicit direct_arrival_s")
                    arrival = measurement.start_time_s + np.argmax(np.abs(measurement.ir)) / measurement.sample_rate
                target_phase = -2 * np.pi * f * float(arrival)
                difference = np.unwrap(target_phase - phase)
                difference -= round(difference[0] / (2*np.pi)) * 2*np.pi
                phase += difference * band_weight(f, target_low, min(float(settings.get("phase_high_hz", 150)), upper))
            desired = wanted * np.exp(1j * phase - 2j * np.pi * f * delay)
            normalization = max(10 ** (reference / 10), 1e-15)
            frequency_weight = quadrature / normalization
            observations.append(Observation(position, source, tuple(terms), desired, frequency_weight,
                                            weights[position] / (input_count * count)))
    problem = JointFIRProblem(spaces, observations, nfft)
    return problem, {"references_db": references, "channels": channels, "inputs": source_ids,
                     "positions": positions, "repeat_scenarios": scenario_count,
                     "measurements_used": len(project.measurements), "measurements_discarded": 0,
                     "estimated_memory_mib": estimated_bytes / 1024**2, "native_sample_rates": {s.channel:s.sample_rate for s in spaces},
                     "routing_edges": [{"source":i,"output":o} for i,o in routes],
                     "crossover_model": model, "crossovers_hz": crossovers, "bass_mode": mode,
                     "large_channels": large, "lfe_in_objective": bool(subs), "phase_target": phase_target,
                     "filter_delay_ms": delay * 1000, "routing_hardware_verified": False,
                     "speaker_correction_band_hz":[low,high],"subwoofer_correction_band_hz":[sub_low,sub_high] if subs else None,
                     "fdw": fdw_reports, "repeat_diagnostics": repeat_diagnostics(project.measurements),
                     "native_synthesis": {"supplied":bool(realization_profile),
                                          "manifest_sha256":realization_profile["manifest_sha256"] if realization_profile else None,
                                          "firmware":realization_profile.get("firmware") if realization_profile else None,
                                          "independently_hardware_verified":False},
                     "notice": "The full measured complex matrix is used with the stated post-bass-management filter-order assumption. Crossover slopes, routing order and native reconstruction require hardware verification."}


def evaluate_joint(problem, taps, *, expanded=False):
    rows = []
    values = defaultdict(list)
    weights = defaultdict(list)
    filters = problem.expanded_spectra(taps) if expanded else problem.common_spectra(taps)
    complex_losses = []
    squared_errors=[]
    for observation, scalar in zip(problem.observations, problem.base_weights):
        predicted = sum(coefficient * filters[output] for output, coefficient in observation.terms)
        selected = observation.frequency_weight > 0
        target = observation.target
        complex_losses.append(parseval_energy((predicted-target)*np.sqrt(observation.frequency_weight),problem.nfft))
        error = 20 * np.log10(np.maximum(np.abs(predicted[selected]), 1e-12) / np.maximum(np.abs(target[selected]), 1e-12))
        quadrature = observation.frequency_weight[selected]
        error_sq = float(np.dot(error**2, quadrature) / np.sum(quadrature))
        squared_errors.append(error_sq)
        key = (observation.source, observation.position)
        values[key].append(error_sq)
        weights[key].append(scalar)
    for key in sorted(values):
        rows.append({"source":key[0],"position":key[1],"magnitude_rms_db": float(np.sqrt(np.average(values[key], weights=weights[key]))),
                     "repeat_scenarios":len(values[key])})
    return {"rows": rows, "mean_group_rms_db": float(np.mean([r["magnitude_rms_db"] for r in rows])),
            "worst_group_rms_db": max(r["magnitude_rms_db"] for r in rows),
            "normalized_complex_error": float(np.dot(problem.base_weights, complex_losses)),
            "weighted_magnitude_rms_db":float(np.sqrt(np.dot(problem.base_weights,squared_errors))),
            "level_normalization_after_design": False}


def routed_charts(problem, parameters):
    before_filters = problem.common_spectra(problem.identity)
    after_filters = problem.common_spectra(parameters)
    frequency = np.fft.rfftfreq(problem.nfft, 1/max(space.sample_rate for space in problem.spaces))
    collected = {}
    for observation, scalar in zip(problem.observations,problem.base_weights):
        selected = np.flatnonzero(observation.frequency_weight > 0)
        if not len(selected):
            continue
        high = min(float(frequency[selected[-1]]),500 if observation.source=="LFE" else 20000)
        grid = np.geomspace(max(10,float(frequency[selected[0]])),high,360)
        before = sum(coefficient * before_filters[output] for output,coefficient in observation.terms)
        after = sum(coefficient * after_filters[output] for output,coefficient in observation.terms)
        target = observation.target
        item=collected.setdefault(observation.source,{"frequency":grid,"before":np.zeros(360),"after":np.zeros(360),"target":np.zeros(360),"mass":0.,"before_sq":0.,"after_sq":0.})
        for key,value in [("before",before),("after",after),("target",target)]:
            db=20*np.log10(np.maximum(np.abs(value),1e-12))
            item[key]+=scalar*np.interp(grid,frequency,db)
        mask=observation.frequency_weight>0
        for key,value in [("before_sq",before),("after_sq",after)]:
            error=20*np.log10(np.maximum(np.abs(value[mask]),1e-12)/np.maximum(np.abs(target[mask]),1e-12))
            w=observation.frequency_weight[mask]
            item[key]+=scalar*float(np.dot(error**2,w)/np.sum(w))
        item["mass"]+=scalar
    result=[]
    for source,item in collected.items():
        mass=item["mass"]
        result.append({"channel":source,"chart":{"frequency":item["frequency"].tolist(),
                                                  **{key:(item[key]/mass).tolist() for key in ["before","after","target"]}},
                       "metrics":{"rms_before_db":float(np.sqrt(item["before_sq"]/mass)),"rms_after_db":float(np.sqrt(item["after_sq"]/mass)),
                                  "phase_mode":"native_joint_mixed","effective_delay_ms":0},
                       "limitations":["Program-input response through the full stated routing model. Display averages retain separate position and repeat errors in the report. Routing and native synthesis remain unverified on hardware."]})
    return result


def design_native_joint(project, tier, settings, progress=None, *, cancel=None):
    candidates=settings.get("crossover_candidates_hz",[])
    if isinstance(candidates,str):
        candidates=[float(value.strip()) for value in candidates.split(",") if value.strip()]
    if candidates:
        if not isinstance(candidates,list) or len(candidates)>32 or settings.get("crossovers"):
            raise ValueError("Global crossover search needs at most 32 explicit candidates and no per-channel crossover overrides")
        candidates=list(dict.fromkeys(float(value) for value in candidates))
        allowed=X3700H_CROSSOVERS if "3700" in str(project.model) else None
        if any(not np.isfinite(value) or not 20<=value<=1000 or (allowed and value not in allowed) for value in candidates):
            raise ValueError("Crossover candidates must be finite frequencies supported by the receiver")
        if not any(channel.startswith("SW") for channel in project.channels):
            raise ValueError("A shared-subwoofer crossover search requires a measured subwoofer")
        comparisons=[]
        best=None
        for index,value in enumerate(candidates):
            if cancel is not None and cancel.is_set():
                raise InterruptedError("Crossover search cancelled")
            def candidate_progress(number,message):
                if progress:
                    progress(round((index+number/100)/len(candidates)*95),f"Crossover {value:g} Hz ({index+1}/{len(candidates)}): {message}")
            current=design_native_joint(project,tier,{**settings,"crossover_candidates_hz":[],"crossover_hz":value},candidate_progress,cancel=cancel)
            optimizer=current["joint_optimization"]
            evaluation=current["joint_evaluation"]["joint"]
            digest=hashlib.sha256()
            for item in current["channels"]:
                digest.update(item["channel"].encode("ascii"));digest.update(np.asarray(item["taps"],dtype="<f8").tobytes())
            comparisons.append({"crossover_hz":value,"training_risk_objective":optimizer["risk_objective"],
                                "mean_group_rms_db":evaluation["mean_group_rms_db"],"worst_group_rms_db":evaluation["worst_group_rms_db"],
                                "gain_constraints_satisfied":optimizer["gain_constraints_satisfied"],
                                "numerically_converged":optimizer["numerically_converged"],"filter_sha256":digest.hexdigest()})
            if best is None or optimizer["risk_objective"]<best["joint_optimization"]["risk_objective"]:
                best=current
        best["crossover_search"]={"candidates":comparisons,"selected_hz":best["joint_model"]["crossovers_hz"],
                                   "selection_uses_holdout":False,"selection_metric":"training_complex_position_risk_plus_regularization",
                                   "full_fir_resolved_for_every_candidate":True,"hardware_settings_applied":False,
                                   "notice":"Every listed global crossover was fully re-solved. This is exhaustive over the explicit list, not all possible per-speaker combinations. Apply and verify the matching receiver crossover settings separately."}
        best["settings"]["crossover_candidates_hz"]=candidates
        return best
    validation = settings.get("validation_positions", [])
    if isinstance(validation, str):
        validation = [p.strip() for p in validation.split(",") if p.strip()]
    all_positions = {m.position for m in project.measurements}
    if not isinstance(validation, list) or set(validation) - all_positions:
        raise ValueError("Unknown validation positions")
    train = Project(project.name, [m for m in project.measurements if m.position not in validation],
                    project.origin, project.model, project.tier, dict(project.metadata))
    if not train.measurements:
        raise ValueError("At least one training position is required")
    settings = dict(settings)
    if settings.get("position_weights"):
        if set(settings["position_weights"]) - all_positions:
            raise ValueError("Unknown position weights")
        settings["position_weights"] = {p:w for p,w in settings["position_weights"].items() if p not in validation}
    realization = load_realization(settings["native_synthesis_path"],tier,project.model,float(settings.get("joint_memory_mib",1536))) if settings.get("native_synthesis_path") else None
    problem, model = prepare_native_problem(train, tier, settings, progress=progress, realization_profile=realization, cancel=cancel)
    baseline = []
    independent_items=[]
    for space in problem.spaces:
        if cancel is not None and cancel.is_set():
            raise InterruptedError("Joint design cancelled while preparing independent reference filters")
        own_settings={**settings,"phase_mode":"minimum",'spatial_statistics':'legacy_measurement_median'}
        if space.channel.startswith("SW"):own_settings["low_hz"]=model["subwoofer_correction_band_hz"][0]
        item=design_channel([m for m in train.measurements if m.channel==space.channel],tier,space.channel,own_settings)
        shift=round(model["filter_delay_ms"]*space.sample_rate/1000)
        previous=np.zeros(space.length);previous[shift:]=item["taps"][:space.length-shift]
        baseline.extend(previous);independent_items.append(item)
    baseline=np.asarray(baseline)
    taps, optimization = problem.solve(iterations=int(settings.get("joint_iterations", 60)),
                                      cg_iterations=int(settings.get("joint_cg_iterations", 150)),
                                      cg_tolerance=float(settings.get('joint_cg_tolerance',1e-6)),
                                      over_relaxation=float(settings.get('joint_over_relaxation',1)),
                                      penalty_adaptation=settings.get('joint_penalty_adaptation','rms'),
                                      rho=float(settings.get("joint_rho", 3)),
                                      lower_bound_method=settings.get("joint_lower_bound_method","supporting_halfplanes"),
                                      penalty_metric=settings.get('joint_penalty_metric','root_data_diagonal'),
                                      bound_tolerance_db=float(settings.get('joint_bound_tolerance_db',.15)),
                                      exact_spatial_risk=settings.get('joint_exact_spatial_risk',True),
                                      risk_inner_iterations=int(settings.get('joint_risk_inner_iterations',6)),
                                      risk_inner_tolerance=float(settings.get('joint_risk_inner_tolerance',1e-7)),
                                      anderson_depth=int(settings.get('joint_anderson_depth',0)),
                                      endpoint_refinement=settings.get('joint_endpoint_refinement',False),
                                      polish_pool_size=settings.get('joint_polish_pool_size',4),
                                      polish_passes=settings.get('joint_polish_passes',1),
                                      risk_weight=float(settings.get("position_risk", .35)),
                                      risk_temperature=float(settings.get("risk_temperature", .15)), progress=progress, cancel=cancel,
                                      initial=baseline if realization is None else None)
    if progress:
        progress(87, "Analyzing the unchanged target and shared-filter reachability bounds")
    optimization["reachability"] = analyze_reachability(
        problem, taps, risk_weight=float(settings.get("position_risk", .35)),
        temperature=float(settings.get("risk_temperature", .15)),
        bound_tolerance_db=float(settings.get("joint_bound_tolerance_db", .15)),
        iterations=int(settings.get("joint_reachability_iterations", 40)), cancel=cancel)
    designs = []
    for output, space in enumerate(problem.spaces):
        independent = independent_items[output]
        parameters = taps[problem.slices[output]].copy()
        values = space.expand(parameters).copy()
        own = rfft(values, space.nfft)
        freq = np.fft.rfftfreq(space.nfft, 1/space.sample_rate)
        chart = independent["chart"]
        filter_db = np.interp(chart["frequency"], freq, 20*np.log10(np.maximum(np.abs(own),1e-15)))
        independent["taps"] = values
        if space.realization is not None:
            independent["_native_parameters"] = parameters
            independent["_native_parameter_taps"] = values.copy()
        independent["chart"]["after"] = (np.asarray(chart["before"]) + filter_db).tolist()
        independent["metrics"].update(phase_mode="native_joint_mixed", effective_delay_ms=model["filter_delay_ms"],
                                       filter_peak=float(np.max(np.abs(values))), coefficient_l1_gain_bound=float(np.sum(np.abs(values))))
        selected=(freq>=independent["metrics"]["effective_correction_band_hz"][0])&(freq<=independent["metrics"]["effective_correction_band_hz"][1])
        observed=np.asarray([20*np.log10(np.maximum(np.abs(spectrum(m,freq[selected])),1e-15)) for m in train.measurements if m.channel==space.channel])
        wanted=(independent["metrics"]["reference_level_db"]+target_offset(settings["target_curve"],freq[selected])) if settings.get("target_curve") else (
            independent["metrics"]["reference_level_db"]+float(settings.get("bass_db",3))/(1+(freq[selected]/100)**4)
            +float(settings.get("tilt_db_oct",-.5))*np.log2(np.maximum(freq[selected],1000)/1000))
        gain=20*np.log10(np.maximum(np.abs(own[selected]),1e-15))
        independent["metrics"].update(rms_before_db=float(np.sqrt(np.mean((observed-wanted)**2))),
                                       rms_after_db=float(np.sqrt(np.mean((observed+gain-wanted)**2))),
                                       truncated_energy_fraction=0.,finite_support_by_construction=True)
        independent["limitations"].append("This individual-output plot is not the routed main/subwoofer sum; use the joint evaluation for coupled results. Joint mixed-phase transfer remains unverified.")
        designs.append(independent)
    baseline = np.asarray(baseline)
    evaluation = {"identity":evaluate_joint(problem, problem.identity),
                  "independent_0_4_baseline":evaluate_joint(problem, baseline, expanded=True), "joint":evaluate_joint(problem,taps),
                  "comparison_to_oca": "not_established_no_same_data_reference_run"}
    if validation:
        heldout = Project(project.name, [m for m in project.measurements if m.position in validation],
                          project.origin, project.model, project.tier, dict(project.metadata))
        validation_settings = {**settings, "position_weights":settings.get('validation_position_weights',{})}
        other, _ = prepare_native_problem(heldout, tier, validation_settings, references=model["references_db"], realization_profile=realization)
        # Different recording lengths can produce different FFT grids; taps,
        # native sample rates and lengths remain identical and are not refitted.
        evaluation["holdout"] = {"positions":validation, "used_for_design":False,
                                  "identity":evaluate_joint(other, other.identity),
                                  "independent_0_4_baseline":evaluate_joint(other, baseline, expanded=True), "joint":evaluate_joint(other, taps)}
    optimization["writable_native_coefficients"] = sum(capability(tier).shape(channel)[2] for channel in project.channels)
    optimization["verified_native_synthesis_available"] = False
    optimization["supplied_native_parameter_model"] = bool(realization)
    optimization["design_space"] = "native_parameters_with_supplied_synthesis" if realization else "expanded_causal_fir_reference"
    optimization["native_realization_notice"] = ("Optimization variables are the writable native coefficients in the supplied synthesis model. Its hardware validity has not been independently verified."
                                               if realization else "Expanded FIR taps are not independent native DSP parameters. XT32 coefficient compression and actual synthesis require a supplied validated model; this is a full-FIR reference candidate, not proof of native realizability.")
    result = {"tier":capability(tier).tier, "project_id":project.id, "origin":project.origin,
              "settings":{**settings,"phase_mode":"native_joint_mixed"}, "channels":designs,
              "joint_model":model,"joint_optimization":optimization,"joint_evaluation":evaluation,
              "routed_charts":routed_charts(problem,taps),
              "training_positions":model["positions"],"validation_positions":validation,
              "hardware_verified":False,"receiver_upload_ready":False,
              "notice":"Full-complex, full-tap native-routing joint design. Predictions include the stated bass-management model; receiver routing, crossover slopes and native reconstruction are not hardware-verified."}
    for item in result["routed_charts"]:
        item["metrics"]["effective_delay_ms"]=model["filter_delay_ms"]
    return result

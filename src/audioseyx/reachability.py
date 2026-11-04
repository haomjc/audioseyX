"""Conditional lower bounds for the unchanged complex joint-design objective.

The annular bound gives each observation its own filters. The disk dual keeps
all observations coupled to shared output gains at each frequency. Both relax
finite causal support and native synthesis; neither changes a requested target.
Weak duality makes an unfinished diagnostic solve useful without treating it
as a converged design. These are floating-point model bounds, not hardware or
interval-arithmetic certificates.
"""
from __future__ import annotations

import hashlib
import json
import numpy as np
from scipy.special import logsumexp


def problem_fingerprint(problem, *, risk_weight=.35, temperature=.15, bound_tolerance_db=.15):
    """Fingerprint the stated objective and constraints, not a hardware claim."""
    digest = hashlib.sha256()
    def metadata(value):
        digest.update(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    def array(value, dtype="<f8"):
        value = np.asarray(value, dtype=dtype)
        metadata([value.shape, dtype])
        digest.update(value.tobytes())
    metadata(["audioseyx-joint-model-1", problem.nfft, risk_weight, temperature, bound_tolerance_db])
    for space in problem.spaces:
        metadata([space.channel, space.sample_rate, space.length, space.nfft, space.parameter_ridge])
        for value in [space.prior, space.identity, space.lower, space.upper, space.regularization,
                      space.prior_parameters, space.identity_parameters]:
            array(value)
        metadata(space.realization is not None)
        if space.realization is not None:
            metadata(space.realization.shape)
            array(space.realization.data)
            array(space.realization.indices, "<i8")
            array(space.realization.indptr, "<i8")
    array(problem.base_weights)
    for observation in problem.observations:
        metadata([observation.position, observation.source])
        array(observation.target, "<c16")
        array(observation.frequency_weight)
        metadata(len(observation.terms))
        for output, coefficient in observation.terms:
            metadata(output)
            array(coefficient, "<c16")
    return digest.hexdigest()


def _energy(power, nfft):
    """Integrate already squared one-sided spectra using the solver's rule."""
    return float((2 * np.sum(power) - power[0] - power[-1]) / nfft)


def _risk_from_losses(problem, losses, risk_weight, temperature):
    positions = sorted({o.position for o in problem.observations})
    means, masses = [], []
    for position in positions:
        selected = np.array([o.position == position for o in problem.observations])
        mass = float(problem.base_weights[selected].sum())
        masses.append(mass)
        means.append(float(problem.base_weights[selected] @ losses[selected] / mass))
    means, masses = np.array(means), np.array(masses)
    risk = (1 - risk_weight) * (masses @ means) + risk_weight * temperature * logsumexp(means / temperature, b=masses)
    return float(risk), positions, means, masses


def _gain_bounds(problem, tolerance_db):
    bins = problem.nfft // 2 + 1
    envelope = 10 ** (tolerance_db / 20)
    lower, upper = np.zeros((len(problem.spaces), bins)), np.zeros((len(problem.spaces), bins))
    for i, space in enumerate(problem.spaces):
        count = space.nfft // 2 + 1
        lower[i, :count], upper[i, :count] = space.lower / envelope, space.upper * envelope
    return lower, upper


def _annular_errors(problem, lower, upper):
    """Triangle inequalities for the complete routed sum, including all subs."""
    rows, squared = [], []
    # Native grids can have different Nyquist limits, but share bin spacing.
    spacing = problem.spaces[0].sample_rate / problem.spaces[0].nfft
    frequency = np.arange(problem.nfft // 2 + 1) * spacing
    for observation in problem.observations:
        maximum = sum(np.abs(h) * upper[i] for i, h in observation.terms)
        minimum = np.zeros_like(maximum)
        for i, h in observation.terms:
            minimum = np.maximum(minimum, np.abs(h) * lower[i] - (maximum - np.abs(h) * upper[i]))
        target = np.abs(observation.target)
        # Relaxing real-valued endpoint gains is intentional and optimistic.
        gap = np.abs(target - np.clip(target, minimum, maximum))
        power = gap ** 2 * observation.frequency_weight
        squared.append(power)
        active = observation.frequency_weight > 0
        threshold = 1e-10 * np.maximum.reduce([target, maximum, np.full_like(target, 1e-30)])
        regions = []
        for low, high in [(0, 20), (20, 200), (200, 1000), (1000, 5000), (5000, float("inf"))]:
            selected = active & (frequency >= low) & (frequency < high)
            regions.append({"low_hz": low, "high_hz": None if not np.isfinite(high) else high,
                            "complex_error_lower_bound": _energy(power * selected, problem.nfft)})
        rows.append({"position": observation.position, "source": observation.source,
                     "complex_error_lower_bound": _energy(power, problem.nfft),
                     "active_bins": int(np.count_nonzero(active)),
                     "target_above_maximum_bins": int(np.count_nonzero(active & (target > maximum + threshold))),
                     "target_below_minimum_bins": int(np.count_nonzero(active & (target < minimum - threshold))),
                     "regions": regions})
    return np.asarray(squared), rows


def _disk_dual(z, entries, upper):
    """A feasible residual dual, independently rescaled at every frequency.

    For arbitrary complex y, a=H*Wy and |z_i|<=u_i imply
    ||Hz-t||_W^2 >= -||y||_W^2-2 Re(y*Wt)-2 sum(u_i |a_i|).
    Maximizing this expression over nonnegative multiples of y avoids both
    negative certificates and unnecessary cancellation near the optimum.
    """
    norm, correlation, target_power = (np.zeros(len(z)) for _ in range(3))
    adjoint = np.zeros_like(z)
    for terms, target, weight in entries:
        residual = sum(h * z[:, i] for i, h in terms) - target
        norm += weight * np.abs(residual) ** 2
        correlation += weight * np.real(np.conj(residual) * target)
        target_power += weight * np.abs(target) ** 2
        for i, h in terms:
            adjoint[:, i] += weight * np.conj(h) * residual
    support = np.sum(upper * np.abs(adjoint), axis=1)
    numerator = np.maximum(0, -correlation - support)
    dual = np.divide(numerator ** 2, norm, out=np.zeros_like(norm), where=norm > 0)
    # Conservative scale-aware subtraction; not a directed-rounding proof.
    margin = 64 * np.finfo(float).eps * (len(entries) + z.shape[1] + 2) * (norm + np.abs(correlation) + support + target_power)
    return np.maximum(0, np.minimum(dual, norm) - margin)


def _shared_disk_bound(problem, observation_weights, upper, initial, iterations, cancel):
    """Bound all bins in bounded-memory batches, retaining every observation."""
    bins, outputs = problem.nfft // 2 + 1, len(problem.spaces)
    result = np.zeros(bins)
    # No dimension grows with observations times all bins inside the batches.
    batch_size = max(8, min(256, 65536 // (outputs * outputs)))
    for start in range(0, bins, batch_size):
        if cancel is not None and cancel.is_set():
            raise InterruptedError("Joint reachability analysis cancelled")
        stop = min(bins, start + batch_size)
        gram = np.zeros((stop - start, outputs, outputs), dtype=complex)
        rhs = np.zeros((stop - start, outputs), dtype=complex)
        entries = []
        for scalar, observation in zip(observation_weights, problem.observations):
            weight = scalar * observation.frequency_weight[start:stop]
            target = observation.target[start:stop]
            terms = tuple((i, h[start:stop]) for i, h in observation.terms)
            entries.append((terms, target, weight))
            for i, h in terms:
                rhs[:, i] += weight * np.conj(h) * target
                for j, other in terms:
                    gram[:, i, j] += weight * np.conj(h) * other
        limits = upper[:, start:stop].T
        def project(value):
            return value * np.minimum(1, np.divide(limits, np.maximum(np.abs(value), 1e-300)))
        z = project(initial[:, start:stop].T.copy())
        momentum, scale = z.copy(), 1.
        lipschitz = np.max(np.sum(np.abs(gram), axis=2), axis=1)
        inverse = np.divide(1, lipschitz, out=np.zeros_like(lipschitz), where=lipschitz > 0)
        best = _disk_dual(z, entries, limits)
        for iteration in range(iterations):
            if iteration % 16 == 0 and cancel is not None and cancel.is_set():
                raise InterruptedError("Joint reachability analysis cancelled")
            normal = np.einsum("kij,kj->ki", gram, momentum) - rhs
            updated = project(momentum - inverse[:, None] * normal)
            next_scale = (1 + np.sqrt(1 + 4 * scale ** 2)) / 2
            momentum = updated + (scale - 1) / next_scale * (updated - z)
            z, scale = updated, next_scale
            if (iteration + 1) % 8 == 0 or iteration + 1 == iterations:
                best = np.maximum(best, _disk_dual(z, entries, limits))
        result[start:stop] = best
    return result


def analyze_reachability(problem, taps, *, risk_weight=.35, temperature=.15,
                         bound_tolerance_db=.15, iterations=40, cancel=None):
    """Analyze a fixed original problem; never refit, normalize or edit it.

    The objective gap is valid only relative to the supplied forward model,
    target, FFT grid and declared gain envelope. Nonnegative regularization is
    omitted from the lower bound. Hardware feasibility is not established.
    """
    if not 0 <= risk_weight <= 1 or not np.isfinite(temperature) or temperature <= 0:
        raise ValueError("Invalid reachability risk settings")
    if not np.isfinite(bound_tolerance_db) or not 0 <= bound_tolerance_db <= 1:
        raise ValueError("Invalid reachability gain envelope")
    if not isinstance(iterations, int) or not 0 <= iterations <= 500:
        raise ValueError("Reachability iterations must be 0-500")
    taps = np.asarray(taps, dtype=float)
    if taps.shape != (problem.size,) or not np.isfinite(taps).all():
        raise ValueError("Reachability parameters do not match the complete problem")
    original = problem_fingerprint(problem, risk_weight=risk_weight, temperature=temperature,
                                   bound_tolerance_db=bound_tolerance_db)
    lower, upper = _gain_bounds(problem, bound_tolerance_db)
    power, rows = _annular_errors(problem, lower, upper)
    losses = np.array([row["complex_error_lower_bound"] for row in rows])
    triangle, positions, _, masses = _risk_from_losses(problem, losses, risk_weight, temperature)
    _, actual_losses = problem.risk(taps, risk_weight, temperature)
    means = np.array([actual_losses[p] for p in positions])
    position_index = np.array([positions.index(o.position) for o in problem.observations])
    log_masses = np.log(masses)
    log_q = log_masses + means / temperature
    log_q -= logsumexp(log_q)
    mixtures = [("position_mass", masses, log_masses)]
    if risk_weight > 0 and len(positions) > 1:
        mixtures.append(("returned_filter_soft_worst", np.exp(log_q), log_q))
    initial = np.asarray(problem.common_spectra(taps))
    bounds = []
    for name, q, log_probability in mixtures:
        factors = (1 - risk_weight) + risk_weight * q / masses
        weights = problem.base_weights * factors[position_index]
        entropy = max(0., float(np.dot(q, log_probability - log_masses)))
        annular_power = weights @ power
        disk_power = _shared_disk_bound(problem, weights, upper, initial, iterations, cancel)
        shared = _energy(disk_power, problem.nfft) - risk_weight * temperature * entropy
        combined = _energy(np.maximum(annular_power, disk_power), problem.nfft) - risk_weight * temperature * entropy
        bounds.append({"position_mixture": name, "shared_disk_risk_lower_bound": max(0., shared),
                       "combined_risk_lower_bound": max(0., combined), "relative_entropy": entropy})
    value = max([triangle] + [r["combined_risk_lower_bound"] for r in bounds])
    objective = problem.risk(taps, risk_weight, temperature)[0]
    feasible = bool(problem.bound_violation_db(taps) <= bound_tolerance_db + 1e-7)
    consistent = bool(value <= objective + 1e-9 * max(1., abs(objective)))
    gap = max(0., objective - value) if feasible and consistent else None
    if original != problem_fingerprint(problem, risk_weight=risk_weight, temperature=temperature,
                                        bound_tolerance_db=bound_tolerance_db):
        raise ValueError("The joint model changed during reachability analysis; the bounds were not accepted")
    return {"method": "annular_triangle_and_shared_disk_residual_duality",
            "objective_and_constraints_sha256": original, "problem_unchanged_verified": True,
            "model_risk_lower_bound": value, "annular_triangle_risk_lower_bound": triangle,
            "returned_risk_objective": objective, "model_objective_gap_upper_bound": gap,
            "gap_fraction_of_returned_objective": gap / objective if gap is not None and objective > 0 else None,
            "returned_filter_gain_feasible": feasible, "lower_bound_consistency_passed": consistent,
            "shared_disk_iterations": iterations, "shared_disk_bounds": bounds,
            "positions": positions, "observations": len(rows), "observation_bounds": rows,
            "gain_bound_tolerance_db": bound_tolerance_db, "requested_target_changed": False,
            "all_observations_retained": True, "hardware_verified": False,
            "scope": "original_objective_under_stated_forward_model_and_native_fft_gain_bounds",
            "relaxed_constraints": ["finite_causal_support", "native_synthesis", "real_endpoint_gains", "nonnegative_regularization"],
            "notice": "Optimistic floating-point model bounds, not hardware performance or a formal interval certificate. The shared disk dual drops lower gain bounds; the annular triangle bound separates observations. The original targets and returned filters are unchanged. An unfinished diagnostic solve still supplies dual lower bounds."}

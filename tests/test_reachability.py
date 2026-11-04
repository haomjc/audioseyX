import threading
import unittest

import numpy as np
from scipy import sparse
from scipy.optimize import minimize

from audioseyx.joint_fir import FilterSpace, JointFIRProblem, Observation
from audioseyx.reachability import analyze_reachability, problem_fingerprint


def flat_problem(targets, *, lower=0, upper=10, weights=None):
    n = 32
    neutral = np.array([1., 0.])
    space = FilterSpace("FL", 48000, 2, n, neutral, neutral, lower, upper, np.zeros(2))
    one = np.ones(n // 2 + 1, dtype=complex)
    observations = [Observation(str(i), "FL", ((0, one),), one * t, np.ones(len(one)),
                                1 if weights is None else weights[i]) for i, t in enumerate(targets)]
    return JointFIRProblem([space], observations, n)


class ReachabilityTests(unittest.TestCase):
    def test_fingerprint_identifies_target_constraints_weights_and_risk_changes(self):
        p = flat_problem([1, 3])
        original = problem_fingerprint(p)
        self.assertNotEqual(original, problem_fingerprint(p, risk_weight=.5))
        self.assertNotEqual(original, problem_fingerprint(p, bound_tolerance_db=0))
        p.observations[0].target[4] += .01
        self.assertNotEqual(original, problem_fingerprint(p))
        p.observations[0].target[4] -= .01
        self.assertEqual(original, problem_fingerprint(p))
        p.spaces[0].upper[2] *= 1.1
        self.assertNotEqual(original, problem_fingerprint(p))
        p.spaces[0].upper[2] /= 1.1
        self.assertEqual(original, problem_fingerprint(p))
        p.base_weights[:] = [.4, .6]
        self.assertNotEqual(original, problem_fingerprint(p))

    def test_exact_single_output_upper_and_lower_conflicts(self):
        for target, lower, upper, candidate, expected in [(3, .5, 1.2, 1.2, 1.8 ** 2),
                                                          (.2, .8, 1.2, .8, .6 ** 2)]:
            p = flat_problem([target], lower=lower, upper=upper)
            report = analyze_reachability(p, [candidate, 0], bound_tolerance_db=0)
            self.assertAlmostEqual(report["model_risk_lower_bound"], expected, places=10)
            self.assertLess(report["model_objective_gap_upper_bound"], 1e-10)
            row = report["observation_bounds"][0]
            key = "target_above_maximum_bins" if target == 3 else "target_below_minimum_bins"
            self.assertEqual(row[key], p.nfft // 2 + 1)

    def test_shared_positions_detect_a_conflict_individual_curves_cannot(self):
        p = flat_problem([1, 3])
        report = analyze_reachability(p, [2, 0], risk_weight=0, bound_tolerance_db=0)
        self.assertEqual(report["annular_triangle_risk_lower_bound"], 0)
        self.assertAlmostEqual(report["model_risk_lower_bound"], 1, places=9)
        self.assertLess(report["model_objective_gap_upper_bound"], 1e-9)

    def test_complex_shared_disk_bound_matches_an_independent_constrained_solver(self):
        rng = np.random.default_rng(67)
        h = rng.normal(size=(4, 2)) + 1j * rng.normal(size=(4, 2))
        target = 4 * (rng.normal(size=4) + 1j * rng.normal(size=4))
        p = flat_problem([1], upper=1.2)
        p.spaces.append(p.spaces[0])
        one = np.ones(17, dtype=complex)
        observations = [Observation("p0", str(k), tuple((j, h[k, j] * one) for j in range(2)),
                                    target[k] * one, np.ones(17)) for k in range(4)]
        p = JointFIRProblem(p.spaces, observations, 32)
        def objective(x):
            z = x[:2] + 1j * x[2:]
            return float(np.mean(np.abs(h @ z - target) ** 2))
        reference = minimize(objective, np.zeros(4), method="SLSQP",
                             constraints=[{"type": "ineq", "fun": lambda x: 1.2 ** 2 - x[:2] ** 2 - x[2:] ** 2}],
                             options={"ftol": 1e-11, "maxiter": 300})
        self.assertTrue(reference.success, reference.message)
        report = analyze_reachability(p, p.identity, risk_weight=0, bound_tolerance_db=0, iterations=200)
        self.assertLessEqual(report["model_risk_lower_bound"], reference.fun + 1e-8)
        self.assertLess(reference.fun - report["model_risk_lower_bound"], 1e-6)

    def test_smooth_spatial_risk_dual_is_below_an_independent_optimum(self):
        p = flat_problem([.6, 1.5, 2.8], weights=[2, 1, 1])
        risk, temperature = .7, .12
        optimum = minimize(lambda x: p.risk(x, risk, temperature)[0], [1.7, 0],
                           method="BFGS", options={"gtol": 1e-9})
        report = analyze_reachability(p, optimum.x, risk_weight=risk, temperature=temperature,
                                      bound_tolerance_db=0, iterations=64)
        self.assertLessEqual(report["model_risk_lower_bound"], optimum.fun + 1e-10)
        self.assertLess(optimum.fun - report["model_risk_lower_bound"], 1e-6)
        self.assertEqual(len(report["shared_disk_bounds"]), 2)

    def test_mismatched_native_nyquist_has_zero_available_output_above_it(self):
        neutral = np.array([1., 0.])
        space = FilterSpace("SW1", 24000, 2, 32, neutral, neutral, 1, 1, np.zeros(2))
        one = np.ones(33, dtype=complex)
        p = JointFIRProblem([space], [Observation("p0", "LFE", ((0, one),), one, np.ones(33))], 64)
        report = analyze_reachability(p, neutral, risk_weight=0, bound_tolerance_db=0)
        self.assertAlmostEqual(report["model_risk_lower_bound"], 31 / 64)
        self.assertLess(report["model_objective_gap_upper_bound"], 1e-10)

    def test_multiple_subs_obey_the_full_sum_triangle_inequality(self):
        p = flat_problem([.1], lower=1, upper=1)
        p.spaces.append(FilterSpace("SW1", 48000, 2, 32, p.identity, p.identity, 1, 1, np.zeros(2)))
        one = np.ones(17, dtype=complex)
        p = JointFIRProblem(p.spaces, [Observation("p0", "FL", ((0, 2 * one), (1, one)),
                                                .1 * one, np.ones(17))], 32)
        report = analyze_reachability(p, [1, 0, -1, 0], risk_weight=0, bound_tolerance_db=0)
        self.assertAlmostEqual(report["model_risk_lower_bound"], .9 ** 2, places=10)
        self.assertLess(report["model_objective_gap_upper_bound"], 1e-10)

    def test_random_routing_duals_never_exceed_feasible_full_fir_candidates(self):
        rng = np.random.default_rng(184)
        n, length, outputs = 32, 5, 3
        neutral = np.r_[1., np.zeros(length - 1)]
        spaces = [FilterSpace(str(i), 48000, length, n, neutral, neutral, .2, 2,
                              np.linspace(0, .05, length)) for i in range(outputs)]
        observations = []
        for i in range(7):
            terms = tuple((j, rng.normal(size=17) + 1j * rng.normal(size=17)) for j in range(outputs))
            target = rng.normal(size=17) + 1j * rng.normal(size=17)
            weight = rng.random(17)
            weight[3:6] = 0
            observations.append(Observation(str(i % 3), str(i), terms, target, weight, i + 1))
        p = JointFIRProblem(spaces, observations, n)
        original = [o.target.copy() for o in observations]
        for budget in [0, 1, 9, 40]:
            report = analyze_reachability(p, p.identity, risk_weight=.8, temperature=.2,
                                          bound_tolerance_db=.15, iterations=budget)
            for _ in range(8):
                candidate = p.identity + rng.normal(0, .035, p.size)
                self.assertLess(p.bound_violation_db(candidate), .15)
                self.assertLessEqual(report["model_risk_lower_bound"], p.risk(candidate, .8, .2)[0] + 1e-9)
            self.assertTrue(report["lower_bound_consistency_passed"])
        for target, saved in zip(original, observations):
            np.testing.assert_array_equal(target, saved.target)

    def test_native_synthesis_is_an_explicit_relaxation_not_hardware_proof(self):
        p = flat_problem([3], lower=.1, upper=2)
        old = p.spaces[0]
        matrix = sparse.csr_matrix([[1., 0], [0, 0], [0, 1], [0, 0]])
        space = FilterSpace("FL", 48000, 4, 32, np.array([1., 0, 0, 0]), np.array([1., 0, 0, 0]),
                            old.lower, old.upper, np.zeros(4), matrix, np.array([1., 0]), np.array([1., 0]))
        p = JointFIRProblem([space], p.observations, p.nfft)
        report = analyze_reachability(p, [1, 0], bound_tolerance_db=0)
        self.assertIn("native_synthesis", report["relaxed_constraints"])
        self.assertFalse(report["hardware_verified"])
        self.assertFalse(report["requested_target_changed"])
        self.assertTrue(report["all_observations_retained"])

    def test_envelope_is_the_declared_returned_feasible_set(self):
        p = flat_problem([2], upper=1)
        envelope = 10 ** (.15 / 20)
        report = analyze_reachability(p, [envelope, 0], bound_tolerance_db=.15)
        self.assertAlmostEqual(report["model_risk_lower_bound"], (2 - envelope) ** 2, places=9)
        self.assertTrue(report["returned_filter_gain_feasible"])
        # An infeasible filter cannot supply an upper bound on the optimum.
        invalid = analyze_reachability(p, [1.5, 0], bound_tolerance_db=.15)
        self.assertFalse(invalid["returned_filter_gain_feasible"])
        self.assertIsNone(invalid["model_objective_gap_upper_bound"])

    def test_zero_frequency_weights_and_repeated_observations_preserve_bounds(self):
        p = flat_problem([1, 3])
        for observation in p.observations:
            observation.frequency_weight[5:] = 0
        first = analyze_reachability(p, [2, 0], risk_weight=.4, bound_tolerance_db=0)
        repeated = JointFIRProblem(p.spaces, p.observations * 3, p.nfft)
        second = analyze_reachability(repeated, [2, 0], risk_weight=.4, bound_tolerance_db=0)
        self.assertAlmostEqual(first["model_risk_lower_bound"], second["model_risk_lower_bound"], places=9)
        self.assertEqual(first["observation_bounds"][0]["active_bins"], 5)

    def test_cancellation_and_invalid_controls(self):
        p = flat_problem([1])
        event = threading.Event()
        event.set()
        with self.assertRaises(InterruptedError):
            analyze_reachability(p, p.identity, cancel=event)
        for options in [{"iterations": -1}, {"bound_tolerance_db": float("nan")}, {"risk_weight": 2}]:
            with self.assertRaises(ValueError):
                analyze_reachability(p, p.identity, **options)


if __name__ == "__main__":
    unittest.main()

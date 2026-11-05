import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from scipy.optimize import minimize

import numpy as np
from scipy import sparse

from audioseyx.joint_fir import FilterSpace, Observation, JointFIRProblem, project_supported_disk, FrequencyBlockInverse, AndersonHistory
from audioseyx.native_joint import prepare_native_problem, design_native_joint, evaluate_joint
from audioseyx.measurement_quality import frequency_dependent_response, repeat_diagnostics
from audioseyx.model import Measurement, Project
from audioseyx.native import compile_channel
from audioseyx.realization import load_realization
from audioseyx.exporter import export_bundle
from audioseyx.app import public_design


SETTINGS = {"timing_confirmed":True,"relative_levels_confirmed":True,"routing_measurements_confirmed":True,
            "bass_db":0,"tilt_db_oct":0,"joint_delay_ms":0,"high_hz":300,"joint_iterations":12,"position_risk":0}


def flat_project(positions=("p0","p1"), channels=("FL","SW1"), repeats=1):
    rows=[]
    for position in positions:
        for channel in channels:
            for repeat in range(repeats):
                ir=np.zeros(1024)
                ir[80]=1
                rows.append(Measurement(channel,position,f"{channel}/{position}/{repeat}",48000,ir,
                                        timing_reference="synthetic_common"))
    return Project("synthetic joint fixture",rows,"synthetic","AVR-TEST","XT32")


class FullSpaceOperatorTests(unittest.TestCase):
    def test_endpoint_refinement_escapes_a_blocked_line_without_relaxing_any_bin(self):
        n = 64
        neutral = np.r_[1., np.zeros(7)]
        upper = np.full(33, 4.)
        upper[0] = 1
        space = FilterSpace("FL", 48000, 8, n, neutral, neutral, 0, upper, np.zeros(8))
        one = np.ones(33, dtype=complex)
        observation = Observation("p0", "FL", ((0, one),), one.copy(), np.ones(33))
        p = JointFIRProblem([space], [observation], n)
        proposal = np.array([.5, .4, .4, .1, 0, 0, 0, 0])
        corrected = p.endpoint_preserving_proposal(neutral, proposal)
        observation.target = p.common_spectra(corrected)[0]
        stalled, alpha = p.feasible_line_candidate(neutral, proposal, 0, .15, 0)
        self.assertEqual(alpha, 0)
        improved, alpha = p.feasible_line_candidate(neutral, corrected, 0, .15, 0)
        self.assertGreater(alpha, .99)
        self.assertEqual(improved.shape, neutral.shape)
        self.assertLess(p.bound_violation_db(improved), 1e-9)
        self.assertLess(p.risk(improved, 0)[0], p.risk(stalled, 0)[0] * 1e-6)
        polished,report=p.polish_candidates(neutral,[proposal],risk_weight=0,temperature=.15,tolerance_db=0)
        self.assertLess(p.risk(polished,0)[0],1e-10)
        self.assertGreater(report['accepted_improvements'],0)
        self.assertTrue(report['original_objective_preserved'])
        self.assertTrue(report['all_parameters_retained'])
        np.testing.assert_allclose(p.native_spectra(corrected)[0][[0, -1]],
                                   p.native_spectra(neutral)[0][[0, -1]], atol=1e-12)

    def test_endpoint_projection_uses_the_actual_native_synthesis_adjoint(self):
        rng = np.random.default_rng(90)
        matrix = sparse.csr_matrix(rng.normal(size=(9, 4)))
        code = np.array([1., 0, 0, 0])
        neutral = matrix @ code
        space = FilterSpace("FL", 48000, 9, 32, neutral, neutral, 0, 100, np.zeros(9), matrix, code, code)
        one = np.ones(17, dtype=complex)
        p = JointFIRProblem([space], [Observation("p0", "FL", ((0, one),), one, np.ones(17))], 32)
        proposal = p.endpoint_preserving_proposal(code, rng.normal(size=4))
        np.testing.assert_allclose(p.native_spectra(proposal)[0][[0, -1]],
                                   p.native_spectra(code)[0][[0, -1]], atol=1e-12)
        self.assertEqual(len(proposal), 4)

    def test_candidate_fusion_retains_the_best_feasible_full_vector(self):
        n=32;neutral=np.array([1.,0.,0.,0.]);one=np.ones(17,dtype=complex)
        space=FilterSpace('FL',48000,4,n,neutral,neutral,.5,1.2,np.zeros(4))
        p=JointFIRProblem([space],[Observation('p0','FL',((0,one),),3*one,np.ones(17))],n)
        for candidates in [[],[neutral],[np.array([20.,0,0,0])],[np.array([.1,0,0,0]),np.array([20.,0,0,0])]]:
            result,report=p.polish_candidates(neutral,candidates,risk_weight=0,tolerance_db=0,passes=2)
            self.assertLessEqual(p.risk(result,0)[0],p.risk(neutral,0)[0])
            self.assertLessEqual(p.bound_violation_db(result),1e-8)
            scores=[report['initial_risk_objective']]+[row['risk_objective'] for row in report['history']]
            self.assertTrue(all(after<before for before,after in zip(scores,scores[1:])))
        with self.assertRaisesRegex(ValueError,'complete'):
            p.polish_candidates(neutral,[np.zeros(3)])
        with self.assertRaisesRegex(ValueError,'complete'):
            p.polish_candidates(neutral,[np.full(4,np.nan)])
        event=threading.Event();event.set()
        with self.assertRaises(InterruptedError):p.polish_candidates(neutral,[neutral],cancel=event)

    def test_solver_reuses_full_iterates_and_reports_post_polish_convergence_separately(self):
        n=32;neutral=np.array([1.,0.,0.,0.]);one=np.ones(17,dtype=complex)
        space=FilterSpace('FL',48000,4,n,neutral,neutral,.1,2,np.full(4,1e-8))
        p=JointFIRProblem([space],[Observation('p0','FL',((0,one),),one*.6,np.ones(17))],n)
        result,report=p.solve(iterations=16,risk_weight=0,polish_pool_size=4)
        self.assertEqual(report['candidate_polishing']['complete_candidate_vectors'],4)
        self.assertLessEqual(report['risk_objective'],report['candidate_polishing']['initial_risk_objective'])
        self.assertTrue(report['gain_constraints_satisfied'])
        self.assertEqual(result.shape,(4,))
        self.assertIn('admm_numerically_converged',report)

    def test_multisecant_acceleration_preserves_all_state_coordinates(self):
        history=AndersonHistory(3);matrix=np.array([[.95,.01],[0,.8]])
        value=np.array([2.,-3.])
        for _ in range(12):
            mapped=matrix@value
            proposal=history.proposal(value,mapped)
            value=mapped if proposal is None else proposal
        self.assertEqual(value.shape,(2,))
        self.assertLess(np.linalg.norm(value),1e-6)
    def test_small_needed_updates_are_not_lost_against_a_large_absolute_rhs(self):
        n=64;neutral=np.array([1e6,0.]);one=np.ones(n//2+1,dtype=complex)
        space=FilterSpace('FL',48000,2,n,neutral,neutral,0,1e8,np.full(2,1e-8))
        p=JointFIRProblem([space],[Observation('p0','FL',((0,one),),one*(1e6+.01),np.ones(len(one)))],n)
        result,report=p.solve(iterations=40,risk_weight=0,cg_tolerance=1e-6,regularization_rounds=1)
        self.assertAlmostEqual(result[0],1e6+.01,delta=1e-6)
        self.assertLess(p.observation_losses(result)[0],1e-10)
    def test_exact_risk_admm_matches_a_small_independent_unconstrained_optimization(self):
        rng=np.random.default_rng(47);n=32;bins=n//2+1
        neutral=np.array([1.,0.]);spaces=[FilterSpace(c,48000,2,n,neutral,neutral,0,100,np.full(2,.08)) for c in ['FL','SW1']]
        observations=[]
        for index,position in enumerate(['centre','left','right']):
            terms=tuple((i,rng.normal(size=bins)+1j*rng.normal(size=bins)) for i in range(2))
            observations.append(Observation(position,'FL',terms,np.ones(bins,dtype=complex)*(1+index*.2),np.ones(bins)))
        p=JointFIRProblem(spaces,observations,n);cache=p.position_quadratics()
        def gradient(x):return 2*(p.risk_curvature(x,cache,.6,.1)['gradient_half']+p.regularization_apply(x-p.prior))
        reference=minimize(lambda x:p.risk(x,.6,.1)[0],p.identity,jac=gradient,method='BFGS',options={'gtol':1e-9})
        result,report=p.solve(iterations=100,rho=.1,risk_weight=.6,risk_temperature=.1,cg_tolerance=1e-10,
                              regularization_rounds=1,risk_inner_iterations=10,risk_inner_tolerance=1e-9)
        self.assertEqual(report['spatial_risk_solver'],'exact_logsumexp_newton_cg')
        self.assertLessEqual(p.risk(result,.6,.1)[0],reference.fun+1e-6)
    def test_exact_spatial_risk_gradient_and_low_rank_hessian_match_finite_differences(self):
        rng=np.random.default_rng(129);n=64;bins=n//2+1
        neutral=np.array([1.,0.,0.,0.])
        spaces=[FilterSpace(c,48000,4,n,neutral,neutral,0,10,np.full(4,.03)) for c in ['FL','SW1']]
        observations=[]
        for position in ['centre','left','right']:
            terms=tuple((i,rng.normal(size=bins)+1j*rng.normal(size=bins)) for i in range(2))
            observations.append(Observation(position,'FL',terms,np.ones(bins,dtype=complex),np.ones(bins)))
        problem=JointFIRProblem(spaces,observations,n);cache=problem.position_quadratics()
        x=rng.normal(size=8)*.2;v=rng.normal(size=8);eps=1e-6
        def gradient(value):
            info=problem.risk_curvature(value,cache,.6,.2)
            return 2*(info['gradient_half']+problem.regularization_apply(value-problem.prior))
        info=problem.risk_curvature(x,cache,.6,.2)
        numerical=(problem.risk(x+eps*v,.6,.2)[0]-problem.risk(x-eps*v,.6,.2)[0])/(2*eps)
        self.assertAlmostEqual(float(gradient(x)@v),numerical,delta=1e-6)
        expected=2*(problem.normal_apply(v,info['gram'])+problem.regularization_apply(v)+info['low_rank'].T@(info['low_rank']@v))
        np.testing.assert_allclose(expected,(gradient(x+eps*v)-gradient(x-eps*v))/(2*eps),atol=1e-6)
        self.assertGreater(float(v@expected),0)
    def test_exact_schur_frequency_inverse_matches_dense_for_multiple_subwoofers(self):
        rng=np.random.default_rng(93);n=64;bins=n//2+1
        prior=np.array([1.,0.,0.,0.])
        spaces=[FilterSpace(c,48000,4,n,prior,prior,.1,2,np.full(4,.01)) for c in ['FL','FR','C','SW1','SW2']]
        observations=[]
        for main in range(3):
            terms=tuple((i,rng.normal(size=bins)+1j*rng.normal(size=bins)) for i in [main,3,4])
            observations.append(Observation('p0',str(main),terms,np.ones(bins,dtype=complex),np.ones(bins)))
        problem=JointFIRProblem(spaces,observations,n);gram,_=problem.normal_statistics(problem.base_weights)
        weights=[rng.uniform(.1,2,size=bins) for _ in spaces];rho=.7
        inverse=FrequencyBlockInverse(spaces,gram,rho,weights)
        symbol=np.zeros((bins,5,5),dtype=complex)
        for (i,j),value in gram.items():symbol[:,i,j]=value
        for i in range(5):symbol[:,i,i]+=.01+rho*weights[i]
        values=rng.normal(size=(5,bins))+1j*rng.normal(size=(5,bins))
        expected=np.linalg.solve(symbol,values.T[...,None])[...,0].T
        self.assertEqual(inverse.kind,'exact_main_sub_schur_blocks')
        np.testing.assert_allclose(inverse.apply(values),expected,atol=1e-11)

    def test_tolerated_anchor_remains_inside_the_actual_supporting_constraint_set(self):
        n=128;identity=np.array([0.,1.]);warm=np.array([.005,1.])
        one=np.ones(n//2+1,dtype=complex)
        space=FilterSpace('FL',48000,2,n,identity,identity,one.real,one.real,np.full(2,1e-10))
        target=np.fft.rfft(warm,n)
        problem=JointFIRProblem([space],[Observation('p0','FL',((0,one),),target,one.real)],n)
        result,report=problem.solve(iterations=30,regularization_rounds=1,initial=warm,risk_weight=0)
        self.assertTrue(report['numerically_converged'])
        self.assertTrue(report['projection_matches_returned_feasible_set'])
        np.testing.assert_allclose(result,warm,atol=1e-5)

    def test_zero_lower_bound_does_not_add_an_arbitrary_phase_halfplane(self):
        values=np.array([-1.+0j,-.8+.2j,-1.+0j]);anchor=np.ones(3,dtype=complex)
        result=project_supported_disk(values,np.zeros(3),np.ones(3)*2,anchor)
        np.testing.assert_allclose(result,values)

    def test_supported_disk_projection_matches_dense_feasible_search(self):
        rng=np.random.default_rng(18)
        values=rng.normal(size=10)+1j*rng.normal(size=10)
        anchor=np.exp(1j*rng.uniform(-np.pi,np.pi,10));anchor[[0,-1]]=1
        values[[0,-1]]=values[[0,-1]].real
        lower=np.full(10,.6);upper=np.full(10,1.3)
        result=project_supported_disk(values,lower,upper,anchor)
        self.assertTrue(np.all(np.abs(result)<=upper+1e-12))
        self.assertTrue(np.all(np.real(result*np.conj(anchor))>=lower-1e-12))
        radius=np.linspace(.6,1.3,500)
        angle=np.linspace(-np.arccos(.6/1.3),np.arccos(.6/1.3),500)
        grid=radius[:,None]*np.exp(1j*angle[None,:])
        feasible=grid[grid.real>=.6]
        for index in range(1,9):
            distance=np.min(np.abs(feasible*anchor[index]-values[index]))
            self.assertLessEqual(abs(result[index]-values[index]),distance+1e-6)

    def test_lower_annulus_line_search_can_cross_disconnected_feasible_intervals(self):
        prior=np.array([1.,0.,0.,0.]);n=16;one=np.ones(9,dtype=complex)
        space=FilterSpace("FL",48000,4,n,prior,prior,.8,1.2,np.ones(4)*1e-8)
        problem=JointFIRProblem([space],[Observation("p0","FL",((0,one),),-one,np.ones(9))],n)
        result,alpha=problem.feasible_line_candidate(prior,-prior,0,.15,1e-6)
        self.assertGreater(alpha,.99)
        self.assertLess(problem.bound_violation_db(result),1e-6)

    def test_cancellation_interrupts_before_any_cg_iteration(self):
        prior=np.array([1.,0.,0.,0.]);n=16;one=np.ones(9,dtype=complex)
        space=FilterSpace("FL",48000,4,n,prior,prior,.1,2,np.ones(4)*.01)
        problem=JointFIRProblem([space],[Observation("p0","FL",((0,one),),one,np.ones(9))],n)
        cancelled=threading.Event();cancelled.set()
        with self.assertRaises(InterruptedError):problem.solve(cancel=cancelled)

    def test_multirate_normal_operator_matches_finite_difference_and_is_symmetric(self):
        rng=np.random.default_rng(34)
        n=128;bins=n//2+1
        spaces=[]
        for channel,length,size,rate in [("FL",17,128,48000),("SW1",7,16,6000)]:
            prior=np.zeros(length);prior[0]=1
            spaces.append(FilterSpace(channel,rate,length,size,prior,prior,.1,3,np.ones(length)*.01))
        first=rng.normal(size=bins)+1j*rng.normal(size=bins)
        second=rng.normal(size=bins)+1j*rng.normal(size=bins)
        observation=Observation("p0","FL",((0,first),(1,second)),np.ones(bins,dtype=complex),rng.random(bins))
        problem=JointFIRProblem(spaces,[observation],n)
        gram,rhs=problem.normal_statistics(problem.base_weights)
        x=rng.normal(size=problem.size);v=rng.normal(size=problem.size)
        eps=1e-6
        numerical=(problem.observation_losses(x+eps*v)[0]-problem.observation_losses(x-eps*v)[0])/(2*eps)
        analytic=2*v@(problem.normal_apply(x,gram)-rhs)
        self.assertAlmostEqual(numerical,analytic,delta=1e-6)
        self.assertAlmostEqual(float(x@problem.normal_apply(v,gram)),float(v@problem.normal_apply(x,gram)),delta=1e-10)
        self.assertGreaterEqual(float(x@problem.normal_apply(x,gram)),0)
        line=problem.risk_line(x,v,.35,.15)
        for alpha in [0,.17,.9]:
            self.assertAlmostEqual(line(alpha),problem.risk(x+alpha*v,.35,.15)[0],delta=1e-9)
        weights=[rng.uniform(.1,2,size=space.nfft//2+1) for space in spaces]
        self.assertAlmostEqual(float(x@problem.realization_normal(v,weights)),float(v@problem.realization_normal(x,weights)),delta=1e-10)
        self.assertGreater(float(x@problem.realization_normal(x,weights)),0)

    def test_shared_output_is_solved_jointly_with_program_and_lfe_rows(self):
        n=128;size=n//2+1
        prior=np.zeros(16);prior[0]=1
        spaces=[FilterSpace(c,48000,16,n,prior,prior,.1,2,np.full(16,1e-8)) for c in ["FL","SW1"]]
        one=np.ones(size,dtype=complex)
        observations=[Observation("p0","FL",((0,one),(1,one)),one,np.ones(size)),
                      Observation("p0","LFE",((1,one),),one*.5,np.ones(size))]
        problem=JointFIRProblem(spaces,observations,n)
        result,report=problem.solve(iterations=40,risk_weight=0)
        self.assertAlmostEqual(result[0],.5,delta=.002)
        self.assertAlmostEqual(result[16],.5,delta=.002)
        self.assertLess(float(np.max(problem.observation_losses(result))),1e-5)
        self.assertTrue(report["gain_constraints_satisfied"])
        self.assertFalse(report["global_optimality_claimed"])

    def test_unreachable_target_does_not_escape_actual_fir_gain_bounds(self):
        n=128;prior=np.zeros(16);prior[0]=1
        upper=10**(3/20)
        space=FilterSpace("FL",48000,16,n,prior,prior,.1,upper,np.ones(16)*1e-5)
        one=np.ones(n//2+1,dtype=complex)
        problem=JointFIRProblem([space],[Observation("p0","FL",((0,one),),one*10,np.ones(len(one)))],n)
        result,report=problem.solve(iterations=60,risk_weight=0)
        self.assertLessEqual(np.max(np.abs(problem.native_spectra(result)[0])),upper*10**(.15/20)+1e-10)
        self.assertTrue(report["gain_constraints_satisfied"])
        self.assertGreater(problem.observation_losses(result)[0],1)

    def test_sparse_native_parameters_use_the_full_synthesis_and_its_adjoint(self):
        rng=np.random.default_rng(5);n=128;length=17
        matrix=sparse.csr_matrix(np.eye(length)[:,[0,2,4,6,8]])
        code=np.zeros(5);code[0]=1
        neutral=matrix@code
        space=FilterSpace("FL",48000,length,n,neutral,neutral,.1,3,np.ones(length)*.01,matrix,code,code)
        one=np.ones(n//2+1,dtype=complex)
        problem=JointFIRProblem([space],[Observation("p0","FL",((0,one),),one*.8,np.ones(len(one)))],n)
        gram,rhs=problem.normal_statistics(problem.base_weights)
        x=rng.normal(size=5);v=rng.normal(size=5);eps=1e-6
        numerical=(problem.observation_losses(x+eps*v)[0]-problem.observation_losses(x-eps*v)[0])/(2*eps)
        self.assertAlmostEqual(numerical,2*v@(problem.normal_apply(x,gram)-rhs),delta=1e-6)
        result,report=problem.solve(iterations=15,risk_weight=0)
        self.assertEqual(report["optimization_variables"],5)
        self.assertEqual(report["expanded_fir_taps"],17)
        self.assertTrue(np.allclose(space.expand(result)[1::2],0))

    def test_unused_native_padding_does_not_make_the_cg_system_singular(self):
        n=64;matrix=sparse.csr_matrix(np.column_stack([np.eye(8)[:,0],np.zeros(8)]))
        code=np.array([1.,0.]);neutral=matrix@code
        space=FilterSpace("FL",48000,8,n,neutral,neutral,.1,2,np.ones(8)*.01,matrix,code,code)
        one=np.ones(n//2+1,dtype=complex)
        problem=JointFIRProblem([space],[Observation("p0","FL",((0,one),),one*.8,np.ones(len(one)))],n)
        result,report=problem.solve(iterations=15,risk_weight=0)
        self.assertEqual(result[1],0)
        self.assertTrue(all(row["cg_converged"] for row in report["history"]))


class NativeRoutingTests(unittest.TestCase):
    def test_crossover_search_fully_resolves_candidates_without_using_holdout_to_select(self):
        project=flat_project()
        settings={**SETTINGS,"joint_iterations":10,"validation_positions":["p1"],"crossover_candidates_hz":[60,100]}
        first=design_native_joint(project,"XT32",settings)
        for row in project.measurements:
            if row.position=="p1":row.ir=row.ir*4
        second=design_native_joint(project,"XT32",settings)
        search=first["crossover_search"]
        self.assertTrue(search["full_fir_resolved_for_every_candidate"])
        self.assertFalse(search["selection_uses_holdout"])
        self.assertEqual(search["candidates"],second["crossover_search"]["candidates"])
        self.assertEqual(len(search["candidates"]),2)
        for a,b in zip(first["channels"],second["channels"]):np.testing.assert_array_equal(a["taps"],b["taps"])

    def test_every_repeat_combination_is_retained_with_position_balanced_weights(self):
        project=flat_project(repeats=2)
        problem,model=prepare_native_problem(project,"XT32",SETTINGS)
        self.assertEqual(model["measurements_used"],8)
        self.assertEqual(model["measurements_discarded"],0)
        self.assertEqual(model["repeat_scenarios"],12)
        self.assertTrue(model["lfe_in_objective"])
        self.assertIn({"source":"FL","output":"SW1"},model["routing_edges"])
        for p in ["p0","p1"]:
            mass=sum(w for w,o in zip(problem.base_weights,problem.observations) if o.position==p)
            self.assertAlmostEqual(mass,.5)

    def test_duplicate_repeats_do_not_change_the_quadratic_problem(self):
        original=flat_project()
        first,_=prepare_native_problem(original,"XT32",SETTINGS)
        original.measurements.extend(copy.deepcopy(original.measurements[:2]))
        second,_=prepare_native_problem(original,"XT32",SETTINGS)
        a,b=first.normal_statistics(first.base_weights),second.normal_statistics(second.base_weights)
        self.assertEqual(set(a[0]),set(b[0]))
        for key in a[0]:np.testing.assert_allclose(a[0][key],b[0][key],atol=1e-10)
        np.testing.assert_allclose(a[1],b[1],atol=1e-10)

    def test_intentional_lfe_lowpass_is_not_treated_as_a_room_defect(self):
        problem,_=prepare_native_problem(flat_project(),"XT32",SETTINGS)
        self.assertLess(float(np.max(problem.observation_losses(problem.identity))),1e-20)

    def test_missing_matrix_entries_and_unconfirmed_levels_are_rejected(self):
        project=flat_project();project.measurements.pop()
        with self.assertRaisesRegex(ValueError,"Incomplete"):
            prepare_native_problem(project,"XT32",SETTINGS)
        with self.assertRaisesRegex(ValueError,"relative measurement levels"):
            prepare_native_problem(flat_project(),"XT32",{**SETTINGS,"relative_levels_confirmed":False})
        with self.assertRaisesRegex(ValueError,"original IRs"):
            prepare_native_problem(Project("no phase",[Measurement("FL","p0","m",frequency=[20,100,1000],magnitude_db=[0,0,0])]),"XT32",SETTINGS)

    def test_low_tier_delay_is_not_silently_shortened(self):
        with self.assertRaisesRegex(ValueError,"not silently shortened"):
            prepare_native_problem(flat_project(),"MultEQ",{**SETTINGS,"joint_delay_ms":5})

    def test_holdout_changes_never_refit_the_training_filters(self):
        first=flat_project()
        settings={**SETTINGS,"validation_positions":["p1"],"joint_iterations":10}
        a=design_native_joint(first,"XT32",settings)
        for row in first.measurements:
            if row.position=="p1":row.ir=row.ir*4
        b=design_native_joint(first,"XT32",settings)
        for ca,cb in zip(a["channels"],b["channels"]):np.testing.assert_allclose(ca["taps"],cb["taps"],atol=1e-12)
        self.assertFalse(a["joint_evaluation"]["holdout"]["used_for_design"])
        self.assertEqual(a["joint_optimization"]["reachability"], b["joint_optimization"]["reachability"])
        self.assertGreater(b["joint_evaluation"]["holdout"]["joint"]["mean_group_rms_db"],a["joint_evaluation"]["holdout"]["joint"]["mean_group_rms_db"]+5)


class MeasurementWindowTests(unittest.TestCase):
    def test_explicit_early_fdw_reference_avoids_a_stronger_late_reflection(self):
        raw=np.zeros(4096);raw[240]=1;raw[1200]=3
        m=Measurement('FL','p0','early reference',48000,raw)
        f=np.array([1000.,1800.,3000.])
        response,report=frequency_dependent_response(m,f,6,reference_sample=240)
        np.testing.assert_allclose(response,np.exp(-2j*np.pi*f*240/48000),atol=1e-8)
        self.assertEqual(report['reference_source'],'explicit_analysis_reference')
        np.testing.assert_array_equal(m.ir,raw)
    def test_repeat_agreement_does_not_invent_phase_or_extrapolate_missing_coverage(self):
        group=[Measurement("FL","p0",str(i),frequency=[1000,2000,4000],magnitude_db=[0,0,0]) for i in range(2)]
        self.assertFalse(repeat_diagnostics(group)["groups"][0]["available"])
        group=[Measurement("FL","p0",str(i),frequency=[20,100,500],magnitude_db=[0,0,0]) for i in range(2)]
        row=repeat_diagnostics(group)["groups"][0]
        self.assertIsNone(row["complex_repeat_agreement_mean"])
        self.assertEqual(row["median_level_spread_db"],0)

    def test_fdw_removes_late_echo_without_changing_samples_or_timing(self):
        raw=np.zeros(4096);raw[240]=1;raw[1200]=.8
        measurement=Measurement("FL","p0","late echo",48000,raw,start_time_s=-.001)
        f=np.array([1000.,1700.,2700.])
        response,report=frequency_dependent_response(measurement,f,6)
        np.testing.assert_allclose(response,np.exp(-2j*np.pi*f*.004),atol=1e-8)
        np.testing.assert_array_equal(measurement.ir,raw)
        self.assertFalse(report["raw_ir_modified"])
        self.assertEqual(report["frequency_bins_evaluated"],3)

    def test_repeat_timing_diagnostic_does_not_claim_clock_correction(self):
        a=np.zeros(2048);a[100]=1
        b=np.zeros(2048);b[148]=1
        result=repeat_diagnostics([Measurement("FL","p0","a",ir=a),Measurement("FL","p0","b",ir=b)])
        self.assertAlmostEqual(result["groups"][0]["peak_time_spread_ms"],1)
        self.assertLess(result["groups"][0]["complex_repeat_agreement_mean"],.99)
        self.assertFalse(result["clock_drift_corrected"])


class NativeSynthesisTests(unittest.TestCase):
    def test_full_native_parameter_design_can_serialize_and_export_without_legacy_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            matrix=sparse.eye(16321,1024,format="csr")
            identity=np.zeros(1024);identity[0]=1
            file=root/"main.npz"
            np.savez_compressed(file,data=matrix.data,indices=matrix.indices,indptr=matrix.indptr,
                                shape=np.asarray(matrix.shape),identity=identity)
            manifest={"schema":"audioseyx-native-synthesis-1","tier":"XT32","model":"AVR-TEST",
                      "profiles":{"main":{"file":file.name,"sha256":hashlib.sha256(file.read_bytes()).hexdigest()}}}
            path=root/"manifest.json";path.write_text(json.dumps(manifest),encoding="utf-8")
            project=flat_project(channels=("FL",))
            result=design_native_joint(project,"XT32",{**SETTINGS,"native_synthesis_path":str(path),"validation_positions":["p1"]})
            self.assertEqual(result["joint_optimization"]["optimization_variables"],1024)
            self.assertEqual(result["joint_optimization"]["expanded_fir_taps"],16321)
            json.dumps(public_design(result),allow_nan=False)
            bundle=export_bundle(project,result,root/"export")
            import zipfile
            with zipfile.ZipFile(bundle) as archive:
                candidate=json.loads(archive.read("native-candidate.json"))
                self.assertTrue(candidate["encoding_available"])
                self.assertEqual(candidate["channels"][0]["coefficient_count"],1024)
                self.assertFalse(candidate["receiver_upload_ready"])
                self.assertTrue(candidate['float32_serialization_available'])
                self.assertEqual(candidate['channels'][0]['float32_precision']['coefficient_count'],1024)

    def test_supplied_native_parameters_survive_uniform_scaling_and_reject_stale_firs(self):
        code=np.zeros(1024);code[0]=.5;code[10]=.2
        taps=np.zeros(16321);taps[:1024]=code
        item={"channel":"FL","sample_rate":48000,"taps":taps*.25,
              "_native_parameters":code,"_native_parameter_taps":taps}
        result=compile_channel(item,"XT32")
        np.testing.assert_allclose(result["coefficients"],code*.25)
        self.assertEqual(result["codec"],"supplied_native_synthesis")
        item["taps"][14000]=.1
        with self.assertRaisesRegex(ValueError,"stale"):
            compile_channel(item,"XT32")

    def test_native_model_loader_checks_shape_checksum_and_evidence_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            matrix=sparse.eye(16321,1024,format="csr")
            identity=np.zeros(1024);identity[0]=1
            file=root/"main.npz"
            np.savez_compressed(file,data=matrix.data,indices=matrix.indices,indptr=matrix.indptr,
                                shape=np.asarray(matrix.shape),identity=identity)
            manifest={"schema":"audioseyx-native-synthesis-1","tier":"XT32","model":"AVR-TEST",
                      "evidence":{"synthetic":True},"profiles":{"main":{"file":file.name,"sha256":hashlib.sha256(file.read_bytes()).hexdigest()}}}
            path=root/"manifest.json";path.write_text(json.dumps(manifest),encoding="utf-8")
            result=load_realization(path,"XT32","AVR-TEST")
            self.assertEqual(result["profiles"]["main"]["matrix"].shape,(16321,1024))
            self.assertFalse(result["hardware_verified_by_audioseyx"])
            file.write_bytes(file.read_bytes()+b"modified")
            with self.assertRaisesRegex(ValueError,"checksum"):
                load_realization(path,"XT32","AVR-TEST")


if __name__=="__main__":unittest.main()

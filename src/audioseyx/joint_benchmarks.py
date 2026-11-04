"""Reproducible full-layout synthetic joint-FIR validation, without an AVR."""
from __future__ import annotations

import hashlib
import platform
import time

import numpy as np
import scipy

from . import __version__
from .dsp import demo_project, design_project
from .joint_fir import FilterSpace, Observation, JointFIRProblem


def run_joint_benchmarks(iterations=60, progress=None):
    started=time.monotonic()
    checks=[]
    n=128;prior=np.zeros(16);prior[0]=1;one=np.ones(n//2+1,dtype=complex)
    spaces=[FilterSpace(channel,48000,16,n,prior,prior,.1,2,np.full(16,1e-8)) for channel in ["FL","SW1"]]
    observations=[Observation("p0","FL",((0,one),(1,one)),one,np.ones(len(one))),
                  Observation("p0","LFE",((1,one),),one*.5,np.ones(len(one)))]
    problem=JointFIRProblem(spaces,observations,n)
    parameters,report=problem.solve(iterations=40,risk_weight=0)
    checks.append({"name":"Analytic coupled main/subwoofer and LFE solution", "passed":bool(np.max(problem.observation_losses(parameters))<1e-5),
                   "main_tap":float(parameters[0]),"subwoofer_tap":float(parameters[16]),"expected_tap":.5,
                   "numerically_converged":report["numerically_converged"]})
    project=demo_project()
    settings={"optimizer":"native_joint","phase_mode":"minimum","timing_confirmed":True,
              "relative_levels_confirmed":True,"routing_measurements_confirmed":True,
              "joint_iterations":iterations,"joint_cg_iterations":100,"joint_delay_ms":5,
              "position_risk":.35,"bass_db":3,"tilt_db_oct":-.5,"high_hz":500,
              "fdw_cycles":6,"validation_positions":["p2"]}
    design=design_project(project,"XT32",settings,progress)
    optimization=design["joint_optimization"];evaluation=design["joint_evaluation"]
    checks.append({"name":"Full XT32 7.1.4 tap space and explicit native budget", "passed":optimization["optimization_variables"]==195586 and optimization["writable_native_coefficients"]==11968,
                   "expanded_fir_variables":optimization["optimization_variables"],"writable_native_coefficients":optimization["writable_native_coefficients"]})
    checks.append({"name":"Returned FIR gain constraints", "passed":optimization["gain_constraints_satisfied"],
                   "maximum_violation_db":optimization["actual_bound_violation_db"],"tolerance_db":optimization["gain_bound_tolerance_db"]})
    for label,values in [("Training",evaluation),("Excluded position",evaluation["holdout"])]:
        baseline=values["independent_0_4_baseline"]["mean_group_rms_db"]
        joint=values["joint"]["mean_group_rms_db"]
        checks.append({"name":label+" / this project's independent 0.4 reference", "passed":joint<baseline,
                       "baseline_mean_group_rms_db":baseline,"joint_mean_group_rms_db":joint})
    fingerprint=hashlib.sha256()
    for measurement in project.measurements:
        fingerprint.update((measurement.channel+"/"+measurement.position).encode())
        fingerprint.update(np.asarray(measurement.ir,dtype="<f8").tobytes())
    return {"version":__version__,"origin":"synthetic","hardware_tested":False,
            "all_passed":all(item["passed"] for item in checks),"checks":checks,
            "solver_numerically_converged":optimization["numerically_converged"],
            "settings":settings,"measurement_sha256":fingerprint.hexdigest(),
            "joint_model":design["joint_model"],"joint_optimization":optimization,"joint_evaluation":evaluation,
            "runtime_seconds":time.monotonic()-started,
            "environment":{"python":platform.python_version(),"numpy":np.__version__,"scipy":scipy.__version__},
            "notice":"The assertions check this fixed synthetic layout, feasibility and comparison with this project's 0.4 algorithm. A feasible improving iterate is not a converged optimum. There is no same-data OCA/GSonic reference or physical receiver validation."}

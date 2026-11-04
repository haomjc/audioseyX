"""Full tap-space, multi-output FIR optimization with explicit spectral bounds.

Frequency sufficient statistics retain every observation and cross term. The
normal equations are applied with FFTs; no dense tap matrix or PEQ reduction is
used. Annular gain projection is nonconvex: convergence is reported, not assumed.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections import defaultdict
import numpy as np
from scipy.fft import rfft, irfft
from scipy.sparse.linalg import LinearOperator, cg
from scipy.special import logsumexp
from scipy import sparse
from scipy.optimize import minimize_scalar


@dataclass
class FilterSpace:
    channel: str
    sample_rate: int
    length: int
    nfft: int
    prior: np.ndarray
    identity: np.ndarray
    lower: np.ndarray
    upper: np.ndarray
    regularization: np.ndarray
    realization: object | None = None
    prior_parameters: np.ndarray | None = None
    identity_parameters: np.ndarray | None = None
    parameter_ridge: float = 1e-8

    def __post_init__(self):
        if self.length < 2 or self.nfft < self.length or self.nfft % 2:
            raise ValueError("Invalid FIR length or FFT size")
        for name in ["prior", "identity", "regularization"]:
            value = np.asarray(getattr(self, name), dtype=float)
            if value.shape != (self.length,) or not np.isfinite(value).all():
                raise ValueError("Invalid FIR " + name)
            setattr(self, name, value)
        for name in ["lower", "upper"]:
            value = np.broadcast_to(np.asarray(getattr(self, name), dtype=float), (self.nfft // 2 + 1,)).copy()
            if not np.isfinite(value).all() or np.any(value < 0) or (name == "upper" and np.any(value == 0)):
                raise ValueError("Invalid spectral " + name + " bound")
            setattr(self, name, value)
        if np.any(self.lower > self.upper) or np.any(self.regularization < 0):
            raise ValueError("Inconsistent FIR constraints")
        if not np.isfinite(self.parameter_ridge) or self.parameter_ridge <= 0:
            raise ValueError("Native parameter ridge must be positive")
        if self.realization is not None:
            if not sparse.issparse(self.realization) or self.realization.shape[0] != self.length or self.realization.dtype.kind not in "fiu":
                raise ValueError("A native realization must be a finite sparse synthesis matrix")
            self.realization = self.realization.tocsr()
            if not np.isfinite(self.realization.data).all():
                raise ValueError("Invalid native synthesis values")
            for name in ["prior_parameters", "identity_parameters"]:
                value = np.asarray(getattr(self, name), dtype=float)
                if value.shape != (self.realization.shape[1],) or not np.isfinite(value).all():
                    raise ValueError("Invalid native synthesis " + name)
                setattr(self, name, value)
            if not np.allclose(self.expand(self.prior_parameters), self.prior, rtol=1e-8, atol=1e-10) or not np.allclose(self.expand(self.identity_parameters), self.identity, rtol=1e-8, atol=1e-10):
                raise ValueError("Native synthesis parameters do not match their stated impulse responses")
        else:
            self.prior_parameters = self.prior.copy()
            self.identity_parameters = self.identity.copy()

    @property
    def variables(self):
        return self.length if self.realization is None else self.realization.shape[1]

    def expand(self, values):
        return np.asarray(values if self.realization is None else self.realization @ values, dtype=float)

    def adjoint(self, values):
        return np.asarray(values if self.realization is None else self.realization.T @ values, dtype=float)


@dataclass
class Observation:
    position: str
    source: str
    terms: tuple[tuple[int, np.ndarray], ...]
    target: np.ndarray
    frequency_weight: np.ndarray
    weight: float = 1.0


def parseval_energy(spectrum, nfft):
    power = np.abs(spectrum) ** 2
    return float((2 * np.sum(power) - power[0] - power[-1]) / nfft)


def project_annulus(values, lower, upper):
    magnitude = np.abs(values)
    phase = np.divide(values, magnitude, out=np.ones_like(values), where=magnitude > 1e-30)
    result = phase * np.clip(magnitude, lower, upper)
    # Real FIRs have real DC and Nyquist values.
    result[0] = np.copysign(abs(result[0]), values[0].real or 1)
    result[-1] = np.copysign(abs(result[-1]), values[-1].real or 1)
    return result


def project_supported_disk(values, lower, upper, anchor):
    """Project onto a disk intersected with an anchor-oriented half-plane.

    Re(z * conj(unit(anchor))) >= lower is an inner approximation of the
    lower-magnitude constraint. Unlike an annulus, this intersection is convex.
    The phase anchor can be updated between subproblems; no taps are removed.
    """
    phase = np.divide(anchor, np.abs(anchor), out=np.ones_like(anchor), where=np.abs(anchor)>1e-30)
    rotated = values * np.conj(phase)
    magnitude = np.abs(rotated)
    clipped = rotated * np.minimum(1, upper / np.maximum(magnitude, 1e-30))
    selected = clipped.real < lower
    clipped[selected] = lower[selected] + 1j*np.clip(rotated.imag[selected],
        -np.sqrt(np.maximum(upper[selected]**2-lower[selected]**2, 0)),
        np.sqrt(np.maximum(upper[selected]**2-lower[selected]**2, 0)))
    result = clipped * phase
    result[0] = result[0].real
    result[-1] = result[-1].real
    return result


class JointFIRProblem:
    def __init__(self, spaces, observations, nfft):
        self.spaces, self.observations, self.nfft = list(spaces), list(observations), int(nfft)
        spaces,observations=self.spaces,self.observations
        if not self.spaces or not self.observations or self.nfft % 2:
            raise ValueError("A joint FIR problem needs outputs and observations")
        if any(space.nfft > nfft or nfft % space.nfft for space in spaces):
            raise ValueError("Native sample grids must divide the common FFT grid")
        self.slices = []
        offset = 0
        for space in spaces:
            self.slices.append(slice(offset, offset + space.variables))
            offset += space.variables
        self.size = offset
        self.prior = np.concatenate([space.prior_parameters for space in spaces])
        self.identity = np.concatenate([space.identity_parameters for space in spaces])
        bins = nfft // 2 + 1
        for observation in observations:
            if observation.weight <= 0 or not np.isfinite(observation.weight):
                raise ValueError("Observation weights must be positive")
            if observation.target.shape != (bins,) or observation.frequency_weight.shape != (bins,):
                raise ValueError("Observation spectra do not match the common grid")
            if not np.isfinite(observation.target).all() or not np.isfinite(observation.frequency_weight).all() or np.any(observation.frequency_weight < 0):
                raise ValueError("Invalid observation values or weights")
            if not observation.terms or len({term[0] for term in observation.terms}) != len(observation.terms):
                raise ValueError("Observation terms must refer to distinct outputs")
            for output, coefficient in observation.terms:
                if not 0 <= output < len(spaces) or coefficient.shape != (bins,) or not np.isfinite(coefficient).all():
                    raise ValueError("Invalid routing observation term")
        self.base_weights = np.asarray([observation.weight for observation in observations], dtype=float)
        self.base_weights /= self.base_weights.sum()

    def native_spectra(self, taps):
        return [rfft(space.expand(taps[where]), space.nfft) for where, space in zip(self.slices, self.spaces)]

    def expanded_spectra(self, taps):
        arrays = []
        offset = 0
        for space in self.spaces:
            value = np.zeros(self.nfft // 2 + 1, dtype=complex)
            own = rfft(taps[offset:offset+space.length], space.nfft)
            value[:len(own)] = own
            arrays.append(value)
            offset += space.length
        if offset != len(taps):
            raise ValueError("Expanded FIR vector length mismatch")
        return arrays

    def regularization_apply(self, values):
        return np.concatenate([space.adjoint(space.regularization * space.expand(values[where]))
                               + (space.parameter_ridge * values[where] if space.realization is not None else 0)
                               for space, where in zip(self.spaces, self.slices)])

    def realization_normal(self, values, spectral_weights=None):
        if spectral_weights is None:
            return np.concatenate([space.adjoint(space.expand(values[where])) for space, where in zip(self.spaces, self.slices)])
        return np.concatenate([space.adjoint(irfft(weight*rfft(space.expand(values[where]),space.nfft),space.nfft)[:space.length])
                               for space,where,weight in zip(self.spaces,self.slices,spectral_weights)])

    def common_spectra(self, taps):
        arrays = []
        for spectrum in self.native_spectra(taps):
            value = np.zeros(self.nfft // 2 + 1, dtype=complex)
            value[:len(spectrum)] = spectrum
            arrays.append(value)
        return arrays

    def spectrum_adjoint(self, values, output):
        space = self.spaces[output]
        part = np.asarray(values[:space.nfft // 2 + 1], dtype=complex).copy()
        if space.nfft != self.nfft:
            # The native Nyquist bin is an interior bin on the common grid.
            part[-1] *= 2
        return space.adjoint(irfft(part, space.nfft)[:space.length] * (space.nfft / self.nfft))

    def predict(self, taps):
        filters = self.common_spectra(taps)
        return [sum(coefficient * filters[output] for output, coefficient in observation.terms)
                for observation in self.observations]

    def normal_statistics(self, weights):
        gram = {}
        rhs = [np.zeros(self.nfft // 2 + 1, dtype=complex) for _ in self.spaces]
        for scalar, observation in zip(weights, self.observations):
            w = scalar * observation.frequency_weight
            for output, coefficient in observation.terms:
                rhs[output] += np.conj(coefficient) * w * observation.target
                for other, second in observation.terms:
                    key = (output, other)
                    if key not in gram:
                        gram[key] = np.zeros(self.nfft // 2 + 1, dtype=complex)
                    gram[key] += np.conj(coefficient) * second * w
        vector = np.concatenate([self.spectrum_adjoint(value, i) for i, value in enumerate(rhs)])
        return gram, vector

    def normal_apply(self, taps, gram):
        filters = self.common_spectra(taps)
        values = [np.zeros(self.nfft // 2 + 1, dtype=complex) for _ in self.spaces]
        for (output, other), coefficient in gram.items():
            values[output] += coefficient * filters[other]
        return np.concatenate([self.spectrum_adjoint(value, i) for i, value in enumerate(values)])

    def observation_losses(self, taps):
        filters = self.common_spectra(taps)
        losses = []
        for observation in self.observations:
            response = sum(coefficient * filters[output] for output, coefficient in observation.terms)
            losses.append(parseval_energy((response - observation.target) * np.sqrt(observation.frequency_weight), self.nfft))
        return np.asarray(losses)

    def risk(self, taps, risk_weight=.35, temperature=.15):
        losses = self.observation_losses(taps)
        positions = sorted({observation.position for observation in self.observations})
        means, masses = [], []
        for position in positions:
            indices = np.asarray([observation.position == position for observation in self.observations])
            mass = float(self.base_weights[indices].sum())
            means.append(float(np.dot(losses[indices], self.base_weights[indices]) / mass))
            masses.append(mass)
        means, masses = np.asarray(means), np.asarray(masses)
        mean = float(np.dot(means, masses))
        soft_worst = temperature * logsumexp(means / temperature, b=masses)
        regularizer = sum(float(np.dot(space.regularization, (space.expand(taps[where]) - space.prior) ** 2))
                          + (space.parameter_ridge * float(np.sum((taps[where] - space.prior_parameters)**2)) if space.realization is not None else 0)
                          for space, where in zip(self.spaces, self.slices))
        return float((1 - risk_weight) * mean + risk_weight * soft_worst + regularizer), dict(zip(positions, means.tolist()))

    def risk_weights(self, taps, risk_weight, temperature):
        _, losses = self.risk(taps, risk_weight, temperature)
        positions = sorted(losses)
        mass = np.asarray([sum(w for w, observation in zip(self.base_weights, self.observations) if observation.position == p) for p in positions])
        log_scale = np.asarray([losses[p] for p in positions]) / temperature
        scale = np.exp(log_scale - np.max(log_scale))
        scale /= float(np.dot(mass, scale))
        factors = {p: (1 - risk_weight) + risk_weight * value for p, value in zip(positions, scale)}
        return self.base_weights * np.asarray([factors[observation.position] for observation in self.observations])

    def bound_violation_db(self, taps):
        violations = []
        for values, space in zip(self.native_spectra(taps), self.spaces):
            magnitude = np.maximum(np.abs(values), 1e-30)
            with np.errstate(divide="ignore"):
                lower_error = 20 * np.log10(space.lower / magnitude)
            violations.append(max(float(np.max(20 * np.log10(magnitude / space.upper))), float(np.max(lower_error)), 0.0))
        return max(violations)

    def bound_diagnostics(self, taps):
        rows=[]
        for values,space in zip(self.native_spectra(taps),self.spaces):
            magnitude=np.maximum(np.abs(values),1e-30)
            with np.errstate(divide="ignore"):
                lower_error=20*np.log10(space.lower/magnitude)
            upper_error=20*np.log10(magnitude/space.upper)
            error=np.maximum(lower_error,upper_error)
            index=int(np.argmax(error))
            rows.append({"channel":space.channel,"frequency_hz":index*space.sample_rate/space.nfft,
                         "violation_db":max(0.,float(error[index])),
                         "bound":"lower" if lower_error[index]>upper_error[index] else "upper"})
        return rows

    def feasible_line_candidate(self, anchor, proposal, risk_weight, temperature, tolerance_db):
        """Optimize a full-dimensional direction over every feasible line interval.

        Upper gain disks bound the line. Lower magnitude bounds remove a union
        of open intervals, which is retained rather than approximated as one
        convex interval. Every native FFT bin participates in the calculation.
        """
        if self.bound_violation_db(anchor) > tolerance_db + 1e-8:
            raise ValueError("A feasible line search needs a feasible anchor")
        direction = proposal - anchor
        starts, ends = [], []
        maximum = 1.0
        for base, delta, space in zip(self.native_spectra(anchor), self.native_spectra(direction), self.spaces):
            a = np.abs(delta)**2
            b = 2*np.real(np.conj(base)*delta)
            active = a > 1e-24
            upper = space.upper * 10**(tolerance_db/20)
            c = np.abs(base)**2 - upper**2
            discriminant = np.maximum(b*b-4*a*c,0)
            root = np.full(len(a),np.inf)
            root[active] = (-b[active]+np.sqrt(discriminant[active]))/(2*a[active])
            maximum = min(maximum,float(np.min(root[active])) if active.any() else 1)
            lower = space.lower * 10**(-tolerance_db/20)
            c = np.abs(base)**2-lower**2
            discriminant = b*b-4*a*c
            selected = active & (discriminant>0) & (lower>0)
            first=(-b[selected]-np.sqrt(discriminant[selected]))/(2*a[selected])
            second=(-b[selected]+np.sqrt(discriminant[selected]))/(2*a[selected])
            keep=(second>0)&(first<1)
            starts.extend(first[keep]);ends.extend(second[keep])
        maximum=float(np.clip(maximum,0,1))
        if maximum<1e-12:
            return anchor.copy(),0.
        intervals=[]
        if starts:
            start=np.clip(np.asarray(starts),0,maximum);end=np.clip(np.asarray(ends),0,maximum)
            keep=end>start
            start,end=start[keep],end[keep]
            order=np.argsort(start);start,end=start[order],end[order]
            cursor=0.
            for a,b in zip(start,end):
                if a>cursor:intervals.append((cursor,float(a)))
                cursor=max(cursor,float(b))
            if cursor<maximum:intervals.append((cursor,maximum))
        else:
            intervals=[(0.,maximum)]
        if not intervals:
            return anchor.copy(),0.
        objective=self.risk_line(anchor,direction,risk_weight,temperature)
        optimum=minimize_scalar(objective,bounds=(0,maximum),method="bounded",options={"xatol":1e-5}).x
        choices={0.}
        for begin,end in intervals:
            choices.add(float(np.clip(optimum,begin+min(1e-8,(end-begin)/4),end-min(1e-8,(end-begin)/4))))
        alpha=min(choices,key=objective)
        result=anchor+alpha*direction
        if self.bound_violation_db(result)>tolerance_db+1e-6:
            return anchor.copy(),0.
        return result,alpha

    def risk_line(self,anchor,direction,risk_weight,temperature):
        """Exact quadratic sufficient statistics for a full FIR direction."""
        base=self.common_spectra(anchor);delta=self.common_spectra(direction)
        coefficients=defaultdict(lambda:np.zeros(3))
        masses=defaultdict(float)
        for observation,weight in zip(self.observations,self.base_weights):
            residual=sum(h*base[i] for i,h in observation.terms)-observation.target
            change=sum(h*delta[i] for i,h in observation.terms)
            w=observation.frequency_weight
            power=np.real(np.conj(residual)*change)*w
            cross=float((2*np.sum(power)-power[0]-power[-1])/self.nfft)
            coefficients[observation.position]+=weight*np.array([parseval_energy(change*np.sqrt(w),self.nfft),2*cross,
                                                                 parseval_energy(residual*np.sqrt(w),self.nfft)])
            masses[observation.position]+=weight
        positions=sorted(masses)
        mass=np.array([masses[p] for p in positions])
        terms=np.array([coefficients[p]/masses[p] for p in positions])
        regularizer=np.zeros(3)
        for space,where in zip(self.spaces,self.slices):
            change=space.expand(direction[where]);residual=space.expand(anchor[where])-space.prior
            regularizer+=np.array([np.dot(space.regularization,change**2),2*np.dot(space.regularization,residual*change),
                                  np.dot(space.regularization,residual**2)])
            if space.realization is not None:
                a=anchor[where]-space.prior_parameters;d=direction[where]
                regularizer+=space.parameter_ridge*np.array([d@d,2*(a@d),a@a])
        def objective(alpha):
            basis=np.array([alpha*alpha,alpha,1])
            means=terms@basis
            return float((1-risk_weight)*(mass@means)+risk_weight*temperature*logsumexp(means/temperature,b=mass)+regularizer@basis)
        return objective

    def feasible_block_candidate(self,anchor,proposal,risk_weight,temperature,tolerance_db):
        current=anchor.copy()
        for where in self.slices:
            direction=current.copy();direction[where]=proposal[where]
            current,_=self.feasible_line_candidate(current,direction,risk_weight,temperature,tolerance_db)
        return current

    def solve(self, *, iterations=60, cg_iterations=100, cg_tolerance=1e-6,
              risk_weight=.35, risk_temperature=.15, regularization_rounds=3,
              rho=.3, bound_tolerance_db=.15, progress=None, cancel=None, initial=None,
              lower_bound_method="supporting_halfplanes",penalty_metric="uniform"):
        if not 1 <= iterations <= 500 or not 1 <= cg_iterations <= 1000:
            raise ValueError("Invalid joint solver iteration budget")
        if not 0 <= risk_weight <= 1 or not np.isfinite(risk_temperature) or risk_temperature <= 0:
            raise ValueError("Invalid position-risk settings")
        if not 1 <= regularization_rounds <= 12 or not 0 < rho <= 100 or not 0 < cg_tolerance < 1:
            raise ValueError("Invalid joint solver tolerance or regularization")
        if lower_bound_method not in ["annulus", "supporting_halfplanes"]:
            raise ValueError("Unknown lower magnitude bound method")
        if penalty_metric not in ["uniform","data_diagonal"]:
            raise ValueError("Unknown ADMM penalty metric")
        x = self.prior.copy() if initial is None else np.asarray(initial,dtype=float).copy()
        if x.shape!=(self.size,) or not np.isfinite(x).all():
            raise ValueError("Initial joint parameters do not match the complete problem")
        z = [project_annulus(value, space.lower, space.upper) for value, space in zip(self.native_spectra(x), self.spaces)]
        dual = [np.zeros_like(value) for value in z]
        best = self.identity.copy()
        if self.bound_violation_db(best)>bound_tolerance_db+1e-7:
            if self.bound_violation_db(x)>bound_tolerance_db+1e-7:
                raise ValueError("The neutral and initial filters violate the gain bounds; a feasible anchor is required")
            best=x.copy()
        best_score, _ = self.risk(best, risk_weight, risk_temperature)
        if self.bound_violation_db(x) <= bound_tolerance_db:
            score, _ = self.risk(x, risk_weight, risk_temperature)
            if score < best_score:
                best, best_score = x.copy(), score
        elif initial is not None:
            candidate,_=self.feasible_line_candidate(best,x,risk_weight,risk_temperature,bound_tolerance_db)
            candidate_score,_=self.risk(candidate,risk_weight,risk_temperature)
            if candidate_score<best_score:best,best_score=candidate,candidate_score
        phase_anchors = self.native_spectra(best)
        def project(values, space, anchor):
            return (project_annulus(values, space.lower, space.upper) if lower_bound_method=="annulus"
                    else project_supported_disk(values, space.lower, space.upper, anchor))
        z = [project(value,space,anchor) for value,space,anchor in zip(self.native_spectra(x),self.spaces,phase_anchors)]
        weights = self.risk_weights(x, risk_weight, risk_temperature) if risk_weight else self.base_weights.copy()
        gram, data_rhs = self.normal_statistics(weights)
        # A scalar time-domain penalty underweights a few high-power bass bins.
        # Use a positive diagonal metric on EVERY native frequency bin. Its
        # scalar-per-bin weight leaves the disk/half-plane projection exact.
        # Keep this metric fixed across risk and phase-anchor subproblems.
        penalty_weights=[]
        for i,space in enumerate(self.spaces):
            diagonal=np.real(gram.get((i,i),np.zeros(self.nfft//2+1)))[:space.nfft//2+1]*space.nfft/self.nfft
            penalty_weights.append(np.ones_like(diagonal) if penalty_metric=="uniform" else
                                   np.maximum(diagonal,max(float(np.mean(space.regularization)),float(np.max(diagonal))*1e-6,1e-8)))
        history = []
        total_cg = 0
        converged = False
        statistics_version=0
        cached_block_key=None
        cached_inverse=None
        use_blocks=all(space.realization is None and space.nfft==self.nfft for space in self.spaces)
        for iteration in range(iterations):
            if cancel is not None and cancel.is_set():
                raise InterruptedError("Joint FIR optimization cancelled")
            if risk_weight and iteration and iteration % max(1, iterations // regularization_rounds) == 0:
                proposed = self.risk_weights(best, risk_weight, risk_temperature)
                weights = .5 * weights + .5 * proposed
                gram, data_rhs = self.normal_statistics(weights)
                statistics_version+=1
                if lower_bound_method=="supporting_halfplanes":
                    phase_anchors=self.native_spectra(best)
                    # Warm-start the new convex subproblem, including its
                    # scaled multipliers; feasibility is checked on real taps.
            rhs = data_rhs + self.regularization_apply(self.prior)
            rhs += rho * np.concatenate([space.adjoint(irfft(weight*(value-u),space.nfft)[:space.length])
                                         for value,u,space,weight in zip(z,dual,self.spaces,penalty_weights)])
            operator = LinearOperator((self.size, self.size), dtype=float,
                                      matvec=lambda v: self.normal_apply(v, gram) + self.regularization_apply(v) + rho * self.realization_normal(v,penalty_weights))
            denominators = []
            for i, (space, where) in enumerate(zip(self.spaces, self.slices)):
                diagonal = np.real(gram.get((i, i), np.zeros(self.nfft // 2 + 1)))[:space.nfft // 2 + 1]
                if space.realization is None:
                    denominator = diagonal * space.nfft / self.nfft + np.mean(space.regularization) + rho*penalty_weights[i]
                else:
                    q = space.regularization + rho*np.mean(penalty_weights[i]) + np.mean(diagonal) * space.nfft / self.nfft
                    denominator = np.asarray(space.realization.power(2).T @ q).ravel() + space.parameter_ridge
                denominators.append(np.maximum(denominator, 1e-10))
            def precondition(value):
                if use_blocks:
                    transformed=np.asarray([rfft(value[where],space.nfft) for where,space in zip(self.slices,self.spaces)])
                    transformed=np.einsum("fij,jf->if",cached_inverse,transformed,optimize=True)
                    return np.concatenate([irfft(transformed[i],space.nfft)[:space.length] for i,space in enumerate(self.spaces)])
                return np.concatenate([irfft(rfft(value[where], space.nfft) / denominator, space.nfft)[:space.length]
                                       if space.realization is None else value[where] / denominator
                                       for where, space, denominator in zip(self.slices, self.spaces, denominators)])
            if use_blocks and cached_block_key!=(statistics_version,rho):
                symbol=np.zeros((self.nfft//2+1,len(self.spaces),len(self.spaces)),dtype=complex)
                for (i,j),value in gram.items():symbol[:,i,j]=value
                for i,space in enumerate(self.spaces):symbol[:,i,i]+=np.mean(space.regularization)+rho*penalty_weights[i]
                cached_inverse=np.linalg.inv(symbol)
                cached_block_key=(statistics_version,rho)
            preconditioner = LinearOperator((self.size, self.size), dtype=float, matvec=precondition)
            inner = [0]
            def callback(_):
                if cancel is not None and cancel.is_set():
                    raise InterruptedError("Joint FIR optimization cancelled")
                inner[0] += 1
            x, info = cg(operator, rhs, x0=x, M=preconditioner, rtol=cg_tolerance,
                         atol=0, maxiter=cg_iterations, callback=callback)
            total_cg += inner[0]
            if info < 0 or not np.isfinite(x).all():
                raise ValueError("Joint FIR normal-equation solve failed")
            actual = self.native_spectra(x)
            previous = z
            z = [project(value+u,space,anchor)
                 for value,u,space,anchor in zip(actual,dual,self.spaces,phase_anchors)]
            dual = [u + value - projected for u, value, projected in zip(dual, actual, z)]
            primal = np.sqrt(sum(parseval_energy(np.sqrt(weight)*(value-projected),space.nfft) for value,projected,space,weight in zip(actual,z,self.spaces,penalty_weights)))
            dual_residual = rho*np.sqrt(sum(parseval_energy(np.sqrt(weight)*(a-b),space.nfft) for a,b,space,weight in zip(z,previous,self.spaces,penalty_weights)))
            violation = self.bound_violation_db(x)
            score, position_losses = self.risk(x, risk_weight, risk_temperature)
            if violation <= bound_tolerance_db and score < best_score:
                best, best_score = x.copy(), score
            elif iteration==0 or (iteration+1)%5==0:
                candidate,alpha=self.feasible_line_candidate(best,x,risk_weight,risk_temperature,bound_tolerance_db)
                candidate_score,_=self.risk(candidate,risk_weight,risk_temperature)
                if candidate_score<best_score:
                    best,best_score=candidate,candidate_score
            if (iteration+1)%10==0:
                candidate=self.feasible_block_candidate(best,x,risk_weight,risk_temperature,bound_tolerance_db)
                candidate_score,_=self.risk(candidate,risk_weight,risk_temperature)
                if candidate_score<best_score:best,best_score=candidate,candidate_score
            history.append({"iteration": iteration + 1, "risk_objective": score,
                            "position_losses": position_losses, "bound_violation_db": violation,
                            "primal_residual": float(primal), "dual_residual": float(dual_residual),
                            "cg_iterations": inner[0], "cg_converged": info == 0, "rho": rho})
            if progress:
                progress(round(10 + 75 * (iteration + 1) / iterations),
                         f"Joint FIR iteration {iteration + 1}/{iterations}; bound gap {violation:.3f} dB")
            final_risk_round = (regularization_rounds - 1) * max(1, iterations // regularization_rounds) if risk_weight else 0
            if iteration >= max(9, final_risk_round + 5) and violation<=bound_tolerance_db and primal < 1e-5 * np.sqrt(len(self.spaces)) and dual_residual < 1e-5 * np.sqrt(len(self.spaces)) and info == 0:
                converged = True
                break
            if primal > 10 * max(dual_residual, 1e-14) and rho < 10:
                rho *= 2
                dual = [value / 2 for value in dual]
            elif dual_residual > 10 * max(primal, 1e-14) and rho > 1e-3:
                rho /= 2
                dual = [value * 2 for value in dual]
        identity_score, _ = self.risk(self.identity, risk_weight, risk_temperature)
        prior_score, _ = self.risk(self.prior, risk_weight, risk_temperature)
        self.last_iterate=x.copy()
        return best, {"algorithm": "full_space_native_routing_admm_cg", "optimization_variables": self.size,
                      "expanded_fir_taps": sum(space.length for space in self.spaces),
                      "output_filters": len(self.spaces), "observations": len(self.observations),
                      "common_fft_size": self.nfft, "iterations": len(history), "cg_iterations": total_cg,
                      "preconditioner":"full_complex_frequency_blocks" if use_blocks else "native_parameter_diagonal",
                      "numerically_converged": converged, "actual_bound_violation_db": self.bound_violation_db(best),
                      "risk_objective": best_score, "identity_risk_objective": identity_score,
                      "regularization_prior_risk_objective": prior_score,
                      "improved_over_identity": bool(best_score < identity_score - 1e-9),
                      "gain_constraints_satisfied": self.bound_violation_db(best) <= bound_tolerance_db+1e-7,
                      "gain_bound_tolerance_db":bound_tolerance_db,
                      "gain_constraint_domain":"every_native_fft_bin; continuous_frequency_certification_not_claimed",
                      "risk_weight": risk_weight, "risk_temperature": risk_temperature, "history": history,
                      "lower_bound_method":lower_bound_method,
                      "admm_penalty_metric":penalty_metric,
                      "full_tap_feasible_block_refinement":True,
                      "returned_bound_diagnostics":self.bound_diagnostics(best),
                      "last_iterate_bound_diagnostics":self.bound_diagnostics(x),
                      "global_optimality_claimed": False, "native_hardware_verified": False,
                      "notice": "All native FIR taps and observation cross terms are retained. Annular magnitude constraints are nonconvex; numerical convergence is not global optimality or hardware validation."}

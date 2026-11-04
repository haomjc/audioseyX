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
    selected = (lower>0) & (clipped.real < lower)
    clipped[selected] = lower[selected] + 1j*np.clip(rotated.imag[selected],
        -np.sqrt(np.maximum(upper[selected]**2-lower[selected]**2, 0)),
        np.sqrt(np.maximum(upper[selected]**2-lower[selected]**2, 0)))
    result = clipped * phase
    result[0] = result[0].real
    result[-1] = result[-1].real
    return result


class FrequencyBlockInverse:
    """Exact symbol inverse, exploiting only proven zero main/main blocks."""
    def __init__(self,spaces,gram,rho,penalty_weights):
        self.count=len(spaces);bins=spaces[0].nfft//2+1
        self.mains=[i for i,s in enumerate(spaces) if not s.channel.startswith('SW')]
        self.subs=[i for i,s in enumerate(spaces) if s.channel.startswith('SW')]
        diagonal=np.array([np.real(gram.get((i,i),np.zeros(bins)))+np.mean(s.regularization)+rho*penalty_weights[i]
                           for i,s in enumerate(spaces)])
        main_set=set(self.mains)
        arrow=not any(i!=j and i in main_set and j in main_set and np.any(value)
                      for (i,j),value in gram.items())
        if not arrow:
            symbol=np.zeros((bins,self.count,self.count),dtype=complex)
            for (i,j),value in gram.items():symbol[:,i,j]=value
            for i in range(self.count):symbol[:,i,i]=diagonal[i]
            self.inverse=np.linalg.inv(symbol);self.kind='full_complex_frequency_blocks'
            return
        self.kind='exact_main_sub_schur_blocks' if self.subs else 'diagonal_frequency_blocks'
        self.main_inverse=1/diagonal[self.mains]
        if not self.subs:return
        subcount=len(self.subs)
        self.coupling=np.zeros((len(self.mains),subcount,bins),dtype=complex)
        for m,i in enumerate(self.mains):
            for s,j in enumerate(self.subs):self.coupling[m,s]=gram.get((i,j),0)
        schur=np.zeros((bins,subcount,subcount),dtype=complex)
        for a,i in enumerate(self.subs):
            for b,j in enumerate(self.subs):schur[:,a,b]=gram.get((i,j),0)
            schur[:,a,a]=diagonal[i]
        if self.mains:
            schur-=np.einsum('msf,mf,mtf->fst',self.coupling.conj(),self.main_inverse,self.coupling,optimize=True)
        self.schur_inverse=np.linalg.inv(schur)

    def apply(self,values):
        if self.kind=='full_complex_frequency_blocks':
            return np.einsum('fij,jf->if',self.inverse,values,optimize=True)
        result=np.zeros_like(values)
        if not self.subs:
            result[self.mains]=self.main_inverse*values[self.mains]
            return result
        rhs=values[self.subs].copy()
        if self.mains:
            rhs-=np.einsum('msf,mf->sf',self.coupling.conj(),self.main_inverse*values[self.mains],optimize=True)
        result[self.subs]=np.einsum('fst,tf->sf',self.schur_inverse,rhs,optimize=True)
        if self.mains:
            result[self.mains]=self.main_inverse*(values[self.mains]-np.einsum('msf,sf->mf',self.coupling,result[self.subs],optimize=True))
        return result


class AndersonHistory:
    """Small safeguarded multisecant history; all state coordinates remain."""
    def __init__(self,depth):
        self.depth=depth;self.inputs=[];self.outputs=[]

    def clear(self):self.inputs.clear();self.outputs.clear()

    def proposal(self,value,mapped):
        self.inputs.append(value.copy());self.outputs.append(mapped.copy())
        if len(self.inputs)>self.depth+1:self.inputs.pop(0);self.outputs.pop(0)
        if len(self.inputs)<2:return None
        residual=[b-a for a,b in zip(self.inputs,self.outputs)]
        df=np.array([residual[i+1]-residual[i] for i in range(len(residual)-1)])
        dg=np.array([self.outputs[i+1]-self.outputs[i] for i in range(len(residual)-1)])
        matrix=df@df.T
        ridge=max(float(np.trace(matrix))*1e-10,1e-20)
        try:gamma=np.linalg.solve(matrix+np.eye(len(matrix))*ridge,df@residual[-1])
        except np.linalg.LinAlgError:return None
        if not np.isfinite(gamma).all() or np.linalg.norm(gamma)>1000:return None
        candidate=mapped-gamma@dg
        return candidate if np.isfinite(candidate).all() else None


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
            if scalar==0:continue
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
        return self.normal_from_spectra(filters,gram)

    def normal_from_spectra(self,filters,gram):
        values = [np.zeros(self.nfft // 2 + 1, dtype=complex) for _ in self.spaces]
        for (output, other), coefficient in gram.items():
            values[output] += coefficient * filters[other]
        return np.concatenate([self.spectrum_adjoint(value, i) for i, value in enumerate(values)])

    def position_quadratics(self):
        positions=sorted({o.position for o in self.observations});result=[]
        for position in positions:
            selected=np.array([o.position==position for o in self.observations])
            mass=float(np.sum(self.base_weights[selected]));weights=self.base_weights*selected/mass
            gram,rhs=self.normal_statistics(weights)
            constant=sum(weight*parseval_energy(o.target*np.sqrt(o.frequency_weight),self.nfft)
                         for weight,o in zip(weights,self.observations) if weight)
            result.append({'position':position,'mass':mass,'gram':gram,'rhs':rhs,'constant':constant})
        return result

    def risk_curvature(self,x,statistics,risk_weight,temperature):
        """Exact data gradient and PSD covariance correction to half-Hessian.

        If l_p=x' A_p x-2 b_p' x+c_p and r_p=A_p x-b_p,
        H/2=sum(alpha_p A_p)+(2*risk/T)*Cov_soft(r_p).
        The covariance is explicitly centred, avoiding subtractive PSD loss.
        """
        spectra=self.common_spectra(x);residuals=[];losses=[]
        for row in statistics:
            applied=self.normal_from_spectra(spectra,row['gram'])
            residuals.append(applied-row['rhs'])
            losses.append(max(0.,float(x@applied-2*x@row['rhs']+row['constant'])))
        losses=np.asarray(losses);residuals=np.asarray(residuals)
        mass=np.array([row['mass'] for row in statistics])
        soft=mass*np.exp((losses-np.max(losses))/temperature);soft/=soft.sum()
        alpha=(1-risk_weight)*mass+risk_weight*soft
        gram={};rhs=np.zeros(self.size)
        for weight,row in zip(alpha,statistics):
            rhs+=weight*row['rhs']
            for key,value in row['gram'].items():
                if key not in gram:gram[key]=np.zeros_like(value)
                gram[key]+=weight*value
        centred=residuals-soft@residuals
        low_rank=np.sqrt(2*risk_weight*soft/temperature)[:,None]*centred
        return {'data_value':float((1-risk_weight)*(mass@losses)+risk_weight*temperature*logsumexp(losses/temperature,b=mass)),
                'gradient_half':alpha@residuals,'gram':gram,'rhs':rhs,'low_rank':low_rank,
                'position_losses':dict(zip([row['position'] for row in statistics],losses.tolist()))}

    def _risk_linear_step(self,gram,rhs,rho,metric,low_rank,cg_iterations,cg_tolerance,cancel):
        blocks=all(space.realization is None and space.nfft==self.nfft for space in self.spaces)
        inverse=FrequencyBlockInverse(self.spaces,gram,rho,metric) if blocks else None
        diagonals=[]
        if not blocks:
            for i,space in enumerate(self.spaces):
                diagonal=np.real(gram.get((i,i),np.zeros(self.nfft//2+1)))[:space.nfft//2+1]
                if space.realization is None:
                    value=diagonal*space.nfft/self.nfft+np.mean(space.regularization)+rho*metric[i]
                else:
                    q=space.regularization+rho*np.mean(metric[i])+np.mean(diagonal)*space.nfft/self.nfft
                    value=np.asarray(space.realization.power(2).T@q).ravel()+space.parameter_ridge
                diagonals.append(np.maximum(value,1e-12))
        def apply(v):
            return self.normal_apply(v,gram)+self.regularization_apply(v)+rho*self.realization_normal(v,metric)+low_rank.T@(low_rank@v)
        def precondition(v):
            if blocks:
                transformed=np.array([rfft(v[where],space.nfft) for where,space in zip(self.slices,self.spaces)])
                transformed=inverse.apply(transformed)
                return np.concatenate([irfft(transformed[i],space.nfft)[:space.length] for i,space in enumerate(self.spaces)])
            return np.concatenate([irfft(rfft(v[where],space.nfft)/diagonal,space.nfft)[:space.length]
                                   if space.realization is None else v[where]/diagonal
                                   for where,space,diagonal in zip(self.slices,self.spaces,diagonals)])
        count=[0]
        def callback(_):
            count[0]+=1
            if cancel is not None and cancel.is_set():raise InterruptedError('Joint risk solve cancelled')
        step,info=cg(LinearOperator((self.size,self.size),matvec=apply,dtype=float),rhs,
                     M=LinearOperator((self.size,self.size),matvec=precondition,dtype=float),
                     rtol=cg_tolerance,atol=0,maxiter=cg_iterations,callback=callback)
        if info<0 or not np.isfinite(step).all():raise ValueError('Exact risk Newton-CG failed')
        return step,info,count[0],inverse.kind if blocks else 'native_parameter_diagonal'

    def risk_x_step(self,x,z,dual,rho,metric,statistics,risk_weight,temperature,cg_iterations,cg_tolerance,
                    max_steps,tolerance,cancel=None):
        penalty_rhs=rho*np.concatenate([space.adjoint(irfft(weight*(value-u),space.nfft)[:space.length])
                                       for space,weight,value,u in zip(self.spaces,metric,z,dual)])
        total_cg=0;last_info=0;kind='';converged=False
        for iteration in range(max_steps):
            if cancel is not None and cancel.is_set():raise InterruptedError('Joint risk solve cancelled')
            curvature=self.risk_curvature(x,statistics,risk_weight,temperature)
            gradient=curvature['gradient_half']+self.regularization_apply(x-self.prior)+rho*self.realization_normal(x,metric)-penalty_rhs
            step,last_info,count,kind=self._risk_linear_step(curvature['gram'],-gradient,rho,metric,curvature['low_rank'],cg_iterations,cg_tolerance,cancel)
            total_cg+=count
            if np.linalg.norm(step)<=tolerance*max(1.,np.linalg.norm(x)) and last_info==0:
                converged=True;break
            direction=float(2*gradient@step)
            if direction>=0:break
            line=self.risk_line(x,step,risk_weight,temperature)
            base=self.native_spectra(x);change=self.native_spectra(step)
            quadratic=np.zeros(3)
            for a,b,u,value,weight,space in zip(base,change,dual,z,metric,self.spaces):
                residual=a-value+u
                cross=np.real(np.conj(residual)*b)*weight
                cross=float((2*np.sum(cross)-cross[0]-cross[-1])/space.nfft)
                quadratic+=np.array([parseval_energy(np.sqrt(weight)*b,space.nfft),2*cross,
                                     parseval_energy(np.sqrt(weight)*residual,space.nfft)])
            def objective(alpha):return line(alpha)+rho*float(quadratic@np.array([alpha*alpha,alpha,1]))
            initial=objective(0);alpha=1.
            for _ in range(24):
                if objective(alpha)<=initial+1e-4*alpha*direction:
                    x=x+alpha*step;break
                alpha*=.5
            else:break
        return x,converged,total_cg,last_info,kind,iteration+1

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
              lower_bound_method="supporting_halfplanes",penalty_metric="uniform",
              over_relaxation=1.,penalty_adaptation='rms',exact_spatial_risk=True,
              risk_inner_iterations=6,risk_inner_tolerance=1e-7,anderson_depth=0):
        if not 1 <= iterations <= 500 or not 1 <= cg_iterations <= 1000:
            raise ValueError("Invalid joint solver iteration budget")
        if not 0 <= risk_weight <= 1 or not np.isfinite(risk_temperature) or risk_temperature <= 0:
            raise ValueError("Invalid position-risk settings")
        if not 1 <= regularization_rounds <= 12 or not 0 < rho <= 100 or not 0 < cg_tolerance < 1:
            raise ValueError("Invalid joint solver tolerance or regularization")
        if lower_bound_method not in ["annulus", "supporting_halfplanes"]:
            raise ValueError("Unknown lower magnitude bound method")
        if penalty_metric not in ["uniform","data_diagonal","root_data_diagonal"]:
            raise ValueError("Unknown ADMM penalty metric")
        if not np.isfinite(bound_tolerance_db) or not 0<=bound_tolerance_db<=1:
            raise ValueError("Gain-bound numerical envelope must be 0-1 dB")
        if not 0<over_relaxation<2 or penalty_adaptation not in ['rms','relative_max','fixed']:
            raise ValueError('Invalid ADMM relaxation or penalty-adaptation method')
        if not isinstance(exact_spatial_risk,bool) or not 1<=risk_inner_iterations<=30 or not 0<risk_inner_tolerance<1:
            raise ValueError('Invalid spatial-risk inner-solve controls')
        if not isinstance(anderson_depth,int) or not 0<=anderson_depth<=8:raise ValueError('Anderson history depth must be 0-8')
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
        # The anchor and projection MUST describe the same feasible set.
        # Previously, accepted anchors could use the declared dB envelope,
        # while their supporting planes enforced stricter bounds. A finite
        # FIR with the anchor phase could then have no feasible realization.
        envelope=10**(bound_tolerance_db/20)
        projection_bounds={id(space):(space.lower/envelope,space.upper*envelope) for space in self.spaces}
        def project(values, space, anchor):
            lower,upper=projection_bounds[id(space)]
            return (project_annulus(values,lower,upper) if lower_bound_method=="annulus"
                    else project_supported_disk(values,lower,upper,anchor))
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
            base_metric=np.maximum(diagonal,max(float(np.mean(space.regularization)),float(np.max(diagonal))*1e-6,1e-8))
            penalty_weights.append(np.ones_like(diagonal) if penalty_metric=="uniform" else
                                   np.clip(np.sqrt(base_metric),.1,32) if penalty_metric=='root_data_diagonal' else base_metric)
        history = []
        positions={o.position for o in self.observations}
        risk_cache=self.position_quadratics() if exact_spatial_risk and risk_weight and len(positions)>1 else None
        total_cg = 0
        converged = False
        statistics_version=0
        cached_block_key=None
        cached_inverse=None
        use_blocks=all(space.realization is None and space.nfft==self.nfft for space in self.spaces)
        preconditioner_name='native_parameter_diagonal'
        phase_period=max(1,iterations//regularization_rounds)
        update_iterations={phase_period*index for index in range(1,regularization_rounds) if phase_period*index<iterations}
        final_update=max(update_iterations,default=0)
        update_count=0
        accelerator=AndersonHistory(anderson_depth) if anderson_depth else None
        acceleration_key=None;pending_acceleration=None;acceleration_attempts=0;acceleration_rejected=0
        scales=[]
        for space,weight in zip(self.spaces,penalty_weights):
            quadrature=np.full(len(weight),2/space.nfft);quadrature[[0,-1]]=1/space.nfft
            scales.append(np.sqrt(weight*quadrature))
        def pack_state(arrays):
            return np.concatenate([part for value,scale in zip(arrays,scales)
                                   for part in [(value*scale).real,(value*scale).imag]])
        def unpack_state(value):
            result=[];offset=0
            for scale in scales:
                count=len(scale);result.append((value[offset:offset+count]+1j*value[offset+count:offset+2*count])/scale)
                offset+=2*count
            return result
        def decompose_state(packed):
            state=unpack_state(packed)
            projected=[project(value,space,anchor) for value,space,anchor in zip(state,self.spaces,phase_anchors)]
            return projected,[value-point for value,point in zip(state,projected)]
        for iteration in range(iterations):
            if cancel is not None and cancel.is_set():
                raise InterruptedError("Joint FIR optimization cancelled")
            if iteration in update_iterations:
                update_count+=1
                if risk_weight and risk_cache is None:
                    proposed = self.risk_weights(best, risk_weight, risk_temperature)
                    weights = .5 * weights + .5 * proposed
                    gram, data_rhs = self.normal_statistics(weights)
                    statistics_version+=1
                if lower_bound_method=="supporting_halfplanes":
                    phase_anchors=self.native_spectra(best)
                    # Warm-start the new convex subproblem, including its
                    # scaled multipliers; feasibility is checked on real taps.
            if accelerator is not None:
                key=(update_count,rho)
                if key!=acceleration_key:
                    accelerator.clear();pending_acceleration=None;acceleration_key=key
                    z,dual=decompose_state(pack_state([point+u for point,u in zip(z,dual)]))
                # Accelerate the Douglas-Rachford state w=z+u. Extrapolating z
                # and u independently breaks z=projection(w), introducing
                # redundant state directions unrelated to the fixed point.
                state_before=pack_state([point+u for point,u in zip(z,dual)])
            risk_inner_converged=True
            risk_inner_steps=0
            if risk_cache is not None:
                x,risk_inner_converged,count,info,preconditioner_name,risk_inner_steps=self.risk_x_step(
                    x,z,dual,rho,penalty_weights,risk_cache,risk_weight,risk_temperature,
                    cg_iterations,cg_tolerance,risk_inner_iterations,risk_inner_tolerance,cancel)
                inner=[count]
            else:
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
                        transformed=cached_inverse.apply(transformed)
                        return np.concatenate([irfft(transformed[i],space.nfft)[:space.length] for i,space in enumerate(self.spaces)])
                    return np.concatenate([irfft(rfft(value[where], space.nfft) / denominator, space.nfft)[:space.length]
                                           if space.realization is None else value[where] / denominator
                                           for where, space, denominator in zip(self.slices, self.spaces, denominators)])
                if use_blocks and cached_block_key!=(statistics_version,rho):
                    cached_inverse=FrequencyBlockInverse(self.spaces,gram,rho,penalty_weights)
                    cached_block_key=(statistics_version,rho)
                preconditioner = LinearOperator((self.size, self.size), dtype=float, matvec=precondition)
                inner = [0]
                def callback(_):
                    if cancel is not None and cancel.is_set():
                        raise InterruptedError("Joint FIR optimization cancelled")
                    inner[0] += 1
                # Solve the remaining correction, not the large absolute RHS.
                # Otherwise CG can accept x unchanged once its residual falls
                # below rtol*||rhs||, even while ADMM still needs small updates.
                residual=rhs-operator.matvec(x)
                correction,info=cg(operator,residual,M=preconditioner,rtol=cg_tolerance,
                                    atol=0,maxiter=cg_iterations,callback=callback)
                x=x+correction
                if use_blocks:preconditioner_name=cached_inverse.kind
            total_cg += inner[0]
            if info < 0 or not np.isfinite(x).all():
                raise ValueError("Joint FIR normal-equation solve failed")
            actual = self.native_spectra(x)
            previous = z
            relaxed=[over_relaxation*value+(1-over_relaxation)*old for value,old in zip(actual,previous)]
            z = [project(value+u,space,anchor)
                 for value,u,space,anchor in zip(relaxed,dual,self.spaces,phase_anchors)]
            dual = [u + value - projected for u, value, projected in zip(dual, relaxed, z)]
            primal = np.sqrt(sum(parseval_energy(np.sqrt(weight)*(value-projected),space.nfft) for value,projected,space,weight in zip(actual,z,self.spaces,penalty_weights)))
            dual_residual = rho*np.sqrt(sum(parseval_energy(np.sqrt(weight)*(a-b),space.nfft) for a,b,space,weight in zip(z,previous,self.spaces,penalty_weights)))
            relative_primal=0.;relative_dual=0.
            for value,projected,old,space,weight in zip(actual,z,previous,self.spaces,penalty_weights):
                lower,upper=projection_bounds[id(space)]
                scale=np.maximum(lower,upper*.05)
                relative_primal=max(relative_primal,float(np.max(np.sqrt(weight)*np.abs(value-projected)/scale)))
                relative_dual=max(relative_dual,float(rho*np.max(np.sqrt(weight)*np.abs(projected-old)/scale)))
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
                            'maximum_relative_primal':relative_primal,'maximum_relative_dual':relative_dual,
                            "cg_iterations": inner[0], "cg_converged": info == 0, "rho": rho,
                            "risk_inner_converged":risk_inner_converged,"risk_inner_steps":risk_inner_steps})
            if progress:
                progress(round(10 + 75 * (iteration + 1) / iterations),
                         f"Joint FIR iteration {iteration + 1}/{iterations}; bound gap {violation:.3f} dB")
            if iteration >= max(9,final_update+5) and violation<=bound_tolerance_db+1e-7 and primal < 1e-5 * np.sqrt(len(self.spaces)) and dual_residual < 1e-5 * np.sqrt(len(self.spaces)) and info == 0 and risk_inner_converged:
                converged = True
                break
            rejected=False
            if accelerator is not None:
                mapped=pack_state([point+u for point,u in zip(z,dual)]);fixed_residual=float(np.linalg.norm(mapped-state_before))
                if pending_acceleration is not None and fixed_residual>1.2*pending_acceleration['residual']:
                    z,dual=decompose_state(pending_acceleration['fallback'])
                    x=pending_acceleration['x'].copy()
                    accelerator.clear();pending_acceleration=None;acceleration_rejected+=1;rejected=True
                else:
                    pending_acceleration=None
                    proposal=accelerator.proposal(state_before,mapped)
                    if proposal is not None:
                        pending_acceleration={'fallback':mapped,'x':x.copy(),'residual':fixed_residual}
                        z,dual=decompose_state(proposal);acceleration_attempts+=1
            balance_primal,balance_dual=(relative_primal,relative_dual) if penalty_adaptation=='relative_max' else (primal,dual_residual)
            adapt_now=not rejected and (penalty_adaptation=='rms' or (penalty_adaptation=='relative_max' and (iteration+1)%5==0 and iteration<iterations*.8))
            if adapt_now and balance_primal > 10 * max(balance_dual, 1e-14) and rho < 100:
                rho *= 2
                dual = [value / 2 for value in dual]
            elif adapt_now and balance_dual > 10 * max(balance_primal, 1e-14) and rho > 1e-3:
                rho /= 2
                dual = [value * 2 for value in dual]
        identity_score, _ = self.risk(self.identity, risk_weight, risk_temperature)
        prior_score, _ = self.risk(self.prior, risk_weight, risk_temperature)
        self.last_iterate=x.copy()
        return best, {"algorithm": "full_space_native_routing_admm_cg", "optimization_variables": self.size,
                      "expanded_fir_taps": sum(space.length for space in self.spaces),
                      "output_filters": len(self.spaces), "observations": len(self.observations),
                      "common_fft_size": self.nfft, "iterations": len(history), "cg_iterations": total_cg,
                      "preconditioner":preconditioner_name,
                      "numerically_converged": converged, "actual_bound_violation_db": self.bound_violation_db(best),
                      "numerical_convergence_scope":"final_phase_exact_spatial_risk" if risk_cache is not None else "final_frozen_phase_risk_subproblem",
                      "spatial_risk_solver":"exact_logsumexp_newton_cg" if risk_cache is not None else "quadratic_or_frozen_weight_cg",
                      "risk_inner_converged":risk_inner_converged,
                      "projection_matches_returned_feasible_set":True,"surrogate_updates":update_count,
                      "risk_objective": best_score, "identity_risk_objective": identity_score,
                      "regularization_prior_risk_objective": prior_score,
                      "improved_over_identity": bool(best_score < identity_score - 1e-9),
                      "gain_constraints_satisfied": self.bound_violation_db(best) <= bound_tolerance_db+1e-7,
                      "gain_bound_tolerance_db":bound_tolerance_db,
                      "gain_constraint_domain":"every_native_fft_bin; continuous_frequency_certification_not_claimed",
                      "risk_weight": risk_weight, "risk_temperature": risk_temperature, "history": history,
                      "lower_bound_method":lower_bound_method,
                      "admm_penalty_metric":penalty_metric,
                      'over_relaxation':over_relaxation,'penalty_adaptation':penalty_adaptation,
                      'anderson_depth':anderson_depth,'acceleration_attempts':acceleration_attempts,'acceleration_rejected':acceleration_rejected,
                      "full_tap_feasible_block_refinement":True,
                      "returned_bound_diagnostics":self.bound_diagnostics(best),
                      "last_iterate_bound_diagnostics":self.bound_diagnostics(x),
                      "global_optimality_claimed": False, "native_hardware_verified": False,
                      "notice": "All native FIR taps and observation cross terms are retained. Annular magnitude constraints are nonconvex; numerical convergence is not global optimality or hardware validation."}

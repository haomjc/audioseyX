"""Exact fixed-FIR power moments for shared sub gain, delay and crossovers."""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np
from scipy import signal

from .curves import target_offset
from .routing import crossover_responses


@dataclass
class ScenarioPairs:
    conditioned: list
    main_indices: tuple
    sub_indices: tuple
    main_route: np.ndarray
    sub_route: np.ndarray
    target: np.ndarray

    def main_samples(self):
        for i in self.main_indices:
            yield np.zeros(len(self.target),complex) if i is None else self.conditioned[i]*self.main_route/self.target

    def sub_samples(self):
        for i in self.sub_indices:yield self.conditioned[i]*self.sub_route/self.target

    def __call__(self):
        for main in self.main_samples():
            for bass in self.sub_samples():yield main,bass


def with_lfe_group(groups, settings):
    priority=float(settings.get('receiver_lfe_priority',1.))
    if not np.isfinite(priority) or priority<=0:raise ValueError('LFE priority must be positive and finite')
    if any(g['id']=='LFE' for g in groups):return groups
    return [*groups,{'id':'LFE','channels':['LFE'],'current_hz':None,'choices_hz':[None],
                    'small':False,'is_lfe':True,'mass':priority}]


def power_scenarios(grid, snapshot, settings, groups, levels, positions, selected=None):
    """Yield complete per-repeat main/sub pairs; no coherent repeat averaging."""
    subs=[c for c in grid.items if c.startswith('SW')]
    if len(subs)!=1:raise ValueError('The coupled native power model requires one measured physical subwoofer')
    sub=subs[0];context=settings['receiver_measurement_context']
    if context not in ['native_uncorrected','uncrossed_playback_eq_off']:
        raise ValueError('An explicit uncrossed measurement context is required')
    low=float(settings.get('receiver_analysis_low_hz',20))
    high=float(settings.get('receiver_analysis_high_hz',min(20000,grid.high,grid.rate*.42)))
    if not grid.low<=low<high<=min(grid.high,grid.rate*.49):
        raise ValueError('The coupled objective needs a complete common measured band')
    mask=(grid.frequency>=low)&(grid.frequency<=high);f=grid.frequency[mask]
    if len(f)<4:raise ValueError('Insufficient common frequency bins for coupled native planning')
    weights=1/np.maximum(f,low);weights/=weights.sum()
    speed=float(settings['receiver_delay_speed_m_s'])
    if not 250<=speed<=400:raise ValueError('Invalid receiver distance/delay conversion model')
    distances=settings.get('_receiver_base_distances_m',snapshot['distances_m'])
    conditioned=[]
    for i,m in enumerate(grid.project.measurements):
        gain=levels[m.channel] if context=='native_uncorrected' else levels[m.channel]-snapshot['levels_db'][m.channel]
        distance=distances[m.channel] if context=='native_uncorrected' else distances[m.channel]-snapshot['distances_m'][m.channel]
        value=grid.corrected(i)[mask]*10**(gain/20)*np.exp(2j*np.pi*f*distance/speed)
        if m.channel in settings.get('_fixed_inverted_outputs',[]):value=-value
        conditioned.append(value)
    offset=target_offset(settings['target_curve'],f) if settings.get('target_curve') else (
        float(settings.get('bass_db',3))/(1+(f/100)**4)+float(settings.get('tilt_db_oct',-.5))*np.log2(np.maximum(f,1000)/1000))
    models=settings.get('receiver_routing_models') or [settings.get('crossover_model','lr4')]
    if not isinstance(models,(list,tuple)) or not 1<=len(models)<=2 or len(set(models))!=len(models) or any(m not in ['lr4','bw2_lr4'] for m in models):
        raise ValueError('Choose one or both explicit crossover slope models')
    lfe_hz=float(snapshot.get('lfe_lowpass_hz',120))
    if not 10<lfe_hz<grid.rate*.49:raise ValueError('An explicit valid LFE low-pass setting is required')
    lfe=signal.sosfreqz(signal.butter(4,lfe_hz,fs=grid.rate,output='sos'),worN=f,fs=grid.rate)[1]
    for model in models:
        for group_index,group in enumerate(groups):
            for hz in group['choices_hz'] if selected is None else selected[group['id']]:
                hp,lp=(np.ones(len(f),complex),np.zeros(len(f),complex)) if hz is None else crossover_responses(f,hz,model,grid.rate)
                for source in group['channels']:
                    is_lfe=bool(group.get('is_lfe'))
                    if is_lfe:
                        source_weight=weights*((f<=min(high,lfe_hz)))
                        if source_weight.sum()<=0:raise ValueError('The analysis band must retain the LFE passband')
                        source_weight/=source_weight.sum()
                        ref=grid.items[sub]['metrics']['reference_level_db']+(snapshot['levels_db'][sub] if context=='native_uncorrected' else 0.)
                        target=10**((ref+offset)/20)*np.abs(lfe)
                        main_route=np.zeros(len(f),complex);sub_route=lfe
                    else:
                        source_weight=weights
                        large=snapshot['speaker_types'][source]=='large'
                        main_route=np.ones_like(hp) if large else hp
                        sub_route=lp if not large or snapshot.get('bass_mode')=='lfe_plus_main' else np.zeros_like(lp)
                        ref=grid.items[source]['metrics']['reference_level_db']+(snapshot['levels_db'][source] if context=='native_uncorrected' else 0.)
                        ref=settings.get('_receiver_target_references_db',{}).get(source,ref)
                        target=10**((ref+offset)/20)
                    target=np.maximum(target,1e-30)
                    for position,mass in positions.items():
                        sub_indices=grid.groups.get((sub,position),[])
                        main_indices=[None] if is_lfe else grid.groups.get((source,position),[])
                        if not main_indices or not sub_indices:raise ValueError('Every retained output/position response is required')
                        count=len(main_indices)*len(sub_indices)
                        row={'group':group_index,'crossover_hz':hz,'source':source,'position':position,
                             'routing_model':model,'mass':mass/len(group['channels'])/len(models),'repeat_scenarios':count}
                        pairs=ScenarioPairs(conditioned,tuple(main_indices),tuple(sub_indices),main_route,sub_route,target)
                        yield row,f,source_weight,pairs


def _row_moments(row,f,w,pairs):
    # Complete Cartesian-repeat sufficient statistics. Fourth moments and
    # complex cross terms are retained; this is not a synthesized mean IR.
    m2=np.zeros(len(f));ma2=m2.copy();mc=np.zeros(len(f),complex);mac=mc.copy();mc2=mc.copy()
    s2=m2.copy();s4=m2.copy();sm=mc.copy();spm=mc.copy();ss=mc.copy()
    for main in pairs.main_samples():
        power=np.abs(main)**2;conjugate=np.conj(main)
        m2+=power;ma2+=(power-1)**2;mc+=conjugate;mac+=(power-1)*conjugate;mc2+=conjugate**2
    for bass in pairs.sub_samples():
        power=np.abs(bass)**2
        s2+=power;s4+=power**2;sm+=bass;spm+=power*bass;ss+=bass**2
    nm=len(pairs.main_indices);ns=len(pairs.sub_indices)
    m2/=nm;ma2/=nm;mc/=nm;mac/=nm;mc2/=nm
    s2/=ns;s4/=ns;sm/=ns;spm/=ns;ss/=ns
    c=np.asarray([w@ma2,w@((4*m2-2)*s2),w@s4])
    lo=w*4*mac*sm;hi=w*4*mc*spm;twice=w*2*mc2*ss
    if not np.isfinite(c).all() or not np.isfinite(lo).all() or not np.isfinite(hi).all() or not np.isfinite(twice).all():
        raise ValueError('The complete native power moments are not finite')
    return c,lo,hi,twice


def build_power_table(grid, snapshot, settings, groups, levels, positions, selected=None, cancel=None):
    rows=[];constant=[];first=[];second=[];gain_constant=[];linear=[];cubic=[]
    frequency=None
    for row,f,w,pairs in power_scenarios(grid,snapshot,settings,groups,levels,positions,selected):
        if cancel is not None and cancel.is_set():raise InterruptedError('Coupled native power construction cancelled')
        c,lo,hi,twice=_row_moments(row,f,w,pairs)
        rows.append(row);constant.append(float(c.sum()));first.append(lo+hi);second.append(twice)
        gain_constant.append(c);linear.append(lo);cubic.append(hi);frequency=f
    if frequency is None:raise ValueError('The coupled power model has no complete program rows')
    return {'rows':rows,'groups':groups,'frequency':frequency,
            'band_hz':[float(settings.get('receiver_analysis_low_hz',20)),float(settings.get('receiver_analysis_high_hz',min(20000,grid.high,grid.rate*.42)))],
            'constant':np.asarray(constant),'first':np.asarray(first),'second':np.asarray(second),
            'gain_constant':np.asarray(gain_constant),'gain_first_linear':np.asarray(linear),'gain_first_cubic':np.asarray(cubic),
            'lfe_band_hz':[float(settings.get('receiver_analysis_low_hz',20)),min(float(frequency[-1]),float(snapshot.get('lfe_lowpass_hz',120)))]}


def native_delay_grid(settings, profile):
    speed=float(settings.get('receiver_delay_speed_m_s',profile.get('distance_delay_speed_m_s',343)))
    maximum=float(settings.get('receiver_max_delay_ms',15))/1000
    if not np.isfinite([speed,maximum]).all() or not 250<=speed<=400 or not 0<=maximum<=.1:
        raise ValueError('Invalid native delay grid')
    quantum=profile['distance_step_m']/speed
    count=int(np.floor(maximum/quantum+1e-9))
    return np.arange(-count,count+1)*quantum


def build_native_delay_table(grids, snapshot, settings_by_curve, groups, levels, positions, weights, delays,
                             *, selected=None, budget=512., cancel=None, progress=None):
    """All frequency moments transformed exactly onto the finite delay grid.

    Bluestein's zoom FFT evaluates the required DTFT sums; it does not sample
    fewer response frequencies or shorten a filter. Only finite native states
    are variables here, so unqueried continuous delays need not be stored.
    """
    delays=np.asarray(delays,dtype=float)
    if delays.ndim!=1 or not len(delays) or not np.isfinite(delays).all() or (len(delays)>1 and not np.allclose(np.diff(delays),delays[1]-delays[0],rtol=1e-10,atol=1e-14)):
        raise ValueError('An equally spaced complete native delay grid is required')
    rows_per_curve={}
    for name,grid in grids.items():
        options=settings_by_curve[name]
        low=float(options.get('receiver_analysis_low_hz',20))
        high=float(options.get('receiver_analysis_high_hz',min(20000,grid.high,grid.rate*.42)))
        if not np.isfinite([low,high]).all() or not grid.low<=low<high<=min(grid.high,grid.rate*.49) or np.count_nonzero((grid.frequency>=low)&(grid.frequency<=high))<4:
            raise ValueError('The coupled objective requires a complete common measured frequency band')
        models=settings_by_curve[name].get('receiver_routing_models') or [settings_by_curve[name].get('crossover_model','lr4')]
        if not isinstance(models,(list,tuple)) or not 1<=len(models)<=2 or len(set(models))!=len(models) or any(m not in ['lr4','bw2_lr4'] for m in models):
            raise ValueError('Choose one or both explicit crossover slope models')
        rows_per_curve[name]=len(models)*sum(len(g['choices_hz'] if selected is None else selected[g['id']])*len(g['channels'])*len(positions) for g in groups)
    total=sum(rows_per_curve.values())
    work_states=int(next(iter(settings_by_curve.values())).get('receiver_native_state_batch',256))
    if not 16<=work_states<=2048:raise ValueError('Native state batches must contain 16-2048 states')
    estimate=sum(g.estimated_memory_mib for g in grids.values())+(total*len(delays)*48+total*work_states*96)/1024**2+64*len(grids)
    if not np.isfinite(budget) or estimate>budget:
        raise ValueError('The complete native-delay objective exceeds the memory budget; no curves, models, bins or records were omitted')
    constants=np.empty((total,3));linear=np.empty((total,len(delays)),complex)
    cubic=np.empty_like(linear);second=np.empty_like(linear);rows=[];frequency=None;transforms=None
    for name,grid in grids.items():
        start=len(rows)
        for row,f,w,pairs in power_scenarios(grid,snapshot,settings_by_curve[name],groups,levels,positions,selected):
            if cancel is not None and cancel.is_set():raise InterruptedError('Complete native-delay transform cancelled')
            if frequency is None:
                frequency=f
                spacing=float(f[1]-f[0])
                if not np.allclose(np.diff(f),spacing,rtol=1e-10,atol=1e-10):raise ValueError('The complete physical frequency grid must be uniform')
                if len(delays)>1:
                    transforms=[signal.ZoomFFT(len(f),[harmonic*spacing*delays[0],harmonic*spacing*delays[-1]],len(delays),fs=1.,endpoint=True) for harmonic in [1,2]]
                    phases=[np.exp(-2j*np.pi*harmonic*f[0]*delays) for harmonic in [1,2]]
            elif not np.array_equal(f,frequency):raise ValueError('All curves must retain the same physical frequency grid')
            c,lo,hi,twice=_row_moments(row,f,w,pairs);index=len(rows)
            constants[index]=c
            if transforms is None:
                phase=np.exp(-2j*np.pi*f*delays[0])
                linear[index,0]=lo@phase;cubic[index,0]=hi@phase;second[index,0]=twice@(phase**2)
            else:
                first=transforms[0](np.vstack([lo,hi]))*phases[0]
                linear[index]=first[0];cubic[index]=first[1];second[index]=transforms[1](twice)*phases[1]
            rows.append({**row,'curve_profile':name})
            if progress and len(rows)%16==0:progress(80+round(5*len(rows)/total),f'Complete native objective rows {len(rows)}/{total}; all frequency bins retained')
        if len(rows)-start!=rows_per_curve[name]:raise ValueError('The native-delay objective did not retain every declared scenario row')
    if len(rows)!=total or frequency is None:raise ValueError('The complete native-delay objective is empty or inconsistent')
    return {'rows':rows,'groups':groups,'curve_weights':dict(weights),'frequency':frequency,'delay_grid_s':delays,
            'gain_constant':constants,'gain_first_linear_at_delays':linear,'gain_first_cubic_at_delays':cubic,'second_at_delays':second,
            'band_hz':[float(next(iter(settings_by_curve.values())).get('receiver_analysis_low_hz',20)),
                       float(next(iter(settings_by_curve.values())).get('receiver_analysis_high_hz',min(20000,next(iter(grids.values())).high,next(iter(grids.values())).rate*.42)))],
            'estimated_memory_mib':float(estimate),'state_batch_size':work_states,
            'native_delay_transform':{'method':'full_frequency_moment_DTFT_on_native_grid_using_Bluestein_ZoomFFT',
                                      'frequency_bins_used':len(frequency),'native_delay_points':len(delays),
                                      'response_frequency_bins_omitted':0,'FIR_taps_removed':0,
                                      'repeat_aggregation':'exact_complete_Cartesian_second_fourth_and_complex_cross_moments'}}


def direct_power_evaluation(grids, snapshot, settings_by_curve, groups, proposal, positions, weights, crossovers, cancel=None):
    """Independently square the physical sums at the selected state."""
    from .receiver_calibration import _group_costs
    selected={g['id']:[crossovers[g['id']]] for g in groups}
    rows=[];losses=[]
    for name,grid in grids.items():
        settings={**settings_by_curve[name],'_receiver_base_distances_m':proposal['distances_m'],
                  '_fixed_inverted_outputs':proposal['inverted_outputs']}
        for row,_,w,pairs in power_scenarios(grid,snapshot,settings,groups,proposal['levels_db'],positions,selected):
            if cancel is not None and cancel.is_set():raise InterruptedError('Direct native forward check cancelled')
            loss=0.
            for main,bass in pairs():loss+=float(w@((np.abs(main+bass)**2-1)**2))/row['repeat_scenarios']
            if not np.isfinite(loss):raise ValueError('The direct physical power evaluation is not finite')
            rows.append({**row,'curve_profile':name});losses.append(loss)
    table={'rows':rows,'groups':groups,'curve_weights':weights}
    values=np.asarray(losses)[:,None]
    settings=next(iter(settings_by_curve.values()))
    objective,_,errors=_group_costs(table,values,settings,False,crossovers)
    result={'objective':float(objective[0]),'group_power_error':{g['id']:float(e[0]) for g,e in zip(groups,errors)},
            'all_repeat_scenarios_retained':True,'per_curve':{}}
    for name in weights:
        score,_,errors=_group_costs(table,values,settings,False,crossovers,profile_only=name)
        result['per_curve'][name]={'objective':float(score[0]),'group_power_error':{g['id']:float(e[0]) for g,e in zip(groups,errors)}}
    return result

"""Repeatable relative-polarity checks; acoustic data do not certify wiring."""
from collections import Counter,defaultdict
from itertools import combinations,product
import numpy as np
from scipy import signal


def early_reference(measurement):
    raw=measurement.ir
    if raw is None:raise ValueError("Polarity diagnostics need original impulse responses")
    magnitude=np.abs(raw)
    prefix=raw[:min(len(raw),max(16,round(measurement.sample_rate*.002)))]
    noise=1.4826*float(np.median(np.abs(prefix-np.median(prefix))))
    centres=[]
    for fraction in [.03,.06,.1]:
        peaks,_=signal.find_peaks(magnitude,height=max(noise*12,float(np.max(magnitude))*fraction))
        if not len(peaks):centre=int(np.argmax(magnitude))
        else:
            start=int(peaks[0]);end=min(len(raw),start+max(2,round(.0004*measurement.sample_rate)))
            centre=start+int(np.argmax(magnitude[start:end]))
        centres.append(centre)
    return int(np.median(centres)),float(np.ptp(centres)/measurement.sample_rate*1000)


def _window_response(measurement,centre,after_ms,frequency):
    rate=measurement.sample_rate
    before=round(.0008*rate);after=round(after_ms/1000*rate)
    indices=centre+np.arange(-before,after+1)
    raw=np.zeros(len(indices));valid=(indices>=0)&(indices<len(measurement.ir))
    raw[valid]=measurement.ir[indices[valid]]
    raw*=signal.windows.tukey(len(raw),.4)
    time=(np.arange(len(raw))-before)/rate
    return np.exp(-2j*np.pi*frequency[:,None]*time)@raw


def _phase_scores(first,second,frequency):
    a,b=np.abs(first),np.abs(second)
    good=(a>max(float(np.max(a))*10**(-30/20),1e-20))&(b>max(float(np.max(b))*10**(-30/20),1e-20))
    if np.count_nonzero(good)<len(good)*.4:return {"available":False,"reason":"Insufficient shared direct-window spectral coverage"}
    f=frequency[good]
    unit=np.conj(first[good])*second[good]/(a[good]*b[good])
    delays=np.linspace(-.0005,.0005,193)
    agreement=np.real(np.exp(2j*np.pi*delays[:,None]*f[None,:])@unit/len(unit))
    normal=int(np.argmax(agreement));reverse=int(np.argmin(agreement))
    def band_values(index):
        corrected=unit*np.exp(2j*np.pi*f*delays[index])
        return [float(np.mean(corrected[(f>=low)&(f<high)].real)) if np.any((f>=low)&(f<high)) else None
                for low,high in [(250,800),(800,2000),(2000,6001)]]
    return {"available":True,"normal_phase_agreement":float(agreement[normal]),
            "reversed_phase_agreement":float(-agreement[reverse]),
            "normal_reference_adjustment_ms":float(delays[normal]*1000),
            "reverse_reference_adjustment_ms":float(delays[reverse]*1000),
            "bands_hz":[[250,800],[800,2000],[2000,6000]],
            "normal_band_real_agreement":band_values(normal),"reverse_band_real_agreement":band_values(reverse),
            "shared_bin_fraction":float(np.mean(good))}


def analyze_polarity(project,same_model_groups):
    if not isinstance(same_model_groups,list) or not same_model_groups:
        raise ValueError("Specify groups confirmed to use the same speaker model")
    groups=[]
    for group in same_model_groups:
        if not isinstance(group,list) or len(group)<2 or len(group)!=len(set(group)) or set(group)-set(project.channels):
            raise ValueError("Each same-model group must contain at least two distinct measured channels")
        if any(channel.startswith('SW') for channel in group):
            raise ValueError("Subwoofer/main crossover phase requires a separate integration check")
        groups.append(group)
    selected=set(channel for group in groups for channel in group)
    rows=defaultdict(list);codes=defaultdict(Counter);references={};unavailable=[]
    for index,measurement in enumerate(project.measurements):
        code=measurement.metadata.get('report',{}).get('Polarity')
        if code is not None:codes[measurement.channel][str(code)]+=1
        rows[(measurement.channel,measurement.position)].append((index,measurement))
        if measurement.channel in selected:
            if measurement.ir is None:unavailable.append(index)
            else:references[index]=early_reference(measurement)
    if unavailable:raise ValueError("Original IRs are missing from selected polarity-check channels")
    pair_rows=[]
    for group in groups:
        for left,right in combinations(group,2):
            positions=sorted({m.position for m in project.measurements if m.channel in [left,right]})
            for position in positions:
                first,second=rows[(left,position)],rows[(right,position)]
                if not first or not second:
                    pair_rows.append({"channels":[left,right],"position":position,"classification":"incomplete_coverage","scenarios":[]});continue
                scenarios=[]
                for (ia,a),(ib,b) in product(first,second):
                    high=min(6000,a.sample_rate*.45,b.sample_rate*.45)
                    if high<=800:raise ValueError("Measurement bandwidth is insufficient for this polarity check")
                    frequency=np.geomspace(250,high,192)
                    for after in [1.5,3.,5.]:
                        ha=_window_response(a,references[ia][0],after,frequency)
                        hb=_window_response(b,references[ib][0],after,frequency)
                        scores=_phase_scores(ha,hb,frequency)
                        scenarios.append({"measurement_indices":[ia,ib],"right_window_ms":after,**scores})
                valid=[s for s in scenarios if s['available']]
                reversed_votes=[s for s in valid if s['reversed_phase_agreement']>=.75 and s['reversed_phase_agreement']-s['normal_phase_agreement']>=.35
                                and all(value is not None and value<-.45 for value in s['reverse_band_real_agreement'])]
                normal_votes=[s for s in valid if s['normal_phase_agreement']>=.60 and s['normal_phase_agreement']-s['reversed_phase_agreement']>=.15]
                stable_reference=all(references[index][1]<=.3 for index,_ in first+second)
                if valid and len(reversed_votes)>=.8*len(valid) and stable_reference:classification='possible_relative_inversion'
                elif valid and len(normal_votes)>=.6*len(valid) and stable_reference:classification='no_consistent_broadband_inversion'
                else:classification='indeterminate'
                pair_rows.append({"channels":[left,right],"position":position,"classification":classification,
                                  "normal_votes":len(normal_votes),"reverse_votes":len(reversed_votes),"tested_scenarios":len(valid),
                                  "reference_stable":stable_reference,"scenarios":scenarios})
    return {"schema":"audioseyx-polarity-1","project_id":project.id,"same_model_groups":groups,
            "native_polarity_codes":[{"channel":c,"codes":dict(value)} for c,value in sorted(codes.items())],
            "native_reverse_hint_channels":[c for c,value in sorted(codes.items()) if value.get('R',0)],
            "reference_method":"earliest significant lobe with threshold sensitivity check; not a certified direct-path detector",
            "references":[{"measurement_index":index,"sample":value[0],"threshold_spread_ms":value[1]} for index,value in references.items()],
            "pairs":pair_rows,"unpaired_channels":[c for c in project.channels if c not in selected],
            "possible_relative_inversions":[{"channels":p['channels'],"position":p['position']} for p in pair_rows if p['classification']=='possible_relative_inversion'],
            "raw_ir_modified":False,"terminal_wiring_verified":False,"absolute_polarity_reference_available":False,
            "notice":"Compare only confirmed same-model speakers. Analysis windows and small timing-reference fits do not alter the original IR. Reflections, directivity, drivers and crossovers can make results inconclusive. A negative peak or a native reverse hint alone does not prove reversed terminals. Subwoofer polarity requires main/subwoofer crossover integration; no filter, distance or polarity is applied."}

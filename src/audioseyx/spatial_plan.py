"""Explicit microphone locations, seat priorities, holdouts and capture coverage.

Plans describe intended work. Only retained records establish measured coverage;
neither a plan nor an AVR calibration result creates missing impulse responses.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import copy
import math
import re
import hashlib
import json

from .layouts import validate_channels
from .model import unsafe_capture_indices

SCHEMA = 'audioseyx-spatial-plan-1'


def position_key(value):
    match=re.fullmatch(r'p(\d+)',value)
    return (0,int(match.group(1))) if match else (1,value)


def position_id(value):
    if not isinstance(value,str) or not value or value!=value.strip() or len(value)>80 or any(ord(c)<32 for c in value):
        raise ValueError('Position IDs must be nonempty text, at most 80 characters, without surrounding whitespace or control characters')
    return value


def positive(value, label):
    if isinstance(value,bool):raise ValueError(label+' must be numeric')
    result=float(value)
    if not math.isfinite(result) or not 1e-6<=result<=1e6:raise ValueError(label+' must be finite and between 0.000001 and 1000000')
    return result


def checked_plan(plan, project=None):
    if not isinstance(plan,dict) or plan.get('schema')!=SCHEMA:
        raise ValueError('Supply an explicit spatial measurement plan')
    seats=plan.get('seats');positions=plan.get('positions')
    if not isinstance(seats,list) or not 1<=len(seats)<=64 or not isinstance(positions,list) or not 1<=len(positions)<=256:
        raise ValueError('A spatial plan requires 1-64 seats and 1-256 microphone positions')
    result=dict(schema=SCHEMA,name=str(plan.get('name','Measurement plan'))[:180],
                expected_channels=validate_channels(plan.get('expected_channels')),seats=[],positions=[])
    seen=set()
    for seat in seats:
        if not isinstance(seat,dict):raise ValueError('Each seat must be an object')
        identity=position_id(seat.get('id'))
        if identity in seen:raise ValueError('Seat IDs must be distinct')
        seen.add(identity)
        result['seats'].append(dict(id=identity,label=str(seat.get('label',identity))[:180],weight=positive(seat.get('weight',1),'Seat priority')))
    assigned=set()
    for row in positions:
        if not isinstance(row,dict):raise ValueError('Each position must be an object')
        identity=position_id(row.get('id'))
        if identity in assigned:raise ValueError('Each microphone position must occur exactly once')
        assigned.add(identity)
        seat=row.get('seat');role=row.get('role','training');repeats=row.get('repeats',1)
        if seat not in seen or role not in ('training','validation'):
            raise ValueError('Each position needs an explicit known seat and training/validation role')
        if isinstance(repeats,bool) or not isinstance(repeats,int) or not 1<=repeats<=5:
            raise ValueError('Target repeats per output must be an integer from 1 to 5')
        checked=dict(id=identity,seat=seat,role=role,repeats=repeats,
                     weight=positive(row.get('weight',1),'Within-seat point priority'),label=str(row.get('label',identity))[:200])
        result['positions'].append(checked)
    if not any(p['role']=='training' for p in result['positions']):raise ValueError('At least one planned training position is required')
    if seen-{p['seat'] for p in result['positions']}:raise ValueError('Assign at least one microphone position to every declared seat')
    if sum(p['repeats'] for p in result['positions'])*len(result['expected_channels'])>1024:
        raise ValueError('This plan exceeds the 1024-response archive budget; reduce planned repeats/points or use separate projects')
    if project is not None:
        if set(project.channels)-set(result['expected_channels']):
            raise ValueError('The spatial plan must include every retained measured output')
        if {m.position for m in project.measurements}-assigned:
            raise ValueError('Assign every retained measured position explicitly before saving this plan')
    return result


def listening_area(plan):
    plan=checked_plan(plan)
    return dict(name=plan['name'],seats=[dict(id=s['id'],weight=s['weight'],positions={p['id']:p['weight']
        for p in plan['positions'] if p['seat']==s['id'] and p['role']=='training'}) for s in plan['seats']
        if any(p['seat']==s['id'] and p['role']=='training' for p in plan['positions'])],
        position_notes={p['id']:p['label'] for p in plan['positions']})


def preset_plan(project, preset='three_seat', count=8, centre_priority=.5, repeats=1):
    if preset not in ('three_seat','single_area','observed'):
        raise ValueError('Choose a three-seat, single-area or observed-position plan')
    if isinstance(count,bool) or not isinstance(count,int) or not 1<=count<=256:
        raise ValueError('Planned position count must be an integer from 1 to 256')
    if not math.isfinite(centre_priority) or not .01<=centre_priority<=.98:
        raise ValueError('Centre-seat priority must be 1-98 percent')
    ids=sorted({m.position for m in project.measurements},key=position_key) if preset=='observed' else [f'p{i}' for i in range(count)]
    if preset=='three_seat' and count<3:raise ValueError('A three-seat plan needs at least three microphone positions')
    seats=[dict(id='centre',label='Centre seat',weight=centre_priority),dict(id='left',label='Left seat',weight=(1-centre_priority)/2),
           dict(id='right',label='Right seat',weight=(1-centre_priority)/2)] if preset=='three_seat' else [dict(id='area',label='Listening area',weight=1.)]
    order=['centre','left','right','centre','centre','left','right','centre']
    rows=[dict(id=identity,seat=(order[i] if i<len(order) else ['centre','left','right'][(i-8)%3]) if preset=='three_seat' else 'area',
        role='training',weight=2. if preset=='three_seat' and i<3 else 1.,repeats=repeats,
        label=('Main microphone position' if i==0 else f'Microphone position {i+1}; choose actual placement')) for i,identity in enumerate(ids)]
    return checked_plan(dict(schema=SCHEMA,name=f'{len(rows)}-point '+('three-seat row' if preset=='three_seat' else 'listening area'),
        seats=seats,positions=rows,expected_channels=project.channels))


def inventory(project, plan=None):
    supplied=plan if plan is not None else project.metadata.get('spatial_plan')
    plan=checked_plan(supplied,project) if supplied is not None else None
    expected=plan['expected_channels'] if plan else project.channels
    unsafe=set(unsafe_capture_indices(project));counts=Counter();all_counts=Counter();phase=Counter();runs=defaultdict(set)
    for index,m in enumerate(project.measurements):
        key=(m.position,m.channel);all_counts[key]+=1
        if index not in unsafe:
            counts[key]+=1
            if m.ir is not None or m.phase_deg is not None:phase[key]+=1
        if m.metadata.get('capture_run_id'):runs[m.position].add(m.metadata['capture_run_id'])
    positions=plan['positions'] if plan else [dict(id=p,seat=None,role='training',repeats=1,weight=1.,label=p)
                                             for p in sorted({m.position for m in project.measurements},key=position_key)]
    rows=[]
    for p in positions:
        missing={c:max(0,p['repeats']-counts[p['id'],c]) for c in expected if counts[p['id'],c]<p['repeats']}
        records=sum(all_counts[p['id'],c] for c in expected)
        rows.append({**p,'records':records,'valid_counts':{c:counts[p['id'],c] for c in expected},
            'missing_repeats':missing,'measured_channels':[c for c in expected if all_counts[p['id'],c]],
            'phase_available_for_all_records':all(phase[p['id'],c]==all_counts[p['id'],c] and all_counts[p['id'],c]>0 for c in expected),
            'capture_runs':len(runs[p['id']]),'status':'complete' if not missing else 'partial' if records else 'unmeasured'})
    training=[p for p in rows if p['role']=='training'];held=[p for p in rows if p['role']=='validation']
    return dict(schema='audioseyx-spatial-inventory-1',has_explicit_plan=plan is not None,positions=rows,
        expected_channels=expected,records=len(project.measurements),unsafe_records=len(unsafe),
        measured_positions=len({m.position for m in project.measurements}),planned_positions=len(rows),
        training_complete=all(p['status']=='complete' for p in training),
        validation_complete=all(p['status']=='complete' for p in held),
        complete=bool(rows) and all(p['status']=='complete' for p in rows) and not unsafe,
        pending_training_positions=[p['id'] for p in training if p['status']!='complete'],
        pending_validation_positions=[p['id'] for p in held if p['status']!='complete'],
        records_synthesized=0,original_records_modified=False)


def capture_batches(project, identity, channels, repeats=1, mode='append'):
    identity=position_id(identity);channels=validate_channels(channels)
    if mode not in ('append','resume'):raise ValueError('Choose append or resume capture mode')
    if isinstance(repeats,bool) or not isinstance(repeats,int) or not 1<=repeats<=5:
        raise ValueError('Repeats must be an integer from 1 to 5')
    counts=Counter()
    if project is not None:
        unsafe=set(unsafe_capture_indices(project))
        counts.update(m.channel for i,m in enumerate(project.measurements) if m.position==identity and i not in unsafe)
    groups=defaultdict(list)
    for channel in channels:
        needed=max(0,repeats-counts[channel]) if mode=='resume' else repeats
        if needed:groups[needed].append(channel)
    batches=[dict(channels=group,repeats=count) for count,group in sorted(groups.items())]
    total=sum(len(batch['channels'])*batch['repeats'] for batch in batches)
    if project is not None and len(project.measurements)+total>1024:
        raise ValueError('This capture would exceed the 1024-response project limit; no test tones have been requested')
    from .saved_controls import project_signature
    result=dict(position=identity,mode=mode,target_repeats=repeats,selected_channels=channels,
                batches=batches,responses_to_capture=total,no_hardware_contact=True,
                source_dataset_sha256=project_signature(project) if project is not None else None)
    result['preview_sha256']=hashlib.sha256(json.dumps(result,sort_keys=True,allow_nan=False).encode()).hexdigest()
    return result


def evaluation_weights(plan, positions):
    """Distribute held-out seat mass without fitting targets to held-out data."""
    plan=checked_plan(plan);positions=set(positions)
    if not positions:return {}
    known={p['id'] for p in plan['positions']}
    if positions-known:raise ValueError('Validation positions must belong to the explicit spatial plan')
    result={}
    for seat in plan['seats']:
        local=[p for p in plan['positions'] if p['seat']==seat['id'] and p['id'] in positions]
        total=sum(p['weight'] for p in local)
        for p in local:result[p['id']]=seat['weight']*p['weight']/total
    total=sum(result.values())
    return {p:w/total for p,w in result.items()}


def prepare_design(project, settings):
    settings=dict(settings)
    supplied=project.metadata.get('spatial_plan')
    if supplied is None:return settings
    plan=checked_plan(supplied,project);coverage=inventory(project,plan)
    observed={m.position for m in project.measurements}
    requested=settings.get('validation_positions',[])
    if isinstance(requested,str):requested=[p.strip() for p in requested.split(',') if p.strip()]
    if not isinstance(requested,list):raise ValueError('Validation positions must be a list')
    planned_held={p['id'] for p in plan['positions'] if p['role']=='validation'}
    settings['validation_positions']=sorted(set(requested)|(planned_held&observed),key=position_key)
    if set(settings['validation_positions'])-observed:
        raise ValueError('Validation positions must have actual recorded responses')
    use_area=settings.get('listening_area',project.metadata.get('listening_area')) is not None
    settings['validation_position_weights']=evaluation_weights(plan,settings['validation_positions']) if use_area else {}
    if not coverage['complete'] and settings.get('allow_partial_listening_area') is not True:
        raise ValueError('The explicit measurement plan is incomplete: '+', '.join(coverage['pending_training_positions']+coverage['pending_validation_positions'])+'. Complete its output/repeat coverage or request an explicitly provisional design.')
    settings['spatial_coverage']=coverage
    settings['spatial_plan']=copy.deepcopy(plan)
    return settings

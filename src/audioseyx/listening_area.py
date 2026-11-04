"""Explicit seat priorities and measurement coverage; no synthetic positions."""
import numpy as np


def three_seat_row_plan():
    return {'name':'Three-seat row / centre priority',
            'seats':[{'id':'centre','weight':.5,'positions':{'p0':2.,'p3':1.,'p4':1.}},
                     {'id':'left','weight':.25,'positions':{'p1':2.,'p5':1.}},
                     {'id':'right','weight':.25,'positions':{'p2':2.,'p6':1.}}],
            'position_notes':{'p0':'Centre seat ear position','p1':'Left seat ear position','p2':'Right seat ear position',
                              'p3':'Near centre seat, forward','p4':'Near centre seat, backward',
                              'p5':'Near left seat, forward','p6':'Near right seat, forward'}}


def resolve_listening_area(project,plan,validation_positions=()):
    if not isinstance(plan,dict) or not isinstance(plan.get('seats'),list) or not plan['seats']:
        raise ValueError('A listening-area plan needs explicit seats and position assignments')
    observed={m.position for m in project.measurements};validation=set(validation_positions)
    training=observed-validation
    seat_ids=set();assigned={};rows=[]
    for seat in plan['seats']:
        identity=seat.get('id');weight=float(seat.get('weight',0));positions=seat.get('positions')
        if not isinstance(identity,str) or not identity or identity in seat_ids or not np.isfinite(weight) or weight<=0:
            raise ValueError('Seat identifiers must be distinct and seat priorities positive')
        if not isinstance(positions,dict) or not positions:raise ValueError('Each seat needs explicit microphone position IDs and within-seat weights')
        seat_ids.add(identity);within={}
        for position,value in positions.items():
            value=float(value)
            if not isinstance(position,str) or position in assigned or not np.isfinite(value) or value<=0:
                raise ValueError('Positions must belong to exactly one seat with positive weights')
            assigned[position]=identity;within[position]=value
        rows.append((identity,weight,within))
    unassigned=training-set(assigned)
    if unassigned:raise ValueError('Training positions are not assigned to a seat: '+', '.join(sorted(unassigned)))
    total=sum(weight for _,weight,_ in rows)
    missing=[];weights={};seat_coverage=[]
    for identity,weight,within in rows:
        available={p:v for p,v in within.items() if p in training}
        if not available:missing.append(identity)
        mass=sum(available.values())
        for position,value in available.items():weights[position]=weight/total*value/mass
        seat_coverage.append({'seat':identity,'requested_priority':weight/total,'training_positions':sorted(available),
                              'planned_positions_not_measured':sorted(set(within)-observed)})
    observed_mass=sum(weights.values())
    if observed_mass:
        weights={p:value/observed_mass for p,value in weights.items()}
    return {'name':str(plan.get('name','Listening area')),'position_weights':weights,
            'position_to_seat':{p:assigned[p] for p in training if p in assigned},
            'seats':seat_coverage,'missing_training_seats':missing,
            'required_seats_measured':not missing,'provisional_normalization':bool(missing),
            'unmeasured_positions_are_not_synthesized':True,
            'notice':'Seat priorities are divided among measured points within each seat; repeated scans do not multiply seat priority. Missing seats remain unmeasured and prevent final row-wide correction.'}


def weighted_median(curves,weights):
    values=np.asarray(curves,dtype=float);weights=np.asarray(weights,dtype=float)
    if values.ndim!=2 or weights.shape!=(values.shape[0],) or not np.isfinite(values).all() or not np.isfinite(weights).all() or np.any(weights<=0):
        raise ValueError('Weighted spectral statistics need finite curves and positive matching weights')
    weights=weights/weights.sum()
    order=np.argsort(values,axis=0)
    sorted_values=np.take_along_axis(values,order,axis=0)
    cumulative=np.cumsum(weights[order],axis=0)
    index=np.sum(cumulative<.5-1e-12,axis=0);columns=np.arange(values.shape[1])
    result=sorted_values[index,columns]
    tied=np.abs(cumulative[index,columns]-.5)<1e-12
    following=sorted_values[np.minimum(index+1,len(weights)-1),columns]
    return np.where(tied,(result+following)/2,result)

let lastCapabilitySignature='', lastTemporalDesign=null;
function initVerificationControls(){
 $('generate-capabilities').onclick=()=>action(async()=>{await api('/api/capabilities/export',{});await refresh();toast('Capability report generated without connecting a receiver')});
}
const temporalPercent=value=>Number.isFinite(value)?(100*value).toFixed(2)+'%':'Unavailable';
const temporalSeconds=value=>Number.isFinite(value)?value.toFixed(3)+' s':'Unavailable';
function temporalMedian(summary,cutoff='80'){return summary?.late_energy_fraction?.[cutoff]?.median??null}
function renderVerification(result){
 const body=result.rows.map(row=>{
  const t=row.temporal;
  const late=t?.available?`${temporalPercent(temporalMedian(t.broadband.before))} → ${temporalPercent(temporalMedian(t.broadband.after))}`:esc(t?.reason||'Unavailable');
  return `<tr><td>${esc(row.channel)} / ${esc(row.position)}<small>${row.before_repeats} before / ${row.after_repeats} after repeats</small></td><td>${row.mean_level_delta_db===null?'Shape only':row.mean_level_delta_db.toFixed(2)+' dB'}</td><td>${row.ripple_before_db.toFixed(2)} → ${row.ripple_after_db.toFixed(2)} dB</td><td>${late}</td></tr>`;
 }).join('');
 const details=result.rows.filter(row=>row.temporal?.available).map(row=>{
  const t=row.temporal;
  const bands=t.bands.map(b=>`<tr><td>${esc(b.name)}</td><td>${temporalPercent(temporalMedian(b.before))} → ${temporalPercent(temporalMedian(b.after))}</td><td>${temporalSeconds(b.before.decay_fit_s.T20.median)} → ${temporalSeconds(b.after.decay_fit_s.T20.median)}<small>${b.before.decay_fit_s.T20.count}/${b.before.repeat_count} before, ${b.after.decay_fit_s.T20.count}/${b.after.repeat_count} after valid fits</small></td></tr>`).join('');
  return `<details><summary>${esc(row.channel)} / ${esc(row.position)} · Band decay and repeat quality</summary><p class="hint">Common observed window: ${(t.relative_window_s[0]*1000).toFixed(1)} to ${(t.relative_window_s[1]*1000).toFixed(1)} ms around each reference. ${t.shared_timebase_verified_in_metadata?'Verified shared-timebase peak change: '+t.absolute_peak_time_delta_ms.toFixed(3)+' ms.':'Absolute timing change unavailable: shared timebase is unverified.'} No raw response was aligned.</p><div class="table-scroll"><table><thead><tr><th>Analysis band</th><th>Recorded energy after 80 ms</th><th>T20 extrapolated slope / descriptive</th></tr></thead><tbody>${bands}</tbody></table></div><p class="hint">${esc(t.notice)} Analysis-filter ringing and truncated/noisy fits can make estimates unavailable. The downloadable report retains every repeat and rejection reason.</p></details>`;
 }).join('');
 $('verification-result').innerHTML=`<p class="hint">${esc(result.notice)}</p><p>Matched <strong>${result.matched}</strong> output/position entries. Late-energy values are fractions of each recorded post-reference energy, independent of overall gain.</p><div class="table-scroll"><table><thead><tr><th>Output / position</th><th>Level change</th><th>Bass variation / before → after</th><th>Energy after 80 ms / before → after</th></tr></thead><tbody>${body}</tbody></table></div>${details}`;
}

function renderTemporalPrediction(design){
 const value=design?.temporal_prediction;
 $('temporal-prediction-card').classList.toggle('hidden',!value);
 if(!value||lastTemporalDesign===design)return;
 lastTemporalDesign=design;
 const rows=value.records.map(row=>{
  if(!row.available)return `<tr><td>Record ${row.measurement_index+1}</td><td colspan="3">${esc(row.reason)}</td></tr>`;
  const a=row.before.broadband,b=row.after.broadband;
  return `<tr><td>${esc(row.channel)} / ${esc(row.position)}<small>Record ${row.measurement_index+1} · ${row.used_for_filter_selection?'training':'held out'}</small></td><td>${temporalPercent(a.pre_reference_energy_fraction)} → ${temporalPercent(b.pre_reference_energy_fraction)}</td><td>${temporalPercent(a.late_energy_fraction?.['80'])} → ${temporalPercent(b.late_energy_fraction?.['80'])}</td><td>${row.full_linear_convolution_samples.toLocaleString()} samples</td></tr>`;
 }).join('');
 $('temporal-prediction-result').innerHTML=`<p class="hint">${esc(value.notice)}</p><div class="table-scroll"><table><thead><tr><th>Recorded response</th><th>Energy before fixed reference</th><th>Energy after 80 ms</th><th>Complete convolution</th></tr></thead><tbody>${rows}</tbody></table></div><p>All band profiles, quality checks and repeats are retained in the design bundle.</p>`;
}

function renderCapabilityAssessment(report){
 if(!report)return;
 const signature=JSON.stringify(report);
 if(signature===lastCapabilitySignature)return;
 lastCapabilitySignature=signature;
 const names={implemented_offline:'Implemented offline',reference_only:'Reference / export only',interface_blocked:'Receiver interface lacks required controls'};
 const native={native_candidate_unverified:'Native candidate / unverified',measurement_workflow:'Measurement workflow',export_only:'Export only',not_provided:'Not provided'};
 const c=report.counts,p=report.project_context;
 const context=p?`<p>${p.measurement_count} measurements / ${p.measured_positions.length} measured positions / ${esc(p.layout||'custom layout')}.</p>${p.missing_seats.length?`<p class="hint">Unmeasured seats: ${p.missing_seats.map(esc).join(', ')}. Repeated scans do not fill another seat.</p>`:''}${p.configured_unmeasured_outputs.length?`<p class="hint">Configured outputs without measurements: ${p.configured_unmeasured_outputs.map(esc).join(', ')}.</p>`:''}`:'<p>Import a project to include its measurement coverage.</p>';
 $('capability-summary').innerHTML=`<p><strong>${c.implemented_offline}</strong> implemented offline · <strong>${c.reference_only}</strong> reference/export only · <strong>${c.interface_blocked}</strong> blocked by the current receiver interface.</p><p class="hint">${esc(report.notice)}</p>${context}`;
 $('capability-table').innerHTML=report.features.map(f=>`<tr><td>${esc(f.category)}<small>${esc(f.name)}</small></td><td>${esc(names[f.software_state])}</td><td>${esc(native[f.native_state])}</td><td>${esc(f.scope)}</td></tr>`).join('');
}

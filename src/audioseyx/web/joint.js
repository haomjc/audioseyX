let receiverJointDefaults=null;
function jointSettings(){
 const enabled=$('phase-mode').value==='joint';
 if(!enabled)return {optimizer:'independent'};
 if(!state?.features?.full_complex_joint_fir)throw new Error('Restart audioseyX before using the joint solver from this version.');
 if($('joint-use-receiver-crossovers').checked&&!receiverJointDefaults)throw new Error('Read the receiver crossover values before enabling this option.');
 return {optimizer:'native_joint',relative_levels_confirmed:$('joint-levels-confirmed').checked,
  routing_measurements_confirmed:$('joint-routing-confirmed').checked,independent_subs_confirmed:$('joint-subs-confirmed').checked,
  crossover_model:$('joint-crossover-model').value,bass_mode:$('joint-bass-mode').value,
  lfe_lowpass_hz:Number($('joint-lfe-lowpass').value),
  crossovers:$('joint-use-receiver-crossovers').checked?receiverJointDefaults.crossovers:{},
  large_channels:$('joint-large-channels').value,crossover_candidates_hz:$('joint-crossover-candidates').value,joint_phase_target:$('joint-phase-target').value,
  joint_delay_ms:Number($('joint-delay').value),position_risk:Number($('joint-risk').value),
  joint_iterations:Number($('joint-iterations').value),joint_memory_mib:Number($('joint-memory').value),
  fdw_cycles:Number($('fdw-cycles').value),fdw_start_hz:Number($('fdw-start').value),
  validation_positions:$('joint-validation').value.split(',').map(p=>p.trim()).filter(Boolean),
  native_synthesis_path:$('joint-synthesis-path').value.trim()||null};
}

let jointProjectId='';
function renderJointProject(project){
 if(!project||project.id===jointProjectId)return;
 jointProjectId=project.id;
 const demo=project.origin==='synthetic';
 $('joint-levels-confirmed').checked=demo;$('joint-routing-confirmed').checked=demo;
 $('joint-validation').placeholder=project.positions.length>1?'Example: '+project.positions.at(-1):'At least one training position must remain';
}

function renderJointSummary(result){
 const optimizer=result.optimization;
 $('joint-result').innerHTML=`<p><strong>${result.model.channels.length} output filters</strong> / ${result.model.inputs.length} program inputs / ${result.model.positions.length} positions</p><p>${optimizer.optimization_variables.toLocaleString()} optimization variables; ${optimizer.expanded_fir_taps.toLocaleString()} expanded FIR taps.</p><p class="hint">${esc(optimizer.native_realization_notice)} No second delay/polarity alignment is applied after this joint design.</p>`;
}

function renderJointEvaluation(design){
 const value=design?.joint_evaluation;
 $('joint-evaluation-card').classList.toggle('hidden',!value);
 if(!value)return;
 const optimizer=design.joint_optimization;
 const quality=design.joint_model.repeat_diagnostics;
 const search=design.crossover_search;
 const cell=v=>v.toFixed(3)+' dB';
 const rows=[['Training / neutral',value.identity],['Training / independent 0.4 reference',value.independent_0_4_baseline],['Training / joint',value.joint]];
 if(value.holdout)rows.push(['Holdout / neutral',value.holdout.identity],['Holdout / independent reference',value.holdout.independent_0_4_baseline],['Holdout / joint',value.holdout.joint]);
 $('joint-evaluation-results').innerHTML=`<p>${optimizer.numerically_converged?'Numerical stopping criteria met.':'Iteration budget reached; numerical convergence is not established.'} Returned-filter FFT-grid bound gap: ${optimizer.actual_bound_violation_db.toFixed(4)} dB.</p><div class="table-scroll"><table><thead><tr><th>Evaluation</th><th>Mean group magnitude error</th><th>Worst group error</th></tr></thead><tbody>${rows.map(([name,r])=>`<tr><td>${esc(name)}</td><td>${cell(r.mean_group_rms_db)}</td><td>${cell(r.worst_group_rms_db)}</td></tr>`).join('')}</tbody></table></div><p class="hint">${search?`Crossover search fully re-solved ${search.candidates.length} candidates using training data only. Selected: ${Object.entries(search.selected_hz).map(([c,f])=>esc(c)+' '+f+' Hz').join(', ')}. `:''}The chart above shows routed program-input sums. Independent references are this project's 0.4 algorithm, not an OCA result. Holdout positions never refit the filters. Hardware and native synthesis are not independently verified.</p>${quality.groups.length?`<h4>Repeat consistency</h4><div class="table-scroll"><table><thead><tr><th>Channel / position</th><th>Repeats</th><th>Peak spread</th><th>Level spread</th><th>Complex agreement</th></tr></thead><tbody>${quality.groups.map(r=>`<tr><td>${esc(r.channel)} / ${esc(r.position)}</td><td>${r.repeats}</td><td>${r.peak_time_spread_ms===null?'Unavailable':r.peak_time_spread_ms.toFixed(3)+' ms'}</td><td>${r.median_level_spread_db===null?'Unavailable':r.median_level_spread_db.toFixed(2)+' dB'}</td><td>${r.complex_repeat_agreement_mean===null?'Unavailable':r.complex_repeat_agreement_mean.toFixed(3)}</td></tr>`).join('')}</tbody></table></div><p class="hint">${esc(quality.notice)}</p>`:''}`;
}

$('cancel-design').onclick=()=>action(async()=>{await api('/api/design/cancel',{});toast('Cancellation requested; waiting for the current calculation to exit');});
$('joint-read-receiver').onclick=()=>action(async()=>{
 const values=await api('/api/design/receiver-settings',{});receiverJointDefaults=values;
 $('joint-use-receiver-crossovers').checked=true;
 $('joint-bass-mode').value=values.bass_mode;
 $('joint-large-channels').value=values.large_channels.join(', ');
 $('joint-lfe-lowpass').value=values.lfe_lowpass_hz;
 $('joint-receiver-values').textContent=Object.entries(values.crossovers).map(([channel,hz])=>channel+' '+hz+' Hz').join(' · ');
 toast('Loaded the current receiver menu settings into this offline design. Receiver settings were not changed.');
});

let nativeCalibrationProject='',nativeCalibrationResult='';

function initReceiverCalibrationControls(){
 initReceiverAuditControls();
 initNativeRefinementControls();
 $('phase-mode').addEventListener('change',()=>{
  $('native-calibration-options').classList.toggle('hidden',$('phase-mode').value!=='receiver');
 });
}

function nativeCalibrationSettings(){
 if($('phase-mode').value!=='receiver')return {};
 if(!state?.features?.coupled_native_controls)throw new Error('Restart audioseyX before using this version of coupled native planning.');
 const groups=$('native-same-model').value.trim();
 return {optimizer:'native_calibration',...nativeRefinementSettings(),
  receiver_control_engine:'coupled',
  relative_levels_confirmed:$('native-reference-confirmed').checked,
  routing_measurements_confirmed:$('native-reference-confirmed').checked,
  receiver_measurement_context:$('native-measurement-context').value,
  receiver_routing_models:$('native-routing-model').value==='robust'?['lr4','bw2_lr4']:[$('native-routing-model').value],
  receiver_max_delay_ms:Number($('native-max-delay').value),
  receiver_delay_speed_m_s:Number($('native-distance-speed').value),
  receiver_distance_basis:$('native-distance-basis').value,
  receiver_memory_mib:Number($('native-memory').value),
  receiver_sub_level_search:$('native-sub-level-search').checked,
  receiver_sub_level_min_db:Number($('native-sub-min').value),receiver_sub_level_max_db:Number($('native-sub-max').value),
  receiver_lfe_priority:Number($('native-lfe-priority').value),receiver_position_risk:Number($('native-position-risk').value),
  receiver_analysis_low_hz:Number($('native-analysis-low').value),
  receiver_analysis_high_hz:$('native-analysis-high').value.trim()?Number($('native-analysis-high').value):undefined,
  receiver_crossover_candidates_hz:$('native-crossover-candidates').value.trim()||undefined,
  receiver_auditory_balance:$('native-auditory-balance').checked,
  receiver_time_crossover_search:$('native-time-search').checked,
  same_model_groups:groups?groups.split(';').map(row=>row.split(',').map(c=>c.trim().toUpperCase()).filter(Boolean)):undefined,
  validation_positions:$('native-validation').value.split(',').map(p=>p.trim()).filter(Boolean)};
}

function renderReceiverCalibrationProject(project){
 if(!project||project.id===nativeCalibrationProject)return;
 nativeCalibrationProject=project.id;
 if(String(project.model||'').includes('3700'))$('native-distance-speed').value='300';
 $('native-same-model').value=(project.same_model_groups||[]).map(g=>g.join(', ')).join('; ');
 $('native-reference-confirmed').checked=project.origin==='synthetic';
 $('native-measurement-context').value=project.origin==='native_avr'?'native_uncorrected':'unknown';
}

function renderReceiverCalibration(design){
 const result=design?.receiver_calibration;
 $('native-calibration-results').classList.toggle('hidden',!result);
 $('receiver-adjustments-choice').classList.toggle('hidden',!result);
 if(!result){$('include-receiver-adjustments').checked=false;nativeCalibrationResult='';return;}
 const id=JSON.stringify(result.proposal);
 if(id!==nativeCalibrationResult){nativeCalibrationResult=id;$('include-receiver-adjustments').checked=true;}
 const before=result.baseline,after=result.proposal;
 const channels=Object.keys(after.distances_m);
 const rows=channels.map(c=>`<tr><td>${esc(c)}</td><td>${before.distances_m[c].toFixed(2)} → ${after.distances_m[c].toFixed(2)} m</td><td>${before.levels_db[c].toFixed(1)} → ${after.levels_db[c].toFixed(1)} dB</td><td>${after.crossovers_hz[c]===undefined?'Keep':after.crossovers_hz[c]+' Hz'}</td><td>${after.inverted_outputs.includes(c)?'Invert filter sign':'Keep'}</td></tr>`).join('');
 const search=result.search;
 const score=search?`<p>Fixed-FIR model objective: ${result.original_evaluation.objective.toFixed(6)} → ${search.objective.toFixed(6)}. ${(search.evaluated_delay_polarity_gain_states||search.evaluated_delay_polarity_states).toLocaleString()} native states and all ${search.linked_crossover_combinations.toLocaleString()} linked crossover combinations were covered conditionally.</p><p>Additional subwoofer delay: ${(search.delay_s*1000).toFixed(3)} ms. ${search.sub_level_db===undefined?'':'Sub level before codec compensation: '+search.sub_level_db.toFixed(1)+' dB. '}Distance conversion: ${search.distance_proposal.distance_delay_speed_m_s} m/s (unverified model).</p>`:'<p>Level-only speaker proposal. Existing sub timing and crossovers are retained.</p>';
 const balance=result.auditory_balance;
 const auditory=balance?`<p>Auditory-band matching: ${balance.groups.map(g=>esc(g.channels.join('/'))+' '+g.band_imbalance_rms_before_db.toFixed(2)+' → '+g.band_imbalance_rms_after_db.toFixed(2)+' dB').join(' · ')}.</p>`:'';
 const held=result.independent_validation?`<p>Excluded positions ${result.independent_validation.positions.map(esc).join(', ')}: model objective ${result.independent_validation.before.objective.toFixed(6)} → ${result.independent_validation.after.objective.toFixed(6)}; no refitting or new level normalization.</p>`:'';
 const headroom=result.bass_routing_headroom_proxy?`<p>Coherent-input bass-routing bound growth: ${result.bass_routing_headroom_proxy.maximum_bound_growth_db.toFixed(2)} dB. This proxy does not measure available output, distortion or clipping.</p>`:'';
 const selection=result.control_selection;
 const selected=selection?`<p>Control targets: ${Object.entries(selection.curve_weights).map(([c,w])=>esc(c)+' '+(w*100).toFixed(0)+'%').join(' / ')}. ${selection.includes_lfe?'LFE passband participates. ':''}Main speaker trims are shared across the selected curves.</p>`:'';
 const curveRows=search?.per_curve?Object.entries(search.per_curve).map(([c,e])=>`<tr><td>${esc(c)}</td><td>${result.original_evaluation.per_curve[c].objective.toFixed(6)} → ${e.objective.toFixed(6)}</td></tr>`).join(''):'';
 const perCurve=curveRows?`<div class="table-scroll"><table><thead><tr><th>Curve at the common settings</th><th>Individual model objective / before → selected</th></tr></thead><tbody>${curveRows}</tbody></table></div>`:'';
 const transform=search?.native_delay_transform;
 const evidence=transform?`<details><summary>Full-data solver evidence</summary><p>${transform.frequency_bins_used.toLocaleString()} frequency bins contribute to ${transform.native_delay_points.toLocaleString()} native delay points; no FIR taps or response frequency bins were removed. Direct physical-response gap: ${(result.direct_forward_check?.power_moment_absolute_gap??0).toExponential(3)}.</p><p>Sub level candidates: ${search.sub_level_candidates_db[0].toFixed(1)} to ${search.sub_level_candidates_db.at(-1).toFixed(1)} dB, ${search.sub_level_candidates_db.length} grid values, with ${search.coefficient_compensation_reserved_db.toFixed(1)} dB coefficient compensation reserved.</p></details>`:'';
 $('native-calibration-detail').innerHTML=`${selected}${nativeRefinementReport(result)}${score}${perCurve}${auditory}${held}<div class="table-scroll"><table><thead><tr><th>Output</th><th>Distance</th><th>Level before codec compensation</th><th>Crossover</th><th>Polarity</th></tr></thead><tbody>${rows}</tbody></table></div>${headroom}${evidence}<p class="hint">${esc(result.notice)} These model objectives are not measured acoustic error or standardized loudness. Proposed controls remain unchanged until included in a reviewed receiver transfer.</p>`;
}

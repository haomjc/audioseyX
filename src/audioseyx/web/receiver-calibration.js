let nativeCalibrationProject='',nativeCalibrationResult='';

function initReceiverCalibrationControls(){
 $('phase-mode').addEventListener('change',()=>{
  $('native-calibration-options').classList.toggle('hidden',$('phase-mode').value!=='receiver');
 });
}

function nativeCalibrationSettings(){
 if($('phase-mode').value!=='receiver')return {};
 const groups=$('native-same-model').value.trim();
 return {optimizer:'native_calibration',
  relative_levels_confirmed:$('native-reference-confirmed').checked,
  routing_measurements_confirmed:$('native-reference-confirmed').checked,
  receiver_measurement_context:$('native-measurement-context').value,
  receiver_routing_models:$('native-routing-model').value==='robust'?['lr4','bw2_lr4']:[$('native-routing-model').value],
  receiver_max_delay_ms:Number($('native-max-delay').value),
  receiver_delay_speed_m_s:Number($('native-distance-speed').value),
  receiver_distance_basis:$('native-distance-basis').value,
  receiver_memory_mib:Number($('native-memory').value),
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
 const score=search?`<p>Model power-error objective: ${result.original_evaluation.objective.toFixed(6)} → ${search.objective.toFixed(6)}. ${search.evaluated_delay_polarity_states.toLocaleString()} distance/polarity states and all ${search.linked_crossover_combinations.toLocaleString()} linked crossover combinations were covered through conditional separability.</p><p>Additional subwoofer delay: ${(search.delay_s*1000).toFixed(3)} ms. Distance conversion: ${search.distance_proposal.distance_delay_speed_m_s} m/s (unverified model).</p>`:'<p>Level-only proposal. Existing distance and crossover values are retained.</p>';
 const balance=result.auditory_balance;
 const auditory=balance?`<p>Auditory-band matching: ${balance.groups.map(g=>esc(g.channels.join('/'))+' '+g.band_imbalance_rms_before_db.toFixed(2)+' → '+g.band_imbalance_rms_after_db.toFixed(2)+' dB').join(' · ')}.</p>`:'';
 const held=result.independent_validation?`<p>Excluded positions ${result.independent_validation.positions.map(esc).join(', ')}: model objective ${result.independent_validation.before.objective.toFixed(6)} → ${result.independent_validation.after.objective.toFixed(6)}; no refitting or new level normalization.</p>`:'';
 const headroom=result.bass_routing_headroom_proxy?`<p>Coherent-input bass-routing bound growth: ${result.bass_routing_headroom_proxy.maximum_bound_growth_db.toFixed(2)} dB. This proxy does not measure available output, distortion or clipping.</p>`:'';
 $('native-calibration-detail').innerHTML=`${score}${auditory}${held}<div class="table-scroll"><table><thead><tr><th>Output</th><th>Distance</th><th>Level before codec compensation</th><th>Crossover</th><th>Polarity</th></tr></thead><tbody>${rows}</tbody></table></div>${headroom}<p class="hint">${esc(result.notice)} These model objectives are not measured acoustic error or standardized loudness. Proposed controls remain unchanged until included in a reviewed receiver transfer.</p>`;
}

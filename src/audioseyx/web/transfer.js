let transferDigest='',transferWireChecked=false,transferReceiverSelectionMatches=true;

function initTransferControls(){
 $('template-file').onclick=()=>$('template-input').click();
 $('template-input').onchange=()=>action(async()=>{
  const file=$('template-input').files[0];
  if(file){await api('/api/template',await encodeFile(file));await refresh();toast('Saved the original OCA recovery copy');}
  $('template-input').value='';
 });
 $('prepare-transfer').onclick=()=>action(async()=>{
  toast('Refreshing device information and checking the plan...');
  await api('/api/transfer/prepare',{uncorrected_confirmed:$('uncorrected-confirmed').checked,
   apply_receiver_adjustments:$('include-receiver-adjustments').checked,initial_curve:$('initial-calibration-curve').value});await refresh();
 });
 $('transfer-reviewed').onchange=()=>{
  $('apply-transfer').disabled=!transferDigest||!transferWireChecked||!transferReceiverSelectionMatches||!$('transfer-reviewed').checked;
 };
 $('include-receiver-adjustments').onchange=()=>{
  const plan=state?.transfer?.plan;
  transferReceiverSelectionMatches=!plan||(plan.receiver_settings_included===$('include-receiver-adjustments').checked&&plan.initial_curve===$('initial-calibration-curve').value);
  $('transfer-reviewed').checked=false;$('apply-transfer').disabled=true;
  if(plan)toast('Prepare the transfer review again after changing receiver adjustments');
 };
 $('initial-calibration-curve').onchange=()=>{
  $('transfer-reviewed').checked=false;$('apply-transfer').disabled=true;
  if(state?.transfer?.plan)toast('Prepare the review again after changing the initial curve');
 };
 $('apply-transfer').onclick=()=>action(async()=>{
  if(!transferReceiverSelectionMatches)throw new Error('Prepare and review the selected receiver adjustments again.');
  await api('/api/transfer/apply',{digest:transferDigest,reviewed:$('transfer-reviewed').checked});
  await refresh();page('results');
 });
}

function renderTransfer(result){
 if(!result)return;
 $('template-info').textContent=result.template?`${result.template.model} · ${result.template.channels} channels / ${result.template.original_filter_backup_available?'Original OCA saved':'Settings snapshot only; no original filters'}`:'No complete OCA has been imported.';
 const plan=result.plan;
 transferReceiverSelectionMatches=!plan||(plan.receiver_settings_included===$('include-receiver-adjustments').checked&&plan.initial_curve===$('initial-calibration-curve').value);
 $('apply-transfer').disabled=!transferDigest||!transferWireChecked||!transferReceiverSelectionMatches||!$('transfer-reviewed').checked;
 if((plan?.digest||'')===transferDigest)return;
 transferDigest=plan?.digest||'';
 transferWireChecked=plan?.coefficient_transport_audit?.all_streams_complete===true;
 $('transfer-reviewed').checked=false;$('apply-transfer').disabled=true;
 $('download-calibration').classList.toggle('disabled',!plan||!transferWireChecked);
 if(!plan){$('transfer-plan').innerHTML='<p>Prepare a plan to review the receiver, channel count and gain compensation.</p>';return;}
 const audit=plan.coefficient_transport_audit;
 const quantization=plan.coefficient_quantization;
 const rates=audit?Object.values(audit.sample_rate_slots_hz).map(hz=>(hz/1000)+' kHz').join(' / '):'';
 const dataCheck=audit?`<div class="summary-line"><span>Native data coverage</span><strong>${audit.channels} outputs / ${audit.streams} complete streams</strong></div><div class="summary-line"><span>Different native curve data</span><strong>${audit.channels_with_distinct_curve_coefficients} / ${audit.channels} outputs</strong></div><p class="hint">Both calibration curves and ${rates} slots were reconstructed and checked before transfer. These byte checks do not verify native filter reconstruction or acoustic performance.</p>`:'<p class="hint">A current native data audit is required before transfer.</p>';
 const precision=quantization?`<details><summary>Coefficient precision</summary><p>Largest absolute coefficient rounding error: ${quantization.maximum_absolute_error.toExponential(3)}. Values rounded to zero: ${quantization.underflowed_values}. This describes coefficient values, not acoustic error.</p></details>`:'';
 const proposal=plan.receiver_settings_proposal;
 const robustness=plan.receiver_measurement_robustness;
 const refinement=plan.receiver_eq_refinement;
 const designEvidence=(robustness?`<p>Reviewed measurement model: ${esc(robustness.repeat_confidence.mode)} repeat weighting, ${robustness.uncertainty_case_count} declared gain/timing scenarios. Uncertainty bounds are assumptions.</p>`:'')+(refinement?`<p>Reviewed EQ/control feedback: ${refinement.accepted_EQ_steps} accepted steps with frozen training references; held-out seats did not select steps. This is a local model result.</p>`:'');
 const receiverDetails=proposal?`<p>Reviewed native controls: ${Object.keys(proposal.distances_m).map(c=>esc(c)+' '+proposal.distances_m[c].toFixed(2)+' m'+(proposal.crossovers_hz[c]===undefined?'':' / '+proposal.crossovers_hz[c]+' Hz')+(proposal.inverted_outputs.includes(c)?' / invert filter sign':'')).join(' · ')}.</p><p class="hint">Measurement context: ${esc(plan.receiver_model_assumptions?.measurement_context||'unverified')}; native gain/delay order and distance conversion remain experimental assumptions.</p>${designEvidence}`:'';
 $('transfer-plan').innerHTML=`<div class="summary-line"><span>Target receiver</span><strong>${esc(plan.model)}</strong></div><div class="summary-line"><span>Channels / tier</span><strong>${plan.channel_count} / ${esc(plan.tier)}</strong></div>${dataCheck}<div class="summary-line"><span>Initial curve / targets</span><strong>${esc(plan.initial_curve)} / ${plan.independent_curve_targets?"independent":"same EQ in both slots"}</strong></div><p class="hint">${esc(plan.curve_headroom_rule)}</p>${receiverDetails}<p class="hint">${esc(plan.notice)}${plan.original_filter_backup_available?'':' This mode has no original-filter backup. A common '+plan.global_headroom_db.toFixed(1)+' dB digital headroom is reserved; '+(plan.receiver_settings_included?'reviewed speaker levels are included.':'current channel trims are retained.')+''}</p><div class="table-scroll"><table><thead><tr><th>Channel</th><th>Final trim setting</th></tr></thead><tbody>${plan.adjustments.map(row=>`<tr><td>${esc(row.channel)}</td><td>${row.trim_db===null?'Keep current':row.trim_db.toFixed(1)+' dB'}</td></tr>`).join('')}</tbody></table></div>${precision}`;
}

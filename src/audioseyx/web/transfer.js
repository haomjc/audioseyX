let transferDigest='',transferWireChecked=false;

function initTransferControls(){
 $('template-file').onclick=()=>$('template-input').click();
 $('template-input').onchange=()=>action(async()=>{
  const file=$('template-input').files[0];
  if(file){await api('/api/template',await encodeFile(file));await refresh();toast('Saved the original OCA recovery copy');}
  $('template-input').value='';
 });
 $('prepare-transfer').onclick=()=>action(async()=>{
  toast('Refreshing device information and checking the plan...');
  await api('/api/transfer/prepare',{uncorrected_confirmed:$('uncorrected-confirmed').checked});await refresh();
 });
 $('transfer-reviewed').onchange=()=>{
  $('apply-transfer').disabled=!transferDigest||!transferWireChecked||!$('transfer-reviewed').checked;
 };
 $('apply-transfer').onclick=()=>action(async()=>{
  await api('/api/transfer/apply',{digest:transferDigest,reviewed:$('transfer-reviewed').checked});
  await refresh();page('results');
 });
}

function renderTransfer(result){
 if(!result)return;
 $('template-info').textContent=result.template?`${result.template.model} · ${result.template.channels} channels / ${result.template.original_filter_backup_available?'Original OCA saved':'Settings snapshot only; no original filters'}`:'No complete OCA has been imported.';
 const plan=result.plan;
 if((plan?.digest||'')===transferDigest)return;
 transferDigest=plan?.digest||'';
 transferWireChecked=plan?.coefficient_transport_audit?.all_streams_complete===true;
 $('transfer-reviewed').checked=false;$('apply-transfer').disabled=true;
 $('download-calibration').classList.toggle('disabled',!plan||!transferWireChecked);
 if(!plan){$('transfer-plan').innerHTML='<p>Prepare a plan to review the receiver, channel count and gain compensation.</p>';return;}
 const audit=plan.coefficient_transport_audit;
 const quantization=plan.coefficient_quantization;
 const rates=audit?Object.values(audit.sample_rate_slots_hz).map(hz=>(hz/1000)+' kHz').join(' / '):'';
 const dataCheck=audit?`<div class="summary-line"><span>Native data coverage</span><strong>${audit.channels} outputs / ${audit.streams} complete streams</strong></div><p class="hint">Both calibration curves and ${rates} slots were reconstructed and checked before transfer. These byte checks do not verify native filter reconstruction or acoustic performance.</p>`:'<p class="hint">A current native data audit is required before transfer.</p>';
 const precision=quantization?`<details><summary>Coefficient precision</summary><p>Largest absolute coefficient rounding error: ${quantization.maximum_absolute_error.toExponential(3)}. Values rounded to zero: ${quantization.underflowed_values}. This describes coefficient values, not acoustic error.</p></details>`:'';
 $('transfer-plan').innerHTML=`<div class="summary-line"><span>Target receiver</span><strong>${esc(plan.model)}</strong></div><div class="summary-line"><span>Channels / tier</span><strong>${plan.channel_count} / ${esc(plan.tier)}</strong></div>${dataCheck}<p class="hint">${esc(plan.notice)}${plan.original_filter_backup_available?'':' This mode has no original-filter backup. A common '+plan.global_headroom_db.toFixed(1)+' dB digital headroom is reserved; current channel trims are retained.'}</p><div class="table-scroll"><table><thead><tr><th>Channel</th><th>Final trim setting</th></tr></thead><tbody>${plan.adjustments.map(row=>`<tr><td>${esc(row.channel)}</td><td>${row.trim_db===null?'Keep current':row.trim_db.toFixed(1)+' dB'}</td></tr>`).join('')}</tbody></table></div>${precision}`;
}

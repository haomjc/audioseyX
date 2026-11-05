let receiverAuditKey='',inputLevelKey='';
const receiverAuditLabels={observed_read_only:'Observed in read-only data',not_verified:'Needs signal verification',not_available:'Hidden in the current menu',grayed_out:'Currently disabled',selectable:'Available in the current menu',unknown:'Unknown',not_exposed_by_this_probe:'Not exposed by inspected interfaces'};
function initReceiverAuditControls(){
 const card=document.createElement('article');card.className='card';
 card.innerHTML=`<h3>Read-only receiver evidence</h3><p>Read identity, the actual speaker menu and exposed settings from the receiver IP above. This does not play sound, write settings or select a receiver for capture. Saved reports describe their observation time.</p>
 <button class="secondary" id="receiver-audit-start">Read capabilities without playback</button><a class="button-link secondary disabled" id="receiver-audit-download" href="/download-receiver-audit">Save evidence report</a><div id="receiver-audit-result"></div>
 <h3>Computer microphone input level</h3><p>Device detection and input level do not establish microphone calibration. A level check accesses one recording input for five seconds, discards audio samples and saves numeric statistics only. It does not play sound or adjust gain.</p>
 <div class="button-row"><button class="secondary" id="audio-input-refresh">List recording inputs</button><select id="audio-input-select" aria-label="Recording input"><option value="auto">Auto-select a single microphone input</option></select></div>
 <label class="check"><input type="checkbox" id="audio-level-ready"/><span>Allow this five-second local input-level check; no audio file will be saved.</span></label><button class="secondary" id="audio-level-start">Check input level for 5 seconds</button><div id="audio-level-result"></div>`;
 $('receiver-settings-card').after(card);
 $('receiver-audit-start').onclick=()=>action(async()=>{
  const host=$('avr-host').value.trim()||state?.receiver?.host;
  if(!host)throw new Error('Enter the receiver LAN IP above before reading its capabilities.');
  await api('/api/receiver/audit',{host});await refresh();
 });
 $('audio-input-refresh').onclick=()=>action(async()=>{
  const result=await api('/api/audio-inputs');const select=$('audio-input-select');select.replaceChildren();
  for(const device of result.devices){const option=document.createElement('option');option.value=device.index;option.textContent=device.name;select.append(option)}
 });
 $('audio-level-start').onclick=()=>action(async()=>{
  if(!$('audio-level-ready').checked)throw new Error('Allow this five-second microphone access before checking levels.');
  const selected=$('audio-input-select').value;
  await api('/api/audio-inputs/level',{ready:true,device_index:selected==='auto'?undefined:Number(selected)});
  $('audio-level-ready').checked=false;await refresh();
 });
}

function renderReceiverAudit(current){
 if(!$('receiver-audit-result'))return;
 const busy=current?.job?.status==='running';$('receiver-audit-start').disabled=busy;$('audio-level-start').disabled=busy||!current?.features?.local_input_level_check;
 $('audio-input-refresh').disabled=busy||!current?.features?.local_input_level_check;
 const report=current?.receiver_audit;
 $('receiver-audit-download').classList.toggle('disabled',!report);
 const key=report?.evidence_sha256||'';
 if(key!==receiverAuditKey){
  receiverAuditKey=key;
  $('receiver-audit-result').innerHTML=report?`<p>${esc(report.model||'Model not identified')} · ${esc(report.host)} · observed ${esc(new Date(report.observed_at_utc).toLocaleString())}. HEOS component: ${esc(report.heos_component_version||'unknown')}.</p><p>Configured sub outputs: ${report.configured_subwoofer_outputs.map(esc).join(', ')||'unknown'}. Maximum reported by the menu: ${report.subwoofer_maximum_reported??'unknown'}. This does not verify wiring or separate EQ.</p><div class="table-scroll"><table><thead><tr><th>Capability</th><th>Evidence state</th><th>Scope</th></tr></thead><tbody>${report.capabilities.map(row=>`<tr><td>${esc(row.name)}</td><td>${esc(receiverAuditLabels[row.status]||row.status)}</td><td>${esc(row.detail)}</td></tr>`).join('')}</tbody></table></div><details><summary>Evidence limits</summary><p class="hint">${esc(report.notice)}</p></details>`:'No receiver evidence report has been saved.';
 }
 const level=current?.input_level;const levelKey=level?.evidence_sha256||'';
 if(levelKey!==inputLevelKey){inputLevelKey=levelKey;$('audio-level-result').innerHTML=level?`<p>${esc(level.device?.name||'Recording input')} · ${level.seconds.toFixed(2)} s · peak ${level.peak_dbfs.toFixed(1)} dBFS · RMS ${level.rms_dbfs.toFixed(1)} dBFS · clipped samples ${level.clipped_samples}. Observed ${esc(new Date(level.observed_at_utc).toLocaleString())}.</p><p class="hint">${esc(level.notice)}</p>`:'No local input-level check has been saved.'}
}

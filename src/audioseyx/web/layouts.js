let layoutChoicesLoaded=false,captureSelectionSource='';
async function loadLayoutChoices(){
 if(layoutChoicesLoaded||!state?.features?.variable_channel_layouts)return;
 layoutChoicesLoaded=true;
 try{
  const result=await api('/api/layouts');
  channelChoices.splice(0,channelChoices.length,...result.known_channels);mappingSignature='';
  $('demo-layout').innerHTML=result.presets.map(p=>`<option value="${esc(p.id)}">${esc(p.id)} · ${p.channels.length} outputs</option>`).join('');
  $('demo-layout').value='7.1.4';
 }catch(error){toast(error.message,true)}
}

function renderReceiverLayout(receiver){
 loadLayoutChoices();
 if(!receiver?.connected)return;
 const info=receiver.info||{},layout=receiver.configured_layout;
 const physical=receiver.declared_physical_layout;
 $('avr-tag').textContent='Connected';$('avr-tag').className='tag';
 $('avr-info').textContent=`${info.Model||info.ModelName||'Model not reported'} · ${info.EQType||info.EqType||'Tier not reported'} · Configured ${layout?.signature||'unmapped outputs'}${physical?' · Declared wiring '+physical.signature:''} · Stock microphone ${receiver.status?.Mic?'connected':'not connected'}`;
 const selected=receiver.capture_channels;
 const source=receiver.host+'/'+(selected||[]).join(',');
 if(source!==captureSelectionSource){
  const channels=selected||receiver.web_settings?.configured_channels||[...(layout?.bed_channels||[]),...(layout?.height_channels||[]),...(layout?.subwoofer_outputs||[])];
  $('native-channels').value=channels.join(', ');captureSelectionSource=source;
 }
 const settings=receiver.web_settings;
 $('receiver-settings-card').classList.toggle('hidden',!settings?.available);
 if(!settings?.available)return;
 const channels=settings.configured_channels;
 $('receiver-settings-summary').innerHTML=`<p>Read only · Bass mode: ${esc(settings.bass_mode||'unavailable')} · LFE low-pass: ${settings.lfe_lowpass_hz??'unavailable'} Hz · ${settings.configured_subwoofer_count??'?'} configured subwoofer outputs</p><div class="table-scroll"><table><thead><tr><th>Output</th><th>Speaker type</th><th>Crossover</th><th>Distance</th><th>Trim</th></tr></thead><tbody>${channels.map(c=>`<tr><td>${esc(c)}</td><td>${esc(settings.speaker_types?.[c]||'—')}</td><td>${settings.crossovers?.[c]===undefined?'—':settings.crossovers[c]+' Hz'}</td><td>${settings.distances_m?.[c]===undefined?'—':settings.distances_m[c].toFixed(2)+' m'}</td><td>${settings.levels_db?.[c]===undefined?'—':settings.levels_db[c].toFixed(1)+' dB'}</td></tr>`).join('')}</tbody></table></div><p class="hint">Configured outputs do not establish physical connections or independent subwoofer EQ. Reading these menus does not verify the electrical crossover slopes. HEOS component version: ${esc(info.HEOSFirmwareVersion||'unavailable')}.</p>`;
}

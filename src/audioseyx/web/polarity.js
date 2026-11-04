let polarityProject='';
function renderPolarityProject(project){
 if(!project||project.id===polarityProject)return;
 polarityProject=project.id;
 $('polarity-groups').value=(project.same_model_groups||[]).map(g=>g.join(' ')).join('; ');
 $('polarity-models-confirmed').checked=false;
}
function renderPolarityReport(report){
 $('download-polarity').classList.toggle('disabled',!report);
 if(!report){$('polarity-results').innerHTML='<p>Compare confirmed same-model speakers using original impulse responses.</p>';return}
 const label={possible_relative_inversion:'Possible relative inversion — inspect this pair',no_consistent_broadband_inversion:'No consistent broadband inversion found',indeterminate:'Inconclusive',incomplete_coverage:'Incomplete measurement coverage'};
 $('polarity-results').innerHTML=`<p><strong>Receiver reverse hints:</strong> ${report.native_reverse_hint_channels.map(esc).join(', ')||'None reported'}. These hints do not certify terminal wiring.</p><div class="table-scroll"><table><thead><tr><th>Pair</th><th>Position</th><th>Assessment</th><th>Window/repeat scenarios</th></tr></thead><tbody>${report.pairs.map(p=>`<tr><td>${p.channels.map(esc).join(' / ')}</td><td>${esc(p.position)}</td><td>${esc(label[p.classification]||p.classification)}</td><td>${p.tested_scenarios||0}</td></tr>`).join('')}</tbody></table></div><p>Not compared against a same-model peer: ${report.unpaired_channels.map(esc).join(', ')||'None'}.</p><p class="hint">${esc(report.notice)}</p>`;
}
$('check-polarity').onclick=()=>action(async()=>{
 const groups=$('polarity-groups').value.split(';').map(g=>g.trim().split(/[ ,]+/).map(c=>c.toUpperCase()).filter(Boolean)).filter(g=>g.length);
 const report=await api('/api/polarity',{groups,same_models_confirmed:$('polarity-models-confirmed').checked});
 renderPolarityReport(report);toast('Polarity report complete. No receiver settings were changed.');
});

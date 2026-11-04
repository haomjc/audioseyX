let analysisData=null,analysisSignature='',analysisBusy=false;

function renderAnalysisChoices(project){
 const signature=project?JSON.stringify([project.id,project.measurements.map(m=>[m.channel,m.position,m.name])]):'';
 if(signature!==analysisSignature){
  analysisSignature=signature;analysisData=null;
  $('analysis-measurement').innerHTML=project?project.measurements.map((m,i)=>`<option value="${i}">${esc(m.channel)} / ${esc(m.position)} · ${esc(m.name)}</option>`).join(''):'';
 }
 if(!state?.analysis_available)analysisData=null;
 $('run-analysis').disabled=analysisBusy||!project;
 $('download-analysis').classList.toggle('disabled',!analysisData);
 if(!analysisData){$('analysis-origin').textContent="Not analyzed";$('analysis-summary').textContent="Select a measurement to begin.";$('analysis-notices').textContent="Analysis does not move the impulse peak or modify the raw measurement.";$('decay-results').textContent="Analyze an impulse response to see decay fits.";$('spectrogram-resolution').textContent='';drawAnalysis();}
}

$('analysis-measurement').onchange=()=>{analysisData=null;$('download-analysis').classList.add('disabled');$('analysis-origin').textContent="Not analyzed";$('analysis-summary').textContent="Measurement changed; run analysis again.";$('decay-results').textContent="Analyze an impulse response to see decay fits.";drawAnalysis()};
$('analysis-view').onchange=drawAnalysis;
$('run-analysis').onclick=()=>action(async()=>{
 const projectId=state?.project?.id;
 analysisBusy=true;
 $('run-analysis').disabled=true;
 try{
  const result=await api('/api/analyze',{index:Number($('analysis-measurement').value)});
  if(state?.project?.id!==projectId)throw new Error("The project changed; analyze it again");
  analysisData=result;state.analysis_available=true;
  $('download-analysis').classList.remove('disabled');
  $('analysis-origin').textContent=result.project_origin==='synthetic'?"Synthetic demo / Not a real measurement":"Imported measurement analysis";
  $('analysis-origin').className='tag'+(result.project_origin==='synthetic'?' warning':'');
  $('analysis-summary').textContent=result.time_domain_available?`Peak time ${result.peak_time_ms.toFixed(2)} ms / recorded after peak ${result.record_after_peak_ms.toFixed(0)} ms / record frequency resolution ${result.spectral_resolution_hz.toFixed(2)} Hz`:"Frequency data only; time-domain analysis is unavailable.";
  $('analysis-notices').textContent=result.notices.join(' ');
  renderDecay(result.decay);drawAnalysis();toast("Measurement analysis complete");
 }finally{analysisBusy=false;$('run-analysis').disabled=!state?.project}
});

function canvasContext(id,height=320){
 const canvas=$(id),width=canvas.getBoundingClientRect().width;
 if(width<10)return null;
 const scale=devicePixelRatio||1;canvas.width=width*scale;canvas.height=height*scale;
 const ctx=canvas.getContext('2d');ctx.scale(scale,scale);ctx.clearRect(0,0,width,height);
 return {ctx,w:width,h:height};
}
function emptyChart(id,message){const setup=canvasContext(id);if(!setup)return;const {ctx,w,h}=setup;ctx.fillStyle='#78909c';ctx.font='13px Segoe UI';ctx.textAlign='center';ctx.fillText(message,w/2,h/2);}

function drawAnalysis(){
 const setup=canvasContext('analysis-chart');
 if(!setup)return;
 const {ctx,w,h}=setup;
 if(!analysisData){emptyChart('analysis-chart',"Select a measurement and choose Analyze measurement");emptyChart('spectrogram-chart',"A spectrogram requires an original impulse response");return}
 const data=analysisData,view=$('analysis-view').value,s=data.spectrum;
 let xs,ys,log=false,label='';
 if(view==='shape'){xs=s.frequency_hz;ys=s.shape_db;log=true;label="Relative response / dB"}
 else if(['group_delay','minimum_group_delay','excess_group_delay'].includes(view)){xs=s.frequency_hz;ys=s[view+'_ms'];log=true;label="Group delay / ms"}
 else if(view==='edc'){xs=data.decay?.time_ms;ys=data.decay?.edc_db;label="Time from analysis start / ms; energy / dB"}
 else{xs=data.impulse?.time_ms;ys=view==='impulse'?data.impulse?.relative_amplitude:data.impulse?.etc_db;label="Original measurement time / ms; "+(view==='impulse'?"relative amplitude":"energy / dB")}
 if(!xs||!ys){emptyChart('analysis-chart',"This measurement does not support the selected analysis");drawSpectrogram();return}
 const values=ys.filter(v=>v!==null&&Number.isFinite(v));
 if(values.length<2){emptyChart('analysis-chart',"Insufficient valid data");drawSpectrogram();return}
 let ymin=Math.min(...values),ymax=Math.max(...values);
 const padding=Math.max((ymax-ymin)*.08,.1);ymin-=padding;ymax+=padding;
 const left=60,right=20,top=25,bottom=36;
 const xmin=xs[0],xmax=xs.at(-1),tx=v=>log?Math.log10(v):v;
 const x=v=>left+(tx(v)-tx(xmin))/Math.max(tx(xmax)-tx(xmin),1e-12)*(w-left-right);
 const y=v=>top+(ymax-v)/(ymax-ymin)*(h-top-bottom);
 ctx.font='10px Segoe UI';ctx.fillStyle='#78909c';ctx.textAlign='left';ctx.fillText(label,left,13);
 for(let i=0;i<=5;i++){
  const value=ymin+(ymax-ymin)*i/5;ctx.strokeStyle='#e4ecee';ctx.beginPath();ctx.moveTo(left,y(value));ctx.lineTo(w-right,y(value));ctx.stroke();ctx.fillStyle='#78909c';ctx.textAlign='right';ctx.fillText(value.toFixed(1),left-9,y(value)+3);
 }
 const ticks=log?[20,50,100,200,500,1000,2000,5000,10000,20000]:Array.from({length:6},(_,i)=>xmin+(xmax-xmin)*i/5);
 for(const value of ticks){if(value<xmin||value>xmax)continue;ctx.strokeStyle='#edf2f3';ctx.beginPath();ctx.moveTo(x(value),top);ctx.lineTo(x(value),h-bottom);ctx.stroke();ctx.fillStyle='#78909c';ctx.textAlign='center';ctx.fillText(log?(value>=1000?value/1000+'k':value):value.toFixed(0),x(value),h-12)}
 ctx.save();ctx.beginPath();ctx.rect(left,top,w-left-right,h-top-bottom);ctx.clip();ctx.strokeStyle='#008c82';ctx.lineWidth=1.6;ctx.beginPath();let connected=false;
 xs.forEach((v,i)=>{if(ys[i]===null||!Number.isFinite(ys[i])){connected=false;return}if(connected)ctx.lineTo(x(v),y(ys[i]));else ctx.moveTo(x(v),y(ys[i]));connected=true});ctx.stroke();ctx.restore();drawSpectrogram();
}

function drawSpectrogram(){
 const map=analysisData?.spectrogram;
 if(!map){emptyChart('spectrogram-chart',"No original impulse response is available for this measurement");$('spectrogram-resolution').textContent='';return}
 const setup=canvasContext('spectrogram-chart');if(!setup)return;
 const {ctx,w,h}=setup,left=60,right=55,top=15,bottom=35;
 const times=map.time_ms,freq=map.frequency_hz,levels=map.level_db;
 const xmin=Math.log10(freq[0]),xmax=Math.log10(freq.at(-1));
 const x=f=>left+(Math.log10(f)-xmin)/(xmax-xmin)*(w-left-right),y=t=>top+(t-times[0])/(times.at(-1)-times[0]||1)*(h-top-bottom);
 for(let t=0;t<times.length;t++)for(let k=0;k<freq.length;k++){
  const a=k?Math.sqrt(freq[k-1]*freq[k]):freq[0],b=k+1<freq.length?Math.sqrt(freq[k]*freq[k+1]):freq.at(-1);
  const ya=t?y((times[t-1]+times[t])/2):top,yb=t+1<times.length?y((times[t]+times[t+1])/2):h-bottom;
  const value=Math.max(0,Math.min(1,(levels[t][k]+70)/70));ctx.fillStyle=`hsl(${250-value*250} 72% ${12+value*45}%)`;ctx.fillRect(x(a),ya,Math.max(1,x(b)-x(a)+.5),Math.max(1,yb-ya+.5));
 }
 ctx.font='10px Segoe UI';ctx.fillStyle='#78909c';ctx.textAlign='center';
 for(const f of [20,50,100,200,500])if(f>=freq[0]&&f<=freq.at(-1))ctx.fillText(f+' Hz',x(f),h-12);
 ctx.textAlign='right';for(let i=0;i<=5;i++){const t=times[0]+(times.at(-1)-times[0])*i/5;ctx.fillText(t.toFixed(0)+' ms',left-8,y(t)+3)}
 for(let i=0;i<100;i++){const value=1-i/99;ctx.fillStyle=`hsl(${250-value*250} 72% ${12+value*45}%)`;ctx.fillRect(w-right+12,top+i*(h-top-bottom)/100,10,(h-top-bottom)/100+1)}
 ctx.fillStyle='#78909c';ctx.textAlign='left';ctx.fillText('0',w-right+25,top+8);ctx.fillText('−70',w-right+25,h-bottom);ctx.fillText('dB',w-right+12,h-12);
 $('spectrogram-resolution').textContent=`STFT · ${map.window_ms.toFixed(0)} ms window / ${map.resolution_hz.toFixed(1)} Hz`;
}

function renderDecay(decay){
 if(!decay){$('decay-results').textContent="Frequency data alone cannot establish a measured decay fit.";return}
 const rows=[{label:"Broadband",fits:decay.fits},...decay.bands.map(b=>({label:b.centre_hz+' Hz',fits:b.fits,notice:b.notice}))];
 const cell=fit=>!fit?'—':fit.available?`${(fit.estimate_s*1000).toFixed(0)} ms <small>R² ${fit.fit_r2.toFixed(3)}</small>`:`<span class="subtle">${esc(fit.reason)}</span>`;
 $('decay-results').innerHTML=`<p class="hint">${esc(decay.notice)}</p><div class="table-scroll"><table><thead><tr><th>Analysis band</th><th>EDT / extrapolated indicator</th><th>T20 / 60 dB extrapolation</th><th>T30 / 60 dB extrapolation</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r.label)}${r.notice?`<small>${esc(r.notice)}</small>`:''}</td><td>${cell(r.fits.EDT)}</td><td>${cell(r.fits.T20)}</td><td>${cell(r.fits.T30)}</td></tr>`).join('')}</tbody></table></div>`;
}

$('calculate-modes').onclick=()=>action(async()=>{
 const result=await api('/api/room-modes',{length:Number($('room-length').value),width:Number($('room-width').value),height:Number($('room-height').value),high_hz:Number($('room-high').value)});
 $('room-results').innerHTML=`<p class="hint">${esc(result.notice)} Sound speed ${result.speed_m_s} m/s; ${result.total_modes} modes; showing the lowest ${Math.min(40,result.modes.length)}.</p><div class="table-scroll"><table><thead><tr><th>Frequency</th><th>Order (length, width, height)</th><th>Type</th></tr></thead><tbody>${result.modes.slice(0,40).map(m=>`<tr><td>${m.frequency_hz.toFixed(1)} Hz</td><td>${m.order.join(', ')}</td><td>${esc(m.kind)}</td></tr>`).join('')}</tbody></table></div>`;
});

$('run-benchmarks').onclick=()=>action(async()=>{
 $('run-benchmarks').disabled=true;
 try{
  const result=await api('/api/benchmarks',{});
  $('benchmark-results').innerHTML=`<p class="hint">${esc(result.notice)}</p><div class="table-scroll"><table><thead><tr><th>Numerical case</th><th>Result</th><th>Target error: before to after</th></tr></thead><tbody>${result.checks.map(c=>`<tr><td>${esc(c.name)}</td><td>${c.passed?"Passed":"Check required"}</td><td>${c.before_rms_db===undefined?'—':c.before_rms_db.toFixed(2)+' → '+c.after_rms_db.toFixed(2)+' dB'}</td></tr>`).join('')}</tbody></table></div>`;
  toast(result.all_passed?"Numerical benchmarks passed":"Some numerical benchmarks require attention",!result.all_passed);
 }finally{$('run-benchmarks').disabled=false}
});

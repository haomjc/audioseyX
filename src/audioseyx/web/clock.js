function clockTimes(){return $('clock-marker-times').value.split(',').map(s=>Number(s.trim()));}

function initClockControls(){
 $('clock-reference').onclick=()=>action(async()=>{
  const result=await api('/api/clock/reference',{marker_times_s:clockTimes()});
  $('clock-reference-download').href=result.download_url;$('clock-reference-download').classList.remove('disabled');
  toast('Reference files generated; no sound was played');
 });
 $('clock-correct').onclick=()=>action(async()=>{
  const recording=$('clock-recording-file').files[0],pilot=$('clock-pilot-file').files[0];
  if(!recording||!pilot)throw new Error('Choose an original recording WAV and the known pilot-template WAV.');
  await api('/api/clock/correct',{recording:await encodeFile(recording),pilot:await encodeFile(pilot),
   channel:Number($('clock-recording-channel').value),marker_times_s:clockTimes(),
   latency_guess_s:Number($('clock-latency').value)/1000,search_radius_s:Number($('clock-search-radius').value)/1000});
  await refresh();
 });
 $('clock-cancel').onclick=()=>action(async()=>{await api('/api/clock/cancel',{});toast('Clock correction cancellation requested');});
}

function renderClock(result,job){
 $('clock-cancel').disabled=job.status!=='running'||job.kind!=='clock';
 $('clock-correct').disabled=job.status==='running';
 $('clock-download').classList.toggle('disabled',!result);
 if(!result){$('clock-results').innerHTML='';return;}
 const a=result.analysis,m=result.mapping;
 $('clock-results').innerHTML=`<p>Estimated relative clock drift: <strong>${a.estimated_drift_ppm.toFixed(3)} ppm</strong>; pilot-fit standard error ${a.fit_standard_error_ppm.toFixed(3)} ppm. Maximum timing residual: ${a.maximum_fit_residual_samples.toFixed(3)} samples.</p><p>${a.pilots.length} known pilots; ${m.input_samples.toLocaleString()} → ${m.output_samples.toLocaleString()} recording samples. Output timing origin: ${m.output_start_time_s.toFixed(9)} s; ${m.filter_taps}-tap fractional interpolation.</p><p class="hint">${esc(result.notice)}</p>`;
}

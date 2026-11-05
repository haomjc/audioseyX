let curveProfileOptions='',lastCurveProfileProject='',lastCurveProfileResult='';

function initCurveProfileControls(){
 $('dual-calibration-curves').onchange=()=>$('reference-curve-options').classList.toggle('hidden',!$('dual-calibration-curves').checked);
 $('reference-curve-file').onclick=()=>$('reference-curve-input').click();
 $('reference-curve-input').onchange=()=>action(async()=>{
  const file=$('reference-curve-input').files[0];
  if(file){await api('/api/target-curve',{...await encodeFile(file),curve_name:'Reference'});await refresh();toast('Loaded the independent Reference target')}
  $('reference-curve-input').value='';
 });
 $('reference-curve-reset').onclick=()=>action(async()=>{await api('/api/target-curve',{curve_name:'Reference',reset:true});await refresh()});
 $('result-curve').onchange=()=>{selectedChannel='';refresh()};
}

function calibrationCurveSettings(){
 if(!$('dual-calibration-curves').checked)return {};
 if(!['minimum','receiver'].includes($('phase-mode').value))throw new Error('Choose minimum-phase EQ or native receiver planning for two native curves.');
 return {curve_profiles:{Reference:{
  low_hz:Number($('reference-low').value),high_hz:Number($('reference-high').value),sub_high_hz:Number($('reference-sub-high').value),
  max_boost_db:Number($('reference-boost').value),max_cut_db:Number($('reference-cut').value),
  bass_db:Number($('reference-bass').value),tilt_db_oct:Number($('reference-tilt').value),
  smoothing_octave:Number($('reference-smoothing').value),target_curve:null}}};
}

function activeCurveDesign(){
 const primary=state?.design;
 return $('result-curve').value==='Reference'&&primary?.curve_designs?.Reference?primary.curve_designs.Reference:primary;
}

function renderCurveProfiles(current){
 const projectId=current.project?.id||'';
 if(projectId!==lastCurveProfileProject){
  lastCurveProfileProject=projectId;lastCurveProfileResult='';
  $('dual-calibration-curves').checked=false;$('reference-curve-options').classList.add('hidden');
 }
 const target=current.reference_target_curve;
 $('reference-curve-info').textContent=target?`${target.name} · ${target.points} points / normalized at 1 kHz`:'Use Reference bass lift and tilt, or import its own target curve.';
 $('reference-bass').disabled=!!target;$('reference-tilt').disabled=!!target;
 const primary=current.design;
 const dual=!!primary?.curve_designs?.Reference;
 if(primary){
  const resultSignature=primary.project_id+'|'+JSON.stringify(primary.settings.curve_profiles||null);
  if(resultSignature!==lastCurveProfileResult){
   lastCurveProfileResult=resultSignature;$('dual-calibration-curves').checked=dual;
   $('reference-curve-options').classList.toggle('hidden',!dual);
   if(dual){
    const settings=primary.curve_designs.Reference.settings;
    for(const [id,key] of [['reference-low','low_hz'],['reference-high','high_hz'],['reference-sub-high','sub_high_hz'],['reference-boost','max_boost_db'],['reference-cut','max_cut_db'],['reference-bass','bass_db'],['reference-tilt','tilt_db_oct'],['reference-smoothing','smoothing_octave']]){
     if(settings[key]!==undefined)$(id).value=settings[key];
    }
   }
  }
 }
 const signature=dual?'dual':'single';
 if(signature!==curveProfileOptions){
  const previous=$('result-curve').value;
  $('result-curve').innerHTML=dual?'<option value="Flat">Flat</option><option value="Reference">Reference</option>':'<option value="Flat">Current design</option>';
  $('result-curve').value=dual&&previous==='Reference'?'Reference':'Flat';curveProfileOptions=signature;
 }
 const note=primary?.curve_configuration?.notice;
 $('curve-result-note').classList.toggle('hidden',!note);
 $('curve-result-note').textContent=note||'';
}

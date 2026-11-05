function initNativeRefinementControls(){
 const panel=document.createElement('details');
 panel.innerHTML=`<summary>EQ feedback and measurement robustness</summary>
 <label class="check"><input id="native-eq-refinement" type="checkbox"/><span>Iterate complete minimum-phase EQ and receiver controls. Requires the one-sub timing/routing search; calculation takes longer.</span></label>
 <div class="form-grid">
 <label>EQ feedback rounds<input id="native-refinement-rounds" type="number" min="1" max="12" value="3"/></label>
 <label>Maximum EQ proposal step / dB<input id="native-refinement-step" type="number" min="0.05" max="3" step="0.05" value="0.5"/></label>
 <label>Repeat weighting<select id="native-repeat-weighting"><option value="uniform">Equal repeats</option><option value="consistency">Consistency, at least three repeats</option></select></label>
 <label>Minimum relative repeat weight<input id="native-repeat-floor" type="number" min="0.01" max="1" step="0.05" value="0.25"/></label>
 <label>Main gain uncertainty / ±dB<input id="native-main-uncertainty" type="number" min="0" max="3" step="0.05" value="0"/></label>
 <label>Sub gain uncertainty / ±dB<input id="native-sub-uncertainty" type="number" min="0" max="3" step="0.05" value="0"/></label>
 <label>Relative sub timing uncertainty / ±ms<input id="native-timing-uncertainty" type="number" min="0" max="5" step="0.05" value="0"/></label></div>
 <p class="hint">EQ candidates keep the same physical targets and full filter lengths. Only feasible improvements in the training objective are accepted; excluded seats never select a candidate. Uncertainty radii are declared assumptions, not measured error bounds. Every repeat remains present. Nonzero radii evaluate the complete centre/endpoint Cartesian ensemble and require more memory. This is a finite scenario model, not a continuous worst-case guarantee.</p>`;
 $('native-calibration-options').append(panel);
 const card=document.createElement('article');card.className='card';
 card.innerHTML='<h3>Saved design runs</h3><p>Review completed exports and restore processing controls for these exact measurements. Timing, level and transfer confirmations require fresh review. Original receiver EQ coefficients are not backed up.</p><button id="load-design-history">Show saved runs</button><div id="design-history-list"></div>';
 $('page-results').append(card);
 $('load-design-history').onclick=()=>action(loadDesignHistory);
}

function nativeRefinementSettings(){
 if(!state?.features?.native_eq_refinement)throw new Error('Restart audioseyX to use EQ feedback and measurement robustness.');
 return {receiver_eq_refinement:$('native-eq-refinement').checked&&$('native-time-search').checked,
  receiver_refinement_rounds:Number($('native-refinement-rounds').value),
  receiver_refinement_step_db:Number($('native-refinement-step').value),
  receiver_repeat_weighting:$('native-repeat-weighting').value,
  receiver_repeat_weight_floor:Number($('native-repeat-floor').value),
  receiver_main_uncertainty_db:Number($('native-main-uncertainty').value),
  receiver_sub_uncertainty_db:Number($('native-sub-uncertainty').value),
  receiver_timing_uncertainty_ms:Number($('native-timing-uncertainty').value)};
}

function nativeRefinementReport(result){
 const refinement=result.eq_refinement,quality=result.measurement_robustness;
 const iterative=refinement?`<p>EQ/control feedback: ${refinement.accepted_EQ_steps} accepted EQ steps; fixed-target training objective ${refinement.initial_objective.toFixed(6)} → ${refinement.final_objective.toFixed(6)}. Stop: ${esc(refinement.stop_reason)}. Excluded positions were not used to select steps.</p>`:'';
 const robust=quality?`<p>Repeat weighting: ${esc(quality.repeat_confidence.mode)}; ${quality.repeat_confidence.records_discarded} records discarded. ${quality.uncertainty_case_count} declared gain/timing cases. ${quality.used_in_routed_objective?'All cases participate in the routed objective.':'Routed uncertainty scoring is unavailable in this level-only proposal.'}</p>`:'';
 const baseline=result.search?.baseline_admissible===false?'<p class="hint">The baseline control state is outside the declared feasible grid or coefficient reserve. EQ feedback is compared with its feasible starting proposal. The unprocessed baseline shown separately is not an admissible search candidate.</p>':'';
 return iterative+robust+baseline;
}

async function loadDesignHistory(){
 const result=await api('/api/design-history');
 const box=$('design-history-list');box.replaceChildren();
 const note=document.createElement('p');note.textContent=`${result.total_runs} saved runs; ${result.invalid_receipts} invalid receipts. Showing up to 50 recent runs.`;box.append(note);
 for(const run of result.runs){
  const row=document.createElement('p');
  const label=document.createElement('span');label.textContent=`${new Date(run.created_ns/1e6).toLocaleString()} · ${run.status} · ${run.dataset_matches?'current measurements':'different measurements'} `;row.append(label);
  if(run.status==='complete'&&run.export_available){
   const download=document.createElement('a');download.href='/api/design-history/export?id='+encodeURIComponent(run.id);download.textContent='Download verified export';row.append(download);
   if(run.dataset_matches){const restore=document.createElement('button');restore.textContent='Restore controls';restore.onclick=()=>action(async()=>{
    const recipe=await api('/api/design-history/recipe',{id:run.id});
    for(const input of $('page-design').querySelectorAll('input,select')){
     if(input.tagName==='SELECT'){const option=[...input.options].find(o=>o.defaultSelected)||input.options[0];if(option)input.value=option.value}
     else if(input.type==='checkbox')input.checked=input.defaultChecked;
     else if(input.type!=='file')input.value=input.defaultValue;
    }
    $('native-same-model').value=(recipe.settings.same_model_groups||state.project.same_model_groups||[]).map(g=>g.join(', ')).join('; ');
    $('dual-calibration-curves').checked=!!recipe.settings.curve_profiles?.Reference;
    $('reference-curve-options').classList.toggle('hidden',!$('dual-calibration-curves').checked);
    processingControlsProject='';restoreProcessingControls({...state,saved_design_settings:recipe.settings});
    toast('Processing controls restored. Review references and targets before running a new design.');page('design');
   });row.append(restore)}
  }
  if(run.error){const error=document.createElement('span');error.textContent=' · '+run.error;row.append(error)}
  box.append(row);
 }
}

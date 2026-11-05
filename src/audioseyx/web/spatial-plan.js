let spatialDraft=null,spatialDirty=false,spatialSource='',spatialCurrentProject=null,spatialProjectID='';

function initSpatialPlanner(){
 const card=document.createElement('article');card.className='card';card.id='spatial-planner';
 card.innerHTML=`<h3>Microphone positions and measurement plan</h3>
 <p id="spatial-summary">Import measurements to review their actual positions.</p>
 <p class="hint">A location, a seat and a repeat are different things. Draft points declare intended work; they do not create responses or retrieve a previous TV-guided calibration.</p>
 <div class="form-grid"><label>Plan template<select id="spatial-preset"><option value="three_seat">Three-seat row</option><option value="single_area">One listening area</option><option value="observed">Use observed position IDs</option></select></label>
 <label>Planned microphone positions<input id="spatial-count" type="number" min="1" max="256" value="8"/></label>
 <label>Target repeats per output<input id="spatial-repeats" type="number" min="1" max="5" value="1"/></label></div>
 <button id="spatial-draft">Create draft plan</button><button id="spatial-next">Prepare next missing position</button>
 <div id="spatial-editor" class="hidden"><div class="form-grid"><label>Plan name<input id="spatial-name" maxlength="180"/></label><label>Expected wired outputs / CSV<input id="spatial-outputs"/></label></div>
 <div id="spatial-seats" class="form-grid"></div><button id="spatial-add-seat">Add seat / area</button>
 <div class="table-scroll"><table><thead><tr><th>Position ID</th><th>Seat / area</th><th>Use</th><th>Within-seat weight</th><th>Target repeats</th><th>Placement note</th><th>Actual records</th><th>Action</th></tr></thead><tbody id="spatial-positions"></tbody></table></div>
 <button id="spatial-add-point">Add microphone position</button><button id="spatial-check">Check draft coverage</button><button class="primary" id="spatial-save">Save measurement plan</button><p id="spatial-preview" class="hint"></p></div>`;
 $('page-measure').prepend(card);
 const form=$('native-position').parentElement.parentElement;
 const identity=document.createElement('label');identity.innerHTML='Spatial position ID / optional<input id="native-position-id" maxlength="80" placeholder="Defaults to p0 for position number 1"/>';form.append(identity);
 const mode=document.createElement('label');mode.innerHTML='<span id="native-repeat-meaning">Capture scope</span><select id="native-capture-mode"><option value="resume">Fill missing responses up to target repeats</option><option value="append">Add the requested repeats to every selected output</option></select>';form.append(mode);
 const repeatLabel=document.createElement('span');repeatLabel.id='native-repeat-target-label';const repeatInput=$('native-repeats');repeatInput.parentElement.replaceChildren(repeatLabel,repeatInput);
 $('native-capture-mode').onchange=updateCaptureRepeatLabel;updateCaptureRepeatLabel();
 const preview=document.createElement('button');preview.id='native-preview';preview.className='secondary';preview.textContent='Preview capture scope';$('native-start').parentElement.prepend(preview);
 const detail=document.createElement('p');detail.id='native-capture-preview';detail.className='hint';$('native-start').parentElement.after(detail);
 $('spatial-draft').onclick=()=>action(async()=>{
  const result=await api('/api/spatial-plan/preview',{preset:$('spatial-preset').value,count:Number($('spatial-count').value),repeats:Number($('spatial-repeats').value),centre_priority:Number($('centre-priority').value)/100});
  spatialDraft=result.plan;spatialDirty=true;renderSpatialDraft();showSpatialCoverage(result.inventory,'Draft only; not saved. ');
 });
 $('spatial-add-seat').onclick=()=>{if(!spatialDraft)return;let n=spatialDraft.seats.length+1;while(spatialDraft.seats.some(s=>s.id==='seat'+n))n++;spatialDraft.seats.push({id:'seat'+n,label:'Seat '+n,weight:1});spatialDirty=true;renderSpatialDraft()};
 $('spatial-add-point').onclick=()=>{if(!spatialDraft)return;let n=0;while(spatialDraft.positions.some(p=>p.id==='p'+n))n++;spatialDraft.positions.push({id:'p'+n,seat:spatialDraft.seats[0].id,role:'training',weight:1,repeats:1,label:'Choose actual microphone placement'});spatialDirty=true;renderSpatialDraft()};
 $('spatial-check').onclick=()=>action(async()=>{const result=await api('/api/spatial-plan/preview',{plan:readSpatialDraft()});showSpatialCoverage(result.inventory,'Draft only; ')});
 $('spatial-save').onclick=()=>action(async()=>{await api('/api/spatial-plan',{plan:readSpatialDraft()});spatialDirty=false;spatialSource='';await refresh();toast('Measurement plan saved. No responses were created and no hardware was contacted.')});
 $('spatial-next').onclick=()=>action(()=>{
  if(spatialDirty)throw new Error('Save or discard the draft before preparing a capture.');
  const inventory=spatialCurrentProject?.spatial_inventory;
  if(!inventory?.has_explicit_plan)throw new Error('Save an explicit microphone-position plan first.');
  const next=inventory.positions.find(p=>p.status!=='complete');
  if(!next){toast('Every planned output/repeat cell is complete.');return}
  prepareSpatialCapture(next);
 });
 $('native-preview').onclick=()=>action(async()=>showCapturePreview(await api('/api/native-measure/preview',spatialCaptureBody())));
 $('native-start').onclick=()=>action(async()=>{
  const body=spatialCaptureBody();if(!body.ready)throw new Error('Position the microphone and confirm readiness before requesting test tones.');
  const preview=await api('/api/native-measure/preview',body);showCapturePreview(preview);
  if(!preview.responses_to_capture)throw new Error('This point already meets the requested repeat target.');
  await api('/api/native-measure/start',{...body,preview_sha256:preview.preview_sha256});
  $('native-ready').checked=false;await refresh();toast('Capturing this microphone position. Move it only after this run ends.');
 });
}

function readSpatialDraft(){
 if(!spatialDraft)throw new Error('Create a draft measurement plan first.');
 spatialDraft.name=$('spatial-name').value;
 spatialDraft.expected_channels=$('spatial-outputs').value.split(',').map(c=>c.trim().toUpperCase()).filter(Boolean);
 return spatialDraft;
}

function spatialInput(value,label,type='text',changed){
 const input=document.createElement('input');input.type=type;input.value=value;input.setAttribute('aria-label',label);
 if(type==='number'){input.min='0.000001';input.step='any'}
 input.oninput=()=>{changed(type==='number'?Number(input.value):input.value);spatialDirty=true};
 return input;
}

function renderSpatialDraft(){
 $('spatial-editor').classList.toggle('hidden',!spatialDraft);if(!spatialDraft)return;
 $('spatial-name').value=spatialDraft.name;$('spatial-outputs').value=spatialDraft.expected_channels.join(', ');
 $('spatial-name').oninput=()=>{spatialDraft.name=$('spatial-name').value;spatialDirty=true};
 $('spatial-outputs').oninput=()=>{spatialDraft.expected_channels=$('spatial-outputs').value.split(',').map(c=>c.trim().toUpperCase()).filter(Boolean);spatialDirty=true};
 const seats=$('spatial-seats');seats.replaceChildren();
 for(const seat of spatialDraft.seats){
  const group=document.createElement('div');const name=document.createElement('label');name.textContent=seat.id+' name';const input=spatialInput(seat.label,seat.id+' name','text',v=>seat.label=v);input.onchange=()=>renderSpatialDraft();name.append(input);group.append(name);
  const label=document.createElement('label');label.textContent='Seat priority';label.append(spatialInput(seat.weight,seat.id+' priority','number',v=>seat.weight=v));group.append(label);
  const remove=document.createElement('button');remove.textContent='Remove empty seat';remove.disabled=spatialDraft.positions.some(p=>p.seat===seat.id);remove.onclick=()=>{spatialDraft.seats=spatialDraft.seats.filter(s=>s!==seat);spatialDirty=true;renderSpatialDraft()};group.append(remove);seats.append(group);
 }
 const body=$('spatial-positions');body.replaceChildren();
 const records=spatialCurrentProject?.measurements||[];
 for(const row of spatialDraft.positions){
  const tr=document.createElement('tr');const cell=element=>{const td=document.createElement('td');td.append(element);tr.append(td)};
  const id=spatialInput(row.id,'Position ID '+row.id,'text',v=>row.id=v);id.readOnly=records.some(m=>m.position===row.id);cell(id);
  const seat=document.createElement('select');seat.setAttribute('aria-label','Seat for '+row.id);
  for(const s of spatialDraft.seats){const option=document.createElement('option');option.value=s.id;option.textContent=s.label;seat.append(option)}seat.value=row.seat;seat.onchange=()=>{row.seat=seat.value;spatialDirty=true};cell(seat);
  const role=document.createElement('select');role.setAttribute('aria-label','Role for '+row.id);
  for(const value of ['training','validation']){const option=document.createElement('option');option.value=value;option.textContent=value;role.append(option)}role.value=row.role;role.onchange=()=>{row.role=role.value;spatialDirty=true};cell(role);
  cell(spatialInput(row.weight,'Within-seat weight '+row.id,'number',v=>row.weight=v));
  const repeats=spatialInput(row.repeats,'Target repeats '+row.id,'number',v=>row.repeats=v);repeats.min='1';repeats.max='5';repeats.step='1';cell(repeats);
  cell(spatialInput(row.label,'Placement note '+row.id,'text',v=>row.label=v));
  const actual=document.createElement('span');actual.textContent=records.filter(m=>m.position===row.id).length.toString();cell(actual);
  const actions=document.createElement('div');const prepare=document.createElement('button');prepare.textContent='Prepare capture';prepare.onclick=()=>windowAction(row);actions.append(prepare);
  const remove=document.createElement('button');remove.textContent='Remove unmeasured point';remove.disabled=records.some(m=>m.position===row.id);remove.onclick=()=>{spatialDraft.positions=spatialDraft.positions.filter(p=>p!==row);spatialDirty=true;renderSpatialDraft()};actions.append(remove);cell(actions);body.append(tr);
 }
 function windowAction(row){action(()=>{if(spatialDirty)throw new Error('Save the edited plan before preparing capture controls.');prepareSpatialCapture(row)})}
}

function renderSpatialPlanner(project){
 if(!$('spatial-planner'))return;
 spatialCurrentProject=project;
 if(!project){$('spatial-summary').textContent='Import measurements before planning positions.';return}
 if(project.id!==spatialProjectID){spatialDirty=false;spatialSource='';spatialProjectID=project.id}
 const inventory=project.spatial_inventory;
 $('spatial-summary').textContent=`Actual data: ${inventory?.measured_positions??project.positions.length} microphone locations / ${project.measurement_count} response records. ${inventory?.has_explicit_plan?'Planned: '+inventory.planned_positions+' locations; '+inventory.positions.filter(p=>p.status==='complete').length+' complete output/repeat sets.':'No explicit spatial plan saved. Existing listening-area presets do not create measurements.'}`;
 const source=project.id+'/'+JSON.stringify(project.spatial_plan)+'/'+JSON.stringify(project.measurements.map(m=>[m.channel,m.position]));
 if(source!==spatialSource&&!spatialDirty){spatialDraft=project.spatial_plan?JSON.parse(JSON.stringify(project.spatial_plan)):null;spatialSource=source;renderSpatialDraft()}
 if(inventory?.has_explicit_plan&&!spatialDirty)showSpatialCoverage(inventory,'Saved plan. ');
}

function showSpatialCoverage(inventory,prefix=''){
 $('spatial-preview').textContent=prefix+inventory.measured_positions+' actual locations; '+inventory.records+' records. '+
  (inventory.complete?'Every planned output/repeat cell is complete.':'Pending training: '+(inventory.pending_training_positions.join(', ')||'none')+'; pending validation: '+(inventory.pending_validation_positions.join(', ')||'none')+'.')+' No positions are synthesized.';
}

function prepareSpatialCapture(row){
 $('native-position-id').value=row.id;const match=/^p(\d+)$/.exec(row.id);
 if(match&&Number(match[1])<20)$('native-position').value=Number(match[1])+1;
 $('native-position-note').value=row.label;$('native-repeats').value=row.repeats;
 $('native-channels').value=spatialCurrentProject.spatial_plan.expected_channels.join(', ');
 $('native-capture-mode').value='resume';$('native-new-project').checked=false;$('native-ready').checked=false;
 updateCaptureRepeatLabel();
 $('native-capture-preview').textContent='Prepared '+row.id+'. Place the microphone at the declared location, then preview/confirm this point. No test tone has started.';
 $('native-position-id').focus();toast('Capture controls prepared; microphone readiness remains unconfirmed.');
}

function spatialCaptureBody(){return {position:Number($('native-position').value),position_id:$('native-position-id').value.trim()||undefined,
 position_note:$('native-position-note').value,repeats:Number($('native-repeats').value),mode:$('native-capture-mode').value,
 channels:$('native-channels').value.trim()?$('native-channels').value.split(',').map(c=>c.trim().toUpperCase()).filter(Boolean):undefined,
 ready:$('native-ready').checked,new_project:$('native-new-project').checked}}

function updateCaptureRepeatLabel(){$('native-repeat-target-label').textContent=$('native-capture-mode').value==='resume'?'Target total repeats per output':'Additional repeats per output'}

function showCapturePreview(result){$('native-capture-preview').textContent=result.position+' · '+result.responses_to_capture+' responses to capture. '+
 result.batches.map(b=>b.channels.join('/')+' × '+b.repeats).join('; ')+'. Preview reads local records only; it does not contact the receiver or play sound.'}

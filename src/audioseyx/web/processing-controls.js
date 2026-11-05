let processingControlsProject='';
function restoreProcessingControls(current){
 const project=current.project;
 if(!project||processingControlsProject===project.id)return;
 processingControlsProject=project.id;
 $('restored-controls-note').classList.add('hidden');
 const saved=current.saved_design_settings;
 if(!saved)return;
 const controls={
  'tier':'tier','low-hz':'low_hz','high-hz':'high_hz','sub-high-hz':'sub_high_hz','boost-db':'max_boost_db',
  'cut-db':'max_cut_db','bass-db':'bass_db','tilt-db':'tilt_db_oct','phase-high-hz':'phase_high_hz','delay-ms':'delay_ms','crossover-hz':'crossover_hz',
  'joint-phase-target':'joint_phase_target','joint-delay':'joint_delay_ms','joint-risk':'position_risk','joint-iterations':'joint_iterations',
  'joint-bound-tolerance':'joint_bound_tolerance_db','joint-memory':'joint_memory_mib','fdw-cycles':'fdw_cycles','joint-fdw-reference':'fdw_reference','fdw-start':'fdw_start_hz',
  'joint-large-channels':'large_channels','joint-crossover-model':'crossover_model','joint-bass-mode':'bass_mode','joint-lfe-lowpass':'lfe_lowpass_hz',
  'native-max-delay':'receiver_max_delay_ms','native-distance-speed':'receiver_delay_speed_m_s','native-memory':'receiver_memory_mib',
  'native-sub-min':'receiver_sub_level_min_db','native-sub-max':'receiver_sub_level_max_db','native-lfe-priority':'receiver_lfe_priority',
  'native-position-risk':'receiver_position_risk','native-analysis-low':'receiver_analysis_low_hz','native-analysis-high':'receiver_analysis_high_hz',
  'native-measurement-context':'receiver_measurement_context','curve-control-mode':'receiver_curve_control_mode','curve-risk':'receiver_curve_risk','curve-risk-temperature':'receiver_curve_risk_temperature'};
 for(const [id,key] of Object.entries(controls)){if($(id)&&saved[key]!==undefined)$(id).value=Array.isArray(saved[key])?saved[key].join(', '):saved[key]}
 for(const [id,key] of [['temporal-diagnostics','temporal_diagnostics'],['native-auditory-balance','receiver_auditory_balance'],['native-time-search','receiver_time_crossover_search'],['native-sub-level-search','receiver_sub_level_search']]){
  if(saved[key]!==undefined)$(id).checked=saved[key]===true;
 }
 const mode=saved.optimizer==='native_calibration'?'receiver':saved.optimizer==='native_joint'?'joint':saved.phase_mode||'minimum';
 $('phase-mode').value=mode;$('phase-mode').dispatchEvent(new Event('change'));
 for(const id of ['timing-confirmed','native-reference-confirmed','joint-levels-confirmed','joint-routing-confirmed','joint-subs-confirmed','allow-partial-area'])$(id).checked=false;
 $('native-distance-basis').value='preserve';
 if(saved.validation_positions){for(const id of ['native-validation','joint-validation'])$(id).value=saved.validation_positions.join(', ')}
 if(saved.receiver_crossover_candidates_hz)$('native-crossover-candidates').value=Array.isArray(saved.receiver_crossover_candidates_hz)?saved.receiver_crossover_candidates_hz.join(', '):saved.receiver_crossover_candidates_hz;
 if(saved.receiver_routing_models)$('native-routing-model').value=saved.receiver_routing_models.length>1?'robust':saved.receiver_routing_models[0];
 if(saved.receiver_curve_weights?.Reference!==undefined){const w=saved.receiver_curve_weights;$('curve-flat-priority').value=100*w.Flat/(w.Flat+w.Reference)}
 const reference=saved.curve_profiles?.Reference;
 if(reference){
  $('dual-calibration-curves').checked=true;$('reference-curve-options').classList.remove('hidden');
  for(const [id,key] of [['reference-low','low_hz'],['reference-high','high_hz'],['reference-sub-high','sub_high_hz'],['reference-boost','max_boost_db'],['reference-cut','max_cut_db'],['reference-bass','bass_db'],['reference-tilt','tilt_db_oct'],['reference-smoothing','smoothing_octave']])if(reference[key]!==undefined)$(id).value=reference[key];
 }
 $('restored-controls-note').classList.remove('hidden');
 $('restored-controls-note').textContent='Processing controls restored for these exact measurements. Timing/level and provisional-row confirmations require review; relative distances default to Preserve. No calculation, playback or transfer started.';
}

'use strict';
const icons={map:'<path d="m3 5 6-2 6 2 6-2v16l-6 2-6-2-6 2Z"/><path d="M9 3v16M15 5v16"/>',timer:'<circle cx="12" cy="14" r="8"/><path d="M12 10v4l3 2M9 2h6M12 2v4M18 6l2-2"/>',chest:'<path d="M3 11h18v10H3zM3 11V7a3 3 0 0 1 3-3h12a3 3 0 0 1 3 3v4M8 4v7M16 4v7M3 15h7m4 0h7M10 13h4v5h-4z"/>',settings:'<path d="m9 3 1 3h4l1-3 3 2-1 3 2 3 3 1-1 4-3-1-2 3v3h-4l-1-3-3-1-3 1-2-3 2-2V9L2 7l3-3 3 1Z"/><circle cx="12" cy="12" r="3"/>',swords:'<path d="m4 3 15 15m-4 2 5-5M3 3l1 5 3-1V4Zm17 0L5 18m-1-3 5 5M21 3l-1 5-3-1V4Z"/>',arrow:'<path d="M4 12h16m-6-6 6 6-6 6"/>',play:'<path d="m8 4 12 8-12 8Z"/>',info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/>',check:'<path d="m5 12 4 4L20 5"/>',pin:'<path d="M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 1 1 14 0Z"/><circle cx="12" cy="10" r="2"/>',hand:'<path d="M8 13V5a2 2 0 0 1 4 0v7-5a2 2 0 0 1 4 0v5-3a2 2 0 0 1 4 0v8c0 7-10 7-12 3l-5-6c-1-2 2-3 3-1l2 2"/>',link:'<path d="m9 15 6-6m-6 9-2 2a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0m0 6a4 4 0 0 0 6 0l5-5a4 4 0 0 0-6-6l-2 2"/>',close:'<path d="m6 6 12 12M6 18 18 6"/>'};
const svg=name=>`<svg viewBox="0 0 24 24" aria-hidden="true">${icons[name]||icons.info}</svg>`;
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=(v,d=0)=>Number.isFinite(Number(v))?Number(v).toLocaleString('en-US',{maximumFractionDigits:d}):'—';
let data=null,zones=[],view='explore',selected=Number(localStorage.getItem('afk-slot')??2),room=localStorage.getItem('afk-room')||'Act_03_03',hours=2,world='old',signature='',pending=false,zoom=1.15,pan={x:0,y:0};
let mapGesture=null,mapBusyUntil=0;
const current=()=>data?.characters.find(c=>c.slot===selected);
const sameHero=(a,b)=>!!a&&!!b&&a.identity_version===2&&b.identity_version===2&&a.slot===b.slot&&a.name===b.name&&a.class===b.class;
const visibleCapture=()=>data?.calibration?.running?data.calibration:sameHero(data?.calibration?.character,current())?data.calibration:null;
const regularRegion=r=>/^Act_\d{2}_\d{2}$/.test(r||'');
function calibrationReadiness(){
  const hero=current(),live=data?.live;
  if(!hero)return 'Select a hero to calibrate.';
  if(!live)return data?.game_running?'Waiting for a fresh game connection.':'Recording requires the selected hero in the game. To use an existing calibration, open the expedition planner; the game can stay closed.';
  if(!sameHero(hero,live.character))return `In game: ${live.character?.name||'no hero loaded'}. To record this hero, select ${hero.name} (slot ${hero.slot+1}) on the game’s character selection screen. A saved expedition profile does not require switching heroes.`;
  if(!regularRegion(live.room))return `Current location: ${roomName(live.room)}. Enter a regular Act region to record combat.`;
  if(live.replay_running)return 'Wait for reward delivery to finish.';
  if(live.reward_baseline?.available===false)return live.reward_baseline.error||'Independent reward baseline is unavailable.';
  if(live.capture_on&&!data.calibration?.running)return 'Another recording is active. Finish it before starting a new one.';
  return '';
}
function deliveryActive(){
  const pr=data?.progress||{};
  return !!data?.armed&&['claim','claim_background'].includes(data.job?.action)&&data.job.state==='running'
    &&!pr.reconciliation_required&&!['error','aborted'].includes(pr.state)
    &&(!pr.expedition_id||pr.expedition_id===data.armed.expedition_id+'_claim');
}
function canSettlePartial(){return data?.recovery?.status==='needs_review'&&!data.recovery.recoverable;}
function problemBox(){
  if(!canSettlePartial())return '<div class="error-box">The previous claim has an uncertain outcome. Another claim is blocked to prevent duplicate rewards. Check the activity log.</div>';
  const r=data.recovery;
  return `<div class="error-box">The previous claim stopped with an uncertain outcome at ${num(r.calls_done||0)} of ${num(r.calls_total||0)} reward calls. It cannot continue automatically, which prevents duplicate rewards. Close it as a partial delivery to keep what was delivered and start a new expedition.</div>`;
}
function settlePartialButton(){
  const r=data.recovery,left=Math.max(0,(r.calls_total||0)-(r.calls_done||0));
  return `<button class="primary" data-settle-partial ${busy()?'disabled':''}>${svg('chest')} Close as partial delivery</button><div class="button-note">Keeps the rewards already delivered and gives up the remaining ${num(left)} reward calls. Nothing is generated again. <a href="/api/recovery-report?id=${encodeURIComponent(r.id||'')}" target="_blank" rel="noopener">Recovery report</a></div>`;
}
function deliveryNeedsReview(){
  return !!data?.armed&&(data.progress?.reconciliation_required||(!deliveryActive()&&data.recovery?.status==='needs_review'));
}
function deliveryTitle(){
  return data.progress.state==='paused'?'Delivery paused':data.progress.state==='done'?'Finishing reward delivery':'Delivering rewards';
}
function deliveryMessage(){
  const hero=data.plan?.character?.name||'the expedition hero',region=roomName(data.plan?.zones?.[0]?.room);
  if(data.progress.state==='paused')return `Return as ${hero} to ${region}. This delivery will continue from its saved position; do not start another claim.`;
  if(data.progress.state==='done')return 'Reward calls are complete. Waiting for the final save and Vault transfer result.';
  return `Keep ${hero} in ${region} and leave the game open until delivery finishes. Generated items appear below when delivery is complete.`;
}
function gameStatus(){
  return deliveryActive()?deliveryTitle():data.live?'In game: '+(data.live.character?.name||'menu'):data.game_running?'Waiting for plugin':'Game closed';
}
function deliveryCard(){
  if(!deliveryActive())return '';
  const pr=data.progress;
  return `<section class="card extra-section"><span class="eyebrow">CURRENT REWARD DELIVERY</span><h2>${deliveryTitle()}</h2><p class="muted">${esc(deliveryMessage())}</p><div class="progress-track"><i id="delivery-progress" style="width:${Number(pr.percent)||0}%"></i></div><div class="estimate"><div class="label-row"><span>Reward calls</span><strong id="delivery-calls">${num(pr.calls_done||0)} / ${num(pr.calls_total||0)}</strong></div><div class="label-row"><span>Time left</span><strong id="delivery-eta">${esc(deliveryEtaText())}</strong></div><div class="label-row"><span>Generated items</span><strong id="delivery-items">${num(pr.items||0)}</strong></div><div class="label-row"><span>Hidden by your loot filter</span><strong id="delivery-filtered">${num(pr.items_filtered||0)}</strong></div></div>${pauseButton()}<p class="settings-note">Keep Hero Siege open until delivery finishes. Closing the game window pauses and saves it; claim again to continue. Counts are provisional; the save and Vault transfer are confirmed separately.</p></section>`;
}
// Delivery time: measured on this computer (delivery-rate.json) before a claim,
// and from the live progress of the running one.
let speedDraft=null;const etaSamples=[];
const speedLabel=id=>({normal:'Normal',fast:'Fast',max:'Maximum'}[id]||'Normal');
function chosenSpeed(){return speedDraft||data?.preferences?.delivery_speed||'normal';}
function duration(seconds){
  if(!Number.isFinite(seconds))return '—';
  const s=Math.max(0,Math.round(seconds));
  return s<90?'under 2 min':s<3600?`about ${Math.round(s/60)} min`:`about ${Math.floor(s/3600)} h ${Math.round(s%3600/60)} min`;
}
function sampleDelivery(){
  const pr=data?.progress||{},done=Number(pr.calls_done);
  if(!deliveryActive()||pr.state!=='running'||!Number.isFinite(done)){etaSamples.length=0;return;}
  const now=Date.now();
  if(!etaSamples.length||etaSamples.at(-1).done!==done)etaSamples.push({at:now,done});
  while(etaSamples.length>2&&now-etaSamples[0].at>60000)etaSamples.shift();
}
function deliveryEtaText(){
  const pr=data?.progress||{},left=Math.max(0,(pr.calls_total||0)-(pr.calls_done||0));
  if(pr.state==='paused')return 'Paused';
  const first=etaSamples[0],last=etaSamples.at(-1);
  if(first&&last&&last.at-first.at>=5000&&last.done>first.done)return duration(left/((last.done-first.done)/((last.at-first.at)/1000)));
  const speed=data?.delivery?.speeds?.find(s=>s.id===(data.delivery.active_speed||chosenSpeed()));
  return speed?duration(left/speed.calls_per_second)+' (estimate)':'Measuring…';
}
function speedChoice(paused){
  if(paused)return `<p class="settings-note">Delivery speed: ${esc(speedLabel(data.delivery?.active_speed))} (kept from the paused delivery).</p>`;
  const speeds=data.delivery?.speeds||[],chosen=chosenSpeed();
  const note={normal:'Smooth game while delivering.',fast:'Lower frame rate while delivering.',max:'The game may stutter. Best when you are away.'};
  return `<div class="label-row"><label for="delivery-speed">DELIVERY SPEED</label><select id="delivery-speed" class="settings-input speed-select">${speeds.map(s=>`<option value="${esc(s.id)}" ${s.id===chosen?'selected':''}>${esc(s.label)} · ${esc(duration(s.seconds))}</option>`).join('')}</select></div><p class="settings-note" id="speed-note">${esc(note[chosen]||'')} Times are measured on this computer and improve after each claim.</p>`;
}
function pauseButton(){
  return `<button class="quiet" data-action="pause_delivery" ${data.progress?.state==='running'||data.progress?.state==='paused'?'':'disabled'}>Pause and save delivery</button>`;
}
function backgroundClaim(problem){
  const b=data.background||{};
  if(problem)return '';
  return `<div class="background-claim"><button class="quiet" data-action="claim_background" ${busy()||!b.available?'disabled':''}>${svg('play')} Claim in background</button><p class="settings-note">${esc(b.available?`AFK FARM opens Hero Siege minimized, loads ${data.plan?.character?.name||'the hero'} in ${roomName(data.plan?.zones?.[0]?.room)}, delivers at Maximum speed and closes the game again. Do not play Hero Siege meanwhile.`:(b.reason||'Unavailable right now.'))}</p></div>`;
}
function loadoutChanged(p){return !!p&&data?.profile_live_matches?.[p.id]===false;}
function loadoutNotice(p){
  if(loadoutChanged(p))return `<div class="notice warn-notice"><strong>Your loadout changed since this calibration.</strong> ${esc(p.character?.name)} is wearing different gear, talents or combat settings than when this pace was measured. Claiming will be refused until you switch back or recalibrate.</div>`;
  if(data?.profile_live_matches?.[p.id]===true)return `<p class="settings-note">${svg('check')} Your current loadout matches this calibration.</p>`;
  return `<p class="settings-note">Keep the same gear and talents until you claim; the loadout is checked then.</p>`;
}
function repeatCard(){
  const r=data?.repeat;if(!r||data.armed)return '';
  return `<div class="repeat-card"><div><span class="eyebrow">FARM AGAIN</span><strong>${esc(roomName(r.room))} · ${num(r.hours,2)} h</strong><small>${esc(r.name)}</small></div>${r.profile?`<button class="quiet" data-action="repeat" ${busy()?'disabled':''}>${svg('play')} Start the same expedition</button>`:`<small>${esc(r.reason)}</small>`}</div>`;
}
function claimReadiness(){
  if(data?.recovery?.recoverable)return '';
  const plan=data?.plan,live=data?.live;
  if(!plan)return 'Expedition plan is unavailable.';
  if(!live)return `Load ${plan.character?.name||'the expedition hero'} in ${roomName(plan.zones?.[0]?.room)} to claim. The timer is independent of the game.`;
  if(!sameHero(plan.character,live.character))return `Playing ${live.character?.name||'another hero'}. Return as ${plan.character?.name} (slot ${plan.character?.slot+1}) to claim; your expedition keeps its accrued time.`;
  if(plan.game_build!==live.game_build||(plan.reward_modifiers?!data.claim_context_matches:plan.farm_context?.hash!==live.farm_context?.hash))return 'Restore this expedition’s recorded loadout and game version before claiming.';
  if(plan.zones?.[0]?.room!==live.room)return `Enter ${roomName(plan.zones?.[0]?.room)} to claim rewards.`;
  return '';
}
let dismissedJob='',pollPromise=null,pollTimer=null;
const roomName=r=>zones.find(z=>z.room===r)?.name||(r?.startsWith('Town_')?'Town':r==='Main_Menu_rm'?'Main menu':r==='Chose_rm'?'Character selection':r)||'Waiting for region';
const zone=()=>zones.find(z=>z.room===room)||{name:room,act:'?',world:'old',x:50,y:50};
const profile=()=>data?.profiles.find(p=>p.room===room&&p.usable&&p.character?.slot===selected&&p.character?.name===current()?.name&&p.character?.class===current()?.class);
const recordedTime=seconds=>`${Math.floor(Math.max(0,seconds)/60)}:${String(Math.floor(Math.max(0,seconds)%60)).padStart(2,'0')}`;
function captureFeedback(c=visibleCapture()){
  const o=c?.outcome;if(!o)return '';
  const good=o.status==='saved'||o.status==='ready';
  return `<div class="capture-feedback ${good?'good':''}" role="status"><strong>${esc(o.title)}</strong><p>${esc(o.message)}</p>${o.status!=='validation'?`<div class="capture-requirements"><span class="${c.seconds>=o.min_seconds?'met':''}">Region time <b>${recordedTime(c.seconds)} / ${recordedTime(o.min_seconds)}</b></span><span class="${c.kills>=o.min_kills?'met':''}">Kills <b>${num(c.kills)} / ${num(o.min_kills)}</b></span></div><p class="settings-note">${o.status==='saved'?'Reward records checked.':c.running?'Reward-record coverage is checked when you finish.': 'Stopping a recording does not by itself create a usable profile.'}</p>`:''}</div>`;
}
function calibrationActions(){
  const c=visibleCapture(),recording=!!c?.running,o=c?.outcome,issue=calibrationReadiness();
  const canFinish=!!c?.validation_reference||o?.minimum_ready&&o?.status!=='invalid';
  const canRestart=recording&&sameHero(data.live?.character,c.character)&&data.live?.room===c.room;
  const planner=o?.status==='saved'?`data-use-profile="${esc(o.profile_id)}"`:'data-view="explore"';
  return `<button class="primary" data-action="${recording?'capture_stop':'capture_start'}" ${busy()||(recording?!canFinish:!!issue)?'disabled':''}>${svg('timer')} ${recording?(c.validation_reference?'Finish validation':'Finish and save profile'):c?'Start new calibration':'Start calibration'}</button>${recording?`<button class="quiet" data-action="restart_region" ${busy()||!canRestart?'disabled':''}>Restart region</button>${!canFinish?`<button class="quiet" data-action="capture_stop" data-stop-only ${busy()?'disabled':''}>Stop without saving a profile</button>`:''}`:''}${gameLaunchButton(recording)}<button class="quiet" ${planner}>${svg('map')} ${o?.status==='saved'?'Use saved calibration':'Open expedition planner'}</button>`;
}
function plannerProblem(){
  const c=visibleCapture();
  if(c?.room===room&&sameHero(c.character,current())&&c.outcome&&!['saved','validation'].includes(c.outcome.status))return c.outcome;
  const p=data.profiles.find(p=>p.room===room&&!p.usable&&sameHero(p.character,current()));
  return p?{title:'Profile needs attention',message:(p.problems||[]).join(' · ')}:{title:'A little preparation first',message:'No current calibration for this character and region. Your previous records are safe.'};
}
function useSavedProfile(id){
  const p=data.profiles.find(p=>p.id===id&&p.usable);if(!p)return;
  selected=p.character.slot;room=p.room;world=zone().world;
  localStorage.setItem('afk-slot',selected);localStorage.setItem('afk-room',room);
  switchView('explore');centerMap();
}
function completedActionMessage(){
  if(data.job?.action!=='capture_stop')return 'Action completed.';
  const c=data.calibration;
  if(c?.validation_reference)return 'Validation recording stopped. Check the comparison result.';
  return c?.outcome?.status==='saved'?'Calibration saved. Your expedition profile is ready.':c?.outcome?.title||'Recording stopped. Check the calibration result.';
}
const busy=()=>pending||data?.job?.state==='running';
function gameLaunchButton(disabled=false){
  // This action operates the game, never the offline profile. It cannot replace
  // a loaded hero, and unknown/loading states are not a character-selection menu.
  if(data?.live?.character||(data?.game_running&&!['Main_Menu_rm','Chose_rm'].includes(data?.live?.room)))return '';
  return `<button class="quiet" data-action="launch" ${busy()||!current()||disabled?'disabled':''}>${svg('play')} ${data?.game_running?'Open selected hero in game':'Launch game for calibration / claim'}</button>`;
}
function toast(text){const el=document.querySelector('#toast');el.textContent=text;el.classList.add('show');clearTimeout(toast.timeout);toast.timeout=setTimeout(()=>el.classList.remove('show'),6000);}
async function action(name,extra={}){
  if(pending)return;pending=true;
  try{
    const response=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-AFK-Token':document.querySelector('meta[name=afk-token]').content},body:JSON.stringify({action:name,slot:selected,profile:profile()?.id,hours,...extra})});
    const result=await response.json();if(!response.ok)throw Error(result.error||'Could not start the action');
    await poll();
  }catch(error){toast(error.message);}finally{pending=false;signature='';render();}
}
function switchView(next){view=next;signature='';render();}
function heading(eyebrow,title,description,button=''){
  return `<div class="page-heading"><div><span class="eyebrow">${eyebrow}</span><h1>${title}</h1><p>${description}</p></div>${button}</div>`;
}
function regionOptions(){return zones.map(z=>`<option value="${z.room}" ${z.room===room?'selected':''}>${esc(z.name)} · Act ${z.act}</option>`).join('');}
function jobBanner(){
  const j=data.job;if(!j||j.state==='done'||dismissedJob===j.id)return '';
  const pages={start:'explore',plan:'explore',claim:'explore',cancel:'explore',recover:'explore',capture_start:'measure',capture_stop:'measure',validate_start:'measure',restart_region:'measure',ingest:'loot',install:'settings',configure:'settings',portrait:'settings',save_modifiers:'modifiers',claim_background:'explore',save_preferences:'explore',save_loot_filter:'loot'};
  if(j.state==='error'&&pages[j.action]&&pages[j.action]!==view)return '';
  return `<div class="job-banner ${j.state==='error'?'error':''}" role="status"><span>${j.state==='running'?'<i class="spinner"></i>Working…':'Last attempt: '+esc(j.error)}</span><button data-action="log">Open log</button>${j.state==='error'?'<button data-action="dismiss_error" aria-label="Dismiss last error">Dismiss</button>':''}</div>`;
}
function explore(){
  const p=profile(),armed=data.armed,z=armed?(zones.find(z=>z.room===data.plan?.zones?.[0]?.room)||zone()):zone(),problem=plannerProblem();
  return heading('YOUR HERO’S NEXT CHAPTER',deliveryNeedsReview()?'Reward delivery needs review.':deliveryActive()?'Generating your rewards.':armed?'Your expedition is underway.':'Prepare for your next expedition.',deliveryNeedsReview()?'Partial reward records are preserved. Delivery will not restart automatically.':deliveryActive()?'Keep the expedition hero in its recorded region until reward delivery finishes.':armed?'Play any hero or close the game. Your expedition keeps its own timer.':'Start from a saved calibration. The game can be closed or running another hero.',gameLaunchButton())+jobBanner()+`
  <div class="explore-grid"><section class="atlas" aria-label="Interactive Hero Siege world map">
    <div class="map-viewport" id="map-viewport" tabindex="0" role="group" aria-label="Map navigation" aria-describedby="map-help"><div class="map-stage" id="map-stage"><img src="/assets/${world==='new'?'new-world':'world'}.png" draggable="false" alt="World map from Hero Siege">${zones.filter(n=>n.world===world).map(n=>`<button class="map-node ${n.room===room?'selected':''}" style="left:${n.x}%;top:${n.y}%" data-room="${n.room}" aria-label="Select ${esc(n.name)}" aria-pressed="${n.room===room}"><span>${esc(n.name)}</span></button>`).join('')}</div></div>
    <div class="atlas-top"><div><span class="eyebrow">WORLD MAP</span><div class="atlas-title">${world==='new'?'THE NEW WORLD':'TARETHIEL'}</div></div><div class="world-switch" aria-label="World selection"><button data-world="old" class="${world==='old'?'active':''}">Tarethiel</button><button data-world="new" class="${world==='new'?'active':''}">New World</button></div></div>
    <div class="atlas-bottom"><div class="map-hint">${svg('hand')}<span>Drag to explore <span class="map-wheel-hint">· Scroll to zoom</span></span></div><div class="map-tools"><button data-map="out" aria-label="Zoom out" title="Zoom out (−)">−</button><span id="map-zoom" aria-label="Map zoom">${Math.round(zoom*100)}%</span><button data-map="in" aria-label="Zoom in" title="Zoom in (+)">+</button><button data-map="reset" aria-label="Center selected region" title="Center selected region (Home)">◎</button></div></div><p id="map-help" class="map-accessibility-help">Hold and drag anywhere on the map to pan. Scroll to zoom at the pointer. Click a region to select it. With the map focused, use arrow keys to pan, plus or minus to zoom, and Home to center the selected region.</p>
  </section><aside class="planner" aria-label="Expedition planner">
    <div class="region-tag">${svg('pin')} ACT ${esc(z.act)} · ${z.world==='new'?'NEW WORLD':'TARETHIEL'}</div><h2>${esc(z.name)}</h2><p class="region-description">${armed?'This expedition uses the profile saved when it started.':p?'Your calibration in this region sets the expedition pace.':'Calibrate your character’s farming pace in this region.'}</p>
    ${armed?`<p class="muted">Expedition hero: <strong>${esc(data.plan?.character?.name)}</strong> · Slot ${num((data.plan?.character?.slot??0)+1)}</p>`:`<select class="region-select" id="zone-select" aria-label="Farming region">${regionOptions()}</select>`}
    ${armed?expeditionPanel():`<div class="divider"></div><div class="label-row"><span>EXPEDITION DURATION</span><strong id="hours-label">${num(hours,2)} <small>h</small></strong></div><input id="duration" aria-label="Expedition duration (hours)" type="range" min="0.25" max="8" step="0.25" value="${hours}"><div class="range-labels"><span>15 min</span><span>8 h</span></div><div class="presets">${[1,2,4,8].map(h=>`<button data-hours="${h}" class="${hours===h?'active':''}">${h} h</button>`).join('')}</div><div class="estimate"><div class="label-row"><span>Measured pace</span><strong>${p?num(p.kills_per_min,1)+' <small>kills/min</small>':'—'}</strong></div><div class="label-row"><span>Estimated kills</span><strong id="estimated-kills">${p?num(p.kills_per_min*hours*60):'—'}</strong></div><div class="label-row"><span>Rewards</span><strong><small>Rolled by the game when claimed</small></strong></div></div><p class="modifier-summary">${rewardsSummary()} <button class="inline-link" data-view="modifiers">Edit modifiers</button></p><div class="availability ${p?'good':''}">${svg(p?'check':'info')}<div><strong>${p?(p.quality?.status==='steady'?'Steady measured pace':p.quality?.status==='variable'?'Pace varies across this sample':'Short measured sample'):esc(problem.title)}</strong><p>${p?`${num(p.basis_seconds/60,1)} min recorded · ${num(p.coverage*100,1)}% capture coverage. Uses your saved profile. The recorded hero and loadout are checked when you claim.`:esc(problem.message)}</p></div></div>${p?loadoutNotice(p):''}<button class="primary" data-action="${p?'start':'measure'}" ${busy()?'disabled':''}>${svg(p?'play':'timer')} ${p?'Start expedition':'Calibrate farming pace'} ${svg('arrow')}</button><div class="button-note">${p?'Start from your saved profile. Play another hero or close the game while time accrues.':'Calibrate once · Reuse with the same loadout'}</div>${repeatCard()}`}
  </aside></div><div class="steps"><div class="step"><span class="step-number">01</span><div><strong>Prepare your hero</strong><p>Choose your gear and talents.</p></div></div><div class="step"><span class="step-number">02</span><div><strong>Measure your pace</strong><p>Play your usual route in the selected region.</p></div></div><div class="step"><span class="step-number">03</span><div><strong>Return and claim your rewards</strong><p>Return to the same region with the same hero.</p></div></div></div>`;
}
function expeditionPanel(){
  const pr=data.progress,delivering=deliveryActive(),problem=deliveryNeedsReview(),paused=!delivering&&!!pr.resumable,claimIssue=delivering||problem?'':claimReadiness();
  return `<span class="eyebrow">ACTIVE EXPEDITION</span><div class="clock" id="expedition-clock">00:00:00</div><p class="muted" id="expedition-note"></p><div class="progress-track"><i id="expedition-progress"></i></div><div class="estimate"><div class="label-row"><span>Planned duration</span><strong>${num(data.armed.hours,2)} h</strong></div><div class="label-row"><span>Expedition region</span><strong><small>${esc(zones.find(z=>z.room===data.plan?.zones?.[0]?.room)?.name||data.plan?.zones?.[0]?.room)}</small></strong></div><div class="label-row"><span>Magic Find at delivery</span><strong>${pr.effective_magic_find==null?'Confirmed on claim':num(pr.effective_magic_find)}</strong></div><div class="label-row"><span>Reward delivery</span><strong id="replay-count">${pr.calls_total?num(pr.calls_done||0)+' / '+num(pr.calls_total):'Not started'}</strong></div></div><p class="modifier-summary"><strong>Recorded reward settings</strong><br>${data.plan?.reward_modifiers?rewardsSummary(data.plan.reward_modifiers):'Legacy expedition · original reward rules'}</p>${delivering?'<div class="availability">'+svg('info')+'<div><strong>'+deliveryTitle()+'</strong><p>'+esc(deliveryMessage())+'</p></div></div>':problem?problemBox():'<div class="availability">'+svg('info')+'<div><strong>Your expedition is independent of the active hero</strong><p>Play another hero or close the game. Return with the recorded hero, loadout and region to generate rewards.</p></div></div>'}${paused?`<div class="availability good">${svg('check')}<div><strong>Delivery paused safely</strong><p>${num(pr.calls_done||0)} of ${num(pr.calls_total||0)} reward calls are delivered and saved. Continue with the same hero in the same region; nothing is delivered twice.</p></div></div>`:''}${claimIssue?`<div class="notice">${esc(claimIssue)}</div>`:''}${!delivering&&!problem?speedChoice(paused):''}${problem&&canSettlePartial()?settlePartialButton():`<button class="primary" data-action="claim" ${busy()||problem||claimIssue?'disabled':''}>${svg('chest')} ${problem?'Review required':delivering?deliveryTitle():paused?'Continue delivery':'Claim rewards'}</button><div class="button-note">${problem?'Partial rewards are preserved; no automatic retry.':delivering?'This claim is already in progress.':paused?'Continues from the saved position with its original speed.':'Return early to claim only the time elapsed.'}</div>`}${delivering?pauseButton():backgroundClaim(problem)}${typeof notifyToggle==='function'?notifyToggle():''}${!pr.state?'<button class="inline-link" style="margin-top:16px" data-action="cancel">Cancel expedition</button>':''}`;
}
function measurement(){
  const c=visibleCapture(),live=data.live,recording=!!c?.running,hero=recording?c.character:current(),issue=calibrationReadiness();
  const liveText=live?`${live.character?.name||'No hero loaded'} · ${roomName(live.room)}`:(data.game_running?'Waiting for game state':'Game closed');
  return heading('YOUR HERO, YOUR PACE','Farm calibration','Measure live play here. Start offline expeditions from your saved profile in Explore.')+jobBanner()+`
  <div class="content-grid"><section class="card"><span class="eyebrow">${recording?'RECORDING IN PROGRESS':'CALIBRATION'}</span><div class="spacer"></div>
  <h2>${esc(hero?.name||'Select a hero')}</h2><p class="muted" id="live-hero-location">In game: ${esc(liveText)}</p>
  ${c?`<p class="muted" id="recording-context">${recording?'Current recording':'Last recording'}: ${esc(c.character?.name)} · ${esc(roomName(c.room))}${recording&&live?.room!==c.room?' · Paused outside the recorded region':''}</p>`:''}
  <p class="muted">Walking and combat time in the recorded region count. Town and loading time are excluded.</p>
  <div class="metric-row"><div class="metric"><strong id="capture-seconds">${recordedTime(c?.seconds||0)}</strong><span>time in recorded region</span></div><div class="metric"><strong id="capture-kills">${num(c?.kills||0)}</strong><span>kills</span></div><div class="metric"><strong id="capture-rate">${num(c?.rate||0,1)}</strong><span>kills / min</span></div></div>
  ${c?.invalid?'<div class="error-box">This recording was interrupted or its loadout changed. Start a new calibration.</div>':''}
  <div id="capture-feedback">${captureFeedback(c)}</div>
  ${!recording&&issue&&c?.outcome?.status!=='saved'?`<div class="notice">To record again: ${esc(issue)}</div>`:''}
  <div class="actions calibration-actions" id="calibration-actions">${calibrationActions()}</div>
  <div class="notice">Record your usual route. Minimum: 1 minute, 30 kills and 95% usable capture coverage. Repeating normal routes helps assess consistency; no fixed duration guarantees accuracy.</div></section>
  <section class="card"><span class="eyebrow">HOW IT WORKS</span><ol class="instructions"><li><strong>Load the hero you want to measure.</strong><br>Use your normal gear and talents. ForgePact combat modifiers are optional.</li><li><strong>Enter a regular Act region.</strong><br>Record and complete your usual farming route.</li><li><strong>Finish before taking a break.</strong><br>Use Restart region to record another route. Standing idle in the region still counts.</li><li><strong>Start an expedition from the saved profile.</strong><br>The game may be closed or running another hero. Return to the recorded hero and loadout when claiming rewards.</li></ol></section></div>
  <section class="card" style="margin-top:22px"><h2>Calibration profiles <span class="muted">${data.profiles.length} records</span></h2><table class="table"><thead><tr><th>Region / hero</th><th>Pace</th><th>Duration</th><th>Status</th></tr></thead><tbody>${data.profiles.map(p=>`<tr><td>${esc(zones.find(z=>z.room===p.room)?.name||p.room)}<small>${esc(p.character?.name||'Missing character identity')}</small></td><td>${num(p.kills_per_min,1)} / min</td><td>${num(p.basis_seconds/60,1)} min</td><td><span class="badge ${p.usable?'good':''}">${p.usable?(p.quality?.status==='steady'?'Steady sample':p.quality?.status==='variable'?'Variable pace':'Short sample'):'Recalibrate'}</span>${p.problems.length?`<small>${esc(p.problems[0])}</small>`:''}${p.usable?`<button class="inline-link profile-use" data-use-profile="${esc(p.id)}">Use profile</button>`:''}</td></tr>`).join('')||'<tr><td colspan="4">No profiles yet. Your first calibration will appear here.</td></tr>'}</tbody></table></section>`;
}
function loot(){
  return heading('AFTER THE EXPEDITION','Your loot chest','Reward generation, game saves and Vault transfers are tracked separately.',data.editor?`<a class="quiet" href="${esc(data.editor)}" target="_blank" rel="noopener">${svg('link')} Open Vault</a>`:'')+jobBanner()+deliveryCard()+`<div class="card"><div class="label-row"><span>INFINITE VAULT</span><span class="badge ${data.editor?'good':''}">${data.editor?'Connected':'Item Editor offline'}</span></div>${!data.rewards.length?`<div class="empty">${svg('chest')}<h2>Your first expedition’s story starts here.</h2><p>Claimed items and transfer status appear here. When Item Editor is offline, item records stay on your computer.</p></div>`:`<table class="table"><thead><tr><th>Expedition</th><th>Loot</th><th>Game save</th><th>Vault</th></tr></thead><tbody>${data.rewards.map(r=>`<tr><td>${esc(r.id)}<small>${esc(r.state)}</small></td><td>${num(r.items||0)} items<small>${num(r.gold||0)} gold</small></td><td><span class="badge ${r.save_confirmed?'good':''}">${r.save_confirmed?'Saved':r.partial?'Partial':'Needs review'}</span></td><td>${r.stages?.ingest==='done'?`<span class="badge good">Transferred</span><button class="inline-link" data-ingest="${esc(r.id)}" ${busy()||!data.editor?'disabled':''} title="Adds items your current filter allows that are not in the Vault yet">Transfer again</button>`:!r.save_confirmed&&!r.partial?`<a class="quiet" href="/api/recovery-report?id=${encodeURIComponent(r.id)}" target="_blank" rel="noopener">Review save receipt</a>`:`<button class="quiet" data-ingest="${esc(r.id)}" ${busy()||!data.editor?'disabled':''}>Transfer to Vault</button>`}</td></tr>`).join('')}</tbody></table>`}</div><div class="notice">This version uses regular region kills. Event completion rewards, guaranteed boss drops and ForgePact kill triggers are not fully reproduced. Unverified packets cannot be selected for expeditions.</div>`;
}
function settings(){
  const install=data.installation;
  return heading('EVERYTHING IN PLACE','Connections and setup','Manage your game files and local connections in one place.')+jobBanner()+`<div class="content-grid"><section class="card"><span class="eyebrow">HERO SIEGE</span><div class="spacer"></div><h2>Game folder</h2><label class="muted" for="game-folder">The bin folder containing Hero_Siege.exe</label><input id="game-folder" class="settings-input" type="text" spellcheck="false" value="${esc(data.config.game_bin)}" placeholder="C:\\Games\\HeroSiege\\bin"><div class="actions"><button class="primary" data-action="configure">Save folder</button><button class="quiet" data-action="install" ${busy()||!install.configured?'disabled':''}>Install AFK plugin</button></div><p class="settings-note">Close the game before installing the plugin. Your existing AFK DLL is backed up automatically. Aurie and YYToolkit must already be installed.</p><div class="check-row"><span>Game folder</span><strong class="${install.exe?'':'missing'}">${install.exe?'Found':'Not selected'}</strong></div><div class="check-row"><span>Aurie / YYToolkit</span><strong class="${install.aurie&&install.yytk?'':'missing'}">${install.aurie&&install.yytk?'Found':'Needs attention'}</strong></div><div class="check-row"><span>AFK plugin</span><strong class="${install.plugin?'':'missing'}">${install.plugin?'File found':'Not installed'}</strong></div><div class="check-row"><span>Live connection</span><strong class="${data.live?'':'missing'}">${data.live?esc(data.live.plugin):'Game disconnected'}</strong></div></section><section class="card"><h2>Local. Simple. Yours.</h2><p class="muted">AFK FARM runs on this computer only. No account or internet connection is needed. Your expedition timer continues while the panel is closed.</p><div class="notice">Expedition speed comes from your measured kill rate. Loot is generated by the game’s own drop system.</div><div class="spacer"></div><h2 style="margin-top:20px">Character portrait</h2><p class="muted">Name, class and level are read from your local save. Automatic equipment rendering is not available. You can choose your own character screenshot below.</p><div class="actions"><button class="quiet" data-action="log">Activity log</button><button class="quiet" data-action="close" ${busy()||!data.game_running?'disabled':''}>Close game</button></div></section></div>`;
}
function render(){
  if(!data)return;
  const mapFocus=document.activeElement;
  const mapFocusSelector=mapFocus?.id==='map-viewport'?'#map-viewport':mapFocus?.matches('.map-node')?`.map-node[data-room="${mapFocus.dataset.room}"]`:mapFocus?.matches('.map-tools button')?`[data-map="${mapFocus.dataset.map}"]`:null;
  endMapGesture();
  if(!current()&&data.characters.length)selected=data.characters[0].slot;
  const c=current();
  document.querySelector('#app').innerHTML=`<div class="shell"><aside class="rail"><a href="#" class="brand" aria-label="AFK FARM home"><img src="/assets/emblem.svg" alt="AFK FARM"></a><nav class="nav" aria-label="Main navigation">${[['explore','map','Explore'],['measure','timer','Calibration'],['loot','chest','Loot'],['modifiers','settings','Modifiers'],['settings','settings','Settings']].map(([v,i,label])=>`<button class="nav-button ${view===v?'active':''}" data-view="${v}" ${view===v?'aria-current="page"':''}>${svg(i)}${label}</button>`).join('')}</nav><div class="rail-bottom"><span class="live-dot"></span>OFFLINE EXPEDITIONS</div></aside><main><header class="topbar"><div class="wordmark"><strong>AFK <span style="color:var(--gold)">FARM</span></strong><small>HERO SIEGE COMPANION</small></div><div class="top-actions"><span class="status-pill ${data.live||deliveryActive()?'':'off'}"><i class="live-dot"></i>${esc(gameStatus())}</span><div class="character-picker"><div class="avatar" aria-hidden="true">${svg('swords')}</div><div><label for="character">SELECTED HERO</label><select id="character">${data.characters.map(ch=>`<option value="${ch.slot}" ${ch.slot===selected?'selected':''}>${esc(ch.name)} · ${esc(ch.class_name)} · Lv. ${ch.level} · Slot ${ch.slot+1}</option>`).join('')||'<option>No local characters found</option>'}</select></div></div></div></header><div class="workspace">${view==='explore'?explore():view==='measure'?measurement():view==='loot'?loot():view==='modifiers'?modifiers():settings()}<footer class="footer"><span>${svg('check')} Local saves · No account needed · v${esc(data.version)}</span><span>Measured pace. Native loot. <button data-action="log">Activity log ↗</button></span></footer></div></main></div>`;
  extrasPanel();bindMap();updateLive();
  if(mapFocusSelector)document.querySelector(mapFocusSelector)?.focus({preventScroll:true});
}
function updateLive(){
  sampleDelivery();updateExtras();
  const c=visibleCapture();for(const [id,val] of [['capture-seconds',recordedTime(c?.seconds||0)],['capture-kills',num(c?.kills||0)],['capture-rate',num(c?.rate||0,1)]]){const el=document.getElementById(id);if(el)el.textContent=val;}
  const feedback=document.getElementById('capture-feedback');if(feedback){const html=captureFeedback(c);if(feedback.innerHTML!==html)feedback.innerHTML=html;}
  const controls=document.getElementById('calibration-actions');if(controls){const html=calibrationActions();if(controls.dataset.content!==html){controls.dataset.content=html;controls.innerHTML=html;}}
  if(data.armed){
    const elapsed=Math.max(0,(Date.now()-Date.parse(data.armed.started_at))/1000),total=data.armed.hours*3600,credit=Math.min(elapsed,total),remaining=Math.max(0,total-elapsed);
    const clock=document.getElementById('expedition-clock');if(clock)clock.textContent=[Math.floor(remaining/3600),Math.floor(remaining%3600/60),Math.floor(remaining%60)].map(n=>String(n).padStart(2,'0')).join(':');
    const note=document.getElementById('expedition-note');if(note)note.textContent=deliveryNeedsReview()?'Partial delivery is on hold. Review the save and reward records before any further delivery.':deliveryActive()?deliveryTitle()+'. '+deliveryMessage():data.progress.resumable?'Delivery paused at a saved position. Continue with the same hero in the same region.':remaining?'remaining · '+num(credit/3600,2)+' hours accrued':'Expedition time is complete. Return to the recorded hero and region to generate rewards.';
    const bar=document.getElementById('expedition-progress');if(bar)bar.style.width=(data.progress.state?data.progress.percent:credit/total*100)+'%';
    const counter=document.getElementById('replay-count');if(counter)counter.textContent=data.progress.calls_total?num(data.progress.calls_done||0)+' / '+num(data.progress.calls_total):'Not started';
  }
  if(deliveryActive()){
    const pr=data.progress;
    for(const [id,value] of [['delivery-calls',num(pr.calls_done||0)+' / '+num(pr.calls_total||0)],['delivery-eta',deliveryEtaText()],['delivery-items',num(pr.items||0)],['delivery-filtered',num(pr.items_filtered||0)]]){
      const el=document.getElementById(id);if(el)el.textContent=value;
    }
    const bar=document.getElementById('delivery-progress');if(bar)bar.style.width=(Number(pr.percent)||0)+'%';
  }
  const log=document.querySelector('#log-content');if(log)log.textContent=data.job?.output||'No actions have been run yet.';
}
function mapZoomBounds(stage,viewport){
  const min=Math.max(.65,viewport.clientWidth/stage.offsetWidth,viewport.clientHeight/stage.offsetHeight);
  return {min,max:Math.max(2.4,min)};
}
function mapTransform(){
  const stage=document.getElementById('map-stage');if(!stage)return;
  const viewport=stage.parentElement,{min,max}=mapZoomBounds(stage,viewport);
  zoom=Math.max(min,Math.min(max,zoom));
  const limitX=Math.max(0,(stage.offsetWidth*zoom-viewport.clientWidth)/2),limitY=Math.max(0,(stage.offsetHeight*zoom-viewport.clientHeight)/2);
  pan={x:Math.max(-limitX,Math.min(limitX,pan.x)),y:Math.max(-limitY,Math.min(limitY,pan.y))};
  stage.style.transform=`translate(calc(-50% + ${pan.x}px),calc(-50% + ${pan.y}px)) scale(${zoom})`;
  const label=document.getElementById('map-zoom');if(label)label.textContent=Math.round(zoom*100)+'%';
  const out=document.querySelector('[data-map="out"]'),inside=document.querySelector('[data-map="in"]');
  if(out)out.disabled=zoom<=min+.0001;if(inside)inside.disabled=zoom>=max-.0001;
}
function zoomMap(value,clientX,clientY){
  const stage=document.getElementById('map-stage');if(!stage)return;
  mapTransform();
  const viewport=stage.parentElement,rect=viewport.getBoundingClientRect(),{min,max}=mapZoomBounds(stage,viewport);
  const next=Math.max(min,Math.min(max,value)),ratio=next/zoom;
  const x=clientX===undefined?0:clientX-rect.left-rect.width/2;
  const y=clientY===undefined?0:clientY-rect.top-rect.height/2;
  // Keep the same map point under the pointer, except where an edge must clamp.
  pan={x:x-(x-pan.x)*ratio,y:y-(y-pan.y)*ratio};zoom=next;
  mapBusyUntil=Date.now()+200;mapTransform();
}
function centerMap(){
  const stage=document.getElementById('map-stage');if(!stage)return;
  mapTransform();const z=zone();
  pan={x:(50-z.x)/100*stage.offsetWidth*zoom,y:(50-z.y)/100*stage.offsetHeight*zoom};mapTransform();
}
function mapInteracting(){return !!mapGesture||Date.now()<mapBusyUntil;}
function endMapGesture(){
  if(!mapGesture)return;
  const gesture=mapGesture;mapGesture=null;
  gesture.viewport.classList.remove('dragging');mapBusyUntil=Date.now()+200;
  if(gesture.capture.hasPointerCapture(gesture.id))gesture.capture.releasePointerCapture(gesture.id);
}
window.addEventListener('resize',mapTransform);
window.addEventListener('blur',endMapGesture);
function bindMap(){
  const viewport=document.getElementById('map-viewport');if(!viewport)return;mapTransform();
  let suppressClick=false;
  viewport.addEventListener('pointerdown',e=>{
    if(e.button!==0||!e.isPrimary||mapGesture)return;
    suppressClick=false;
    // Capture on the original node so a stationary press remains a normal click.
    const capture=e.target.closest('.map-node')||viewport;
    mapGesture={id:e.pointerId,x:e.clientX,y:e.clientY,moved:false,viewport,capture};
    capture.setPointerCapture(e.pointerId);viewport.classList.add('dragging');
  });
  viewport.addEventListener('pointermove',e=>{
    const g=mapGesture;if(!g||g.id!==e.pointerId)return;
    if(e.pointerType==='mouse'&&!(e.buttons&1)){endMapGesture();return;}
    const dx=e.clientX-g.x,dy=e.clientY-g.y;
    if(!g.moved&&Math.hypot(dx,dy)<4)return;
    g.moved=true;suppressClick=true;e.preventDefault();
    // Increment from the clamped position: reversing at an edge responds at once.
    pan={x:pan.x+dx,y:pan.y+dy};g.x=e.clientX;g.y=e.clientY;mapTransform();
  });
  for(const event of ['pointerup','pointercancel','lostpointercapture'])viewport.addEventListener(event,e=>{
    if(mapGesture?.id===e.pointerId)endMapGesture();
  });
  viewport.addEventListener('click',e=>{
    if(suppressClick&&e.detail!==0){e.preventDefault();e.stopImmediatePropagation();}
  },true);
  viewport.addEventListener('dragstart',e=>e.preventDefault());
  viewport.addEventListener('wheel',e=>{
    e.preventDefault();if(!e.deltaY||mapGesture)return;
    const unit=e.deltaMode===1?16:e.deltaMode===2?viewport.clientHeight:1;
    const delta=Math.max(-240,Math.min(240,e.deltaY*unit));
    zoomMap(zoom*Math.exp(-delta*.0015),e.clientX,e.clientY);
  },{passive:false});
  viewport.addEventListener('keydown',e=>{
    if(e.target!==viewport||e.ctrlKey||e.metaKey||e.altKey)return;
    const step=e.shiftKey?120:50;
    const direction={ArrowLeft:[step,0],ArrowRight:[-step,0],ArrowUp:[0,step],ArrowDown:[0,-step]}[e.key];
    if(direction){pan={x:pan.x+direction[0],y:pan.y+direction[1]};mapTransform();}
    else if(e.key==='+'||e.key==='=')zoomMap(zoom*1.18);
    else if(e.key==='-')zoomMap(zoom/1.18);
    else if(e.key==='Home')centerMap();else return;
    e.preventDefault();mapBusyUntil=Date.now()+200;
  });
}
document.addEventListener('click',async event=>{
  const target=event.target.closest('button,a.brand');if(!target||target.disabled)return;
  if(target.matches('a.brand')){event.preventDefault();switchView('explore');return;}
  if(target.dataset.view){switchView(target.dataset.view);return;}
  if(target.dataset.useProfile){useSavedProfile(target.dataset.useProfile);return;}
  if(target.dataset.room){room=target.dataset.room;localStorage.setItem('afk-room',room);render();return;}
  if(target.dataset.world){world=target.dataset.world;const candidate=zones.find(z=>z.world===world);if(zone().world!==world)room=candidate.room;localStorage.setItem('afk-room',room);pan={x:0,y:0};zoom=1.15;render();centerMap();return;}
  if(target.dataset.hours){hours=Number(target.dataset.hours);render();return;}
  if(target.dataset.map){if(target.dataset.map==='reset')centerMap();else zoomMap(zoom*(target.dataset.map==='in'?1.18:1/1.18));return;}
  if(target.dataset.ingest){await action('ingest',{id:target.dataset.ingest});return;}
  const a=target.dataset.action;
  if(a==='capture_stop'&&target.hasAttribute('data-stop-only')){await action(a,{save_profile:false});return;}
  if(a==='dismiss_error'){dismissedJob=data.job?.id;render();return;}
  if(a==='measure'){switchView('measure');return;}
  if(a==='log'){document.querySelector('#log').showModal();updateLive();return;}
  if(a==='configure'){await action(a,{game_bin:document.querySelector('#game-folder').value});return;}
  if(a==='cancel'){const dialog=document.querySelector('#confirm');dialog.showModal();dialog.addEventListener('close',()=>{if(dialog.returnValue==='yes')action('cancel');},{once:true});return;}
  if(a==='claim'||a==='claim_background'){await action(a,{speed:chosenSpeed()});return;}
  if(a==='repeat'){const r=data.repeat;if(!r)return;selected=r.slot;room=r.room;hours=r.hours;world=zone().world;localStorage.setItem('afk-slot',selected);localStorage.setItem('afk-room',room);signature='';render();centerMap();if(profile()&&!loadoutChanged(profile()))await action('start');return;}
  if(a==='start'&&loadoutChanged(profile())&&!confirm('Your hero is wearing a different loadout than this calibration. Claiming will be refused until you switch back or recalibrate. Start anyway?'))return;
  if(a)await action(a);
});
document.addEventListener('change',event=>{
  if(event.target.id==='character'){selected=Number(event.target.value);localStorage.setItem('afk-slot',selected);render();}
  if(event.target.id==='zone-select'){room=event.target.value;world=zone().world;localStorage.setItem('afk-room',room);render();centerMap();}
  if(event.target.id==='delivery-speed'){speedDraft=event.target.value;updateLive();if(speedDraft!==data.preferences?.delivery_speed)action('save_preferences',{delivery_speed:speedDraft});}
});
document.addEventListener('input',event=>{
  if(event.target.id!=='duration')return;hours=Number(event.target.value);document.querySelector('#hours-label').innerHTML=`${num(hours,2)} <small>h</small>`;
  document.querySelector('#estimated-kills').textContent=profile()?num(profile().kills_per_min*hours*60):'—';
  document.querySelectorAll('[data-hours]').forEach(b=>b.classList.toggle('active',Number(b.dataset.hours)===hours));
});
document.querySelector('#close-log').addEventListener('click',()=>document.querySelector('#log').close());
let lastJob='';
async function poll(){
  if(pollPromise)return pollPromise;
  clearTimeout(pollTimer);
  pollPromise=(async()=>{
    try{
      const response=await fetch('/api/state');if(!response.ok)throw Error('Connection to the panel was lost.');data=await response.json();
      if(data.job?.state==='done'&&lastJob!==data.job.id){lastJob=data.job.id;toast(completedActionMessage());}
      const c=data.calibration;
      const next=JSON.stringify([view,selected,room,world,data.characters,data.profiles.map(p=>[p.id,p.usable,p.built_at,p.problems]),data.armed,data.plan?.character,data.rewards.map(r=>[r.id,r.stages]),data.job?.state,data.job?.id,data.job?.error,data.live?.plugin,data.live?.room,data.live?.character,data.live?.farm_context?.hash,data.live?.capture_on,data.live?.replay_running,data.game_running,data.editor,data.installation,c?.session,c?.character,c?.room,c?.running,c?.invalid,c?.outcome?.status,c?.outcome?.profile_id,c?.save_error,data.progress.state,data.progress.pause,data.progress.reconciliation_required,data.recovery?.status,data.validations,data.portraits,data.reward_modifiers,data.claim_context_matches,data.progress.resumable,data.loot_filter,data.loot_filter_error,data.preferences,data.repeat,data.profile_live_matches,data.background,data.rewards.map(r=>[r.level_now,r.stages?.ingest])]);
      const editing=document.activeElement?.matches('#game-folder,#duration,#loot-search,#validation-profile,[data-reward],#delivery-speed,[data-vault-rarity]');
      if(next!==signature&&!editing&&!mapInteracting()){signature=next;render();}else updateLive();
    }catch(error){toast(error.message);}
  })();
  try{await pollPromise;}finally{
    pollPromise=null;
    const active=data?.job?.state==='running'||data?.calibration?.running||['running','paused'].includes(data?.progress?.state);
    pollTimer=setTimeout(poll,active?500:document.hidden?5000:2000);
  }
}
document.addEventListener('visibilitychange',()=>{if(!document.hidden)poll();});
setInterval(()=>{if(data&&!document.hidden)updateLive();},1000);
(async()=>{try{zones=await(await fetch('/zones.json')).json();world=zone().world;await poll();centerMap();}catch(error){toast(error.message);}})();

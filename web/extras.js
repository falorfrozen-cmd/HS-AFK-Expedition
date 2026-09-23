'use strict';
let lootExpedition='',lootSearch='',lootRarity='',includeHidden=false,vaultFilterDraft=null;
const rarityOrder=['Unholy','Angelic','Heroic','Set','Satanic'];
function vaultFilterSettings(){return vaultFilterDraft||data.loot_filter||{rarities:{},keep_stackables:true,respect_game_filter:true};}
function vaultFilterDirty(){return !!vaultFilterDraft&&JSON.stringify(vaultFilterDraft)!==JSON.stringify(data.loot_filter);}
function vaultFilterCard(){
  const s=vaultFilterSettings(),rarities=data.loot_filter_rarities||[];
  const label={Other:'Other gear (normal, magic, rare, jewels, charms)'};
  return disclosure('vault-options','Vault transfer filter',`<section class="card extra-section" id="vault-filter"><span class="eyebrow">WHAT GOES TO YOUR VAULT</span><h2>Vault transfer filter</h2><p class="muted">Choose which generated items a transfer sends to Infinite Vault. Items left out are not deleted: they stay in the expedition records, and Transfer again adds them after you widen the filter.</p>${data.loot_filter_error?`<div class="notice">${esc(data.loot_filter_error)}</div>`:''}<div class="filter-grid">${rarities.map(r=>`<label class="filter-option"><input type="checkbox" data-vault-rarity="${esc(r)}" ${s.rarities?.[r]!==false?'checked':''}> <span class="rarity-${esc(r.toLowerCase())}">${esc(label[r]||r)}</span></label>`).join('')}<label class="filter-option"><input type="checkbox" data-vault-option="keep_stackables" ${s.keep_stackables!==false?'checked':''}> Keys, materials, runes and gems</label><label class="filter-option"><input type="checkbox" data-vault-option="respect_game_filter" ${s.respect_game_filter!==false?'checked':''}> Leave out what the game’s loot filter hides</label></div><div class="actions"><button class="primary" data-vault-filter-save ${busy()?'disabled':''}>Save filter</button><span class="settings-note" id="vault-filter-status">${vaultFilterDirty()?'Unsaved changes.':'Saved · applies to the next transfer.'}</span></div></section>`,data.loot_filter_error?'Needs attention':'Choose what reaches your stash',!!data.loot_filter_error);
}
function notificationCard(){
  const n=data.notification||{},on=data.preferences?.ready_notification!==false;
  if(!n.windows)return '';
  const when=n.scheduled?.at?new Date(n.scheduled.at).toLocaleString('en-US', {weekday:'short',hour:'2-digit',minute:'2-digit'}):'';
  const status=n.error?`<p class="settings-note error-text">${esc(n.error)}</p>`:on&&when?`<p class="settings-note">Scheduled for ${esc(when)}.</p>`:on?'<p class="settings-note">Scheduled when you start an expedition.</p>':'';
  return `<section class="card"><span class="eyebrow">NOTIFICATIONS</span><h2>Know when to claim</h2><label class="notify-toggle"><input type="checkbox" id="ready-notification" ${on?'checked':''}> Windows notification when an expedition is ready, also while AFK FARM and the game are closed</label><p class="settings-note">Works even with the panel closed. Claiming or cancelling removes the reminder.</p>${status}</section>`;
}
let regionSort='xp_per_hour';
function regionCard(){
  const entry=(data.regions||[]).find(e=>sameHero(e.character,current()));
  if(!entry||!entry.rows.length)return '';
  const cols=[['kills_per_min','Kills / min',1],['xp_per_hour','XP / h',0],['gold_per_hour','Gold / h (×1)',0],['Unholy','Unholy / h',1],['Angelic','Angelic / h',1],['Heroic','Heroic / h',1],['Satanic','Satanic / h',0]];
  const value=(r,k)=>{const v=k in (r.rarities_per_hour||{})?r.rarities_per_hour[k]:r[k];return typeof v==='number'?v:null;};
  const best={};for(const [k] of cols){const values=entry.rows.map(r=>value(r,k)).filter(v=>v!==null);best[k]=values.length>1?Math.max(...values):null;}
  const rows=[...entry.rows].sort((a,b)=>(value(b,regionSort)??-1)-(value(a,regionSort)??-1));
  const basis=r=>r.expeditions?`${num(r.hours,2)} h · ${r.expeditions} expedition${r.expeditions>1?'s':''}${r.magic_find.length?' · MF ×'+r.magic_find.map(m=>num(m,m%1?1:0)).join(' / '):''}`:'Calibration only';
  return disclosure('region-comparison','Compare your regions',`<section class="card extra-section"><span class="eyebrow">REGION COMPARISON</span><h2>Where ${esc(current()?.name||'your hero')} farms best</h2><div class="table-wrap" tabindex="0" role="region" aria-label="Region comparison"><table class="table region-table"><thead><tr><th>Region</th>${cols.map(([k,label])=>`<th><button class="sort-header ${regionSort===k?'active':''}" data-region-sort="${k}" aria-pressed="${regionSort===k}">${label}</button></th>`).join('')}<th>Based on</th><th></th></tr></thead><tbody>${rows.map(r=>`<tr><td><strong>${esc(r.name)}</strong></td>${cols.map(([k,,digits])=>{const v=value(r,k);return `<td class="${v!==null&&v===best[k]?'best':''}">${v===null?'—':num(v,digits)}</td>`;}).join('')}<td><small>${esc(basis(r))}</small></td><td>${r.usable?`<button class="inline-link" data-use-profile="${esc(r.profile)}">Plan here</button>`:'<small>Needs recalibration</small>'}</td></tr>`).join('')}</tbody></table></div><p class="settings-note">XP per hour comes from your calibration in that region, before reward settings. Gold and item columns come from expeditions you delivered there: gold at ×1, items at the Magic Find you used. Short expeditions give rough numbers. Click a column to sort.</p></section>`, `Measured pace & delivered rewards`);
}
function conversionLine(c){
  if(!c||!c.enabled)return '';
  const sum=o=>Object.values(o||{}).reduce((a,b)=>a+b,0),fragments=sum(c.created),pending=sum(c.pending),parts=[];
  if(c.sold_items)parts.push(`Sold ${num(c.sold_items)} filtered items for <b>${num(c.sell_gold)}</b> gold`);
  if(c.prospected_items)parts.push(`broke ${num(c.prospected_items)} down into <b>${num(fragments)}</b> fragments (${num(c.output_stacks)} stack${c.output_stacks===1?'':'s'})`);
  if(pending)parts.push(`${num(pending)} fragments still pending`);
  if(c.kept_items)parts.push(`${num(c.kept_items)} Satanic and above kept (no Prospector recipe)`);
  return `<p class="conversion-line">${parts.join(' · ')||'No filtered item was sold or broken down.'}${c.note?`<br><small>${esc(c.note)}</small>`:''}</p>`;
}
function filteredItemsCard(){
  const mode=data.preferences?.filtered_items||'convert';
  return disclosure('filtered-options','Filtered item handling',`<section class="card extra-section" id="filtered-items"><span class="eyebrow">ITEMS YOUR LOOT FILTER HIDES</span><h2>Sell or break them down while delivering</h2><label class="filter-option"><input type="radio" name="filtered-items" value="convert" ${mode==='convert'?'checked':''}> Sell items below Satanic; break Satanic and above down like the Prospector</label><label class="filter-option"><input type="radio" name="filtered-items" value="keep" ${mode==='keep'?'checked':''}> Keep them in the expedition records</label><p class="settings-note">Applies to your next claim. It happens in the game during delivery, with each item's own sale value and the game's Prospector recipes: the gold goes to your hero, and the fragments (Satanic Crystal Fragments; Gypsy's, Mallet or Dice Fragments from S and SS items) go to the Vault's AFK Materials in stacks of up to 999. Satanic and above items the Prospector does not take are kept. Selling runs only while the game is offline and not connected to the Hero Siege servers; otherwise the items are kept.</p></section>`, `${mode==='convert'?'Sell & break down':'Keep item records'}`);
}
function summaryCard(r){
  const loot=r?.loot;if(!loot)return '';
  const chips=[...rarityOrder,'Other'].filter(k=>(loot.visible_rarities||{})[k]).map(k=>`<span class="rarity-chip rarity-${esc(k.toLowerCase())}">${esc(k==='Other'?'Other gear':k)} <b>${num(loot.visible_rarities[k])}</b></span>`).join('');
  const level=Number.isFinite(r.level_before)&&Number.isFinite(r.level_now)?(r.level_now>r.level_before?`Lv. ${r.level_before} → ${r.level_now}`:`Lv. ${r.level_now}`):'—';
  const best=(loot.best||[]).map(b=>`<li class="best-drop"><span class="item-art small">${b.icon?`<img src="${esc(b.icon)}" alt="" loading="lazy">`:svg('chest')}</span><span><strong>${esc(b.name)}</strong><small class="rarity-${esc(String(b.group||'').toLowerCase())}">${esc(b.rarity)}</small></span>${b.count>1?`<b>×${num(b.count)}</b>`:''}</li>`).join('');
  return `<div class="summary-block"><div class="metric-row"><div class="metric"><strong>${num(loot.total||0)}</strong><span>items generated</span></div><div class="metric"><strong>${num((loot.total||0)-(loot.filtered||0))}</strong><span>shown by the game filter</span></div><div class="metric"><strong>${num(r.gold||0)}</strong><span>gold</span></div><div class="metric"><strong>${num(r.exp||0)}</strong><span>XP</span></div><div class="metric"><strong>${esc(level)}</strong><span>hero level</span></div><div class="metric"><strong>${Number.isFinite(r.delivery_seconds)?esc(duration(r.delivery_seconds)):'—'}</strong><span>delivery time</span></div></div><div class="rarity-chips">${chips||'<span class="muted">No items passed the game filter.</span>'}${loot.stackables?`<span class="rarity-chip">Keys &amp; materials <b>${num(loot.stackables)}</b></span>`:''}</div>${conversionLine(r.conversion)}${best?`<h3 class="summary-title">Best drops</h3><ul class="best-drops">${best}</ul>`:''}</div>`;
}
const readSaved=(key)=>{try{return JSON.parse(localStorage.getItem(key)||'[]');}catch{return [];}};
let favoriteProfiles=readSaved('afk-favorite-profiles'),favoriteItems=readSaved('afk-favorite-items');
const qualityTitles={invalid:'Recording interrupted',insufficient:'Below profile minimum',short:'Short sample',variable:'Pace varies',steady:'Steady within this recording'};
function qualityContent(q){
  if(!q)return '<p class="muted">Start a recording to see your pace across one-minute windows.</p>';
  const rates=q.window_rates||[],max=Math.max(1,...rates);
  return `<div class="label-row"><span class="eyebrow">MEASUREMENT QUALITY</span><span class="badge ${q.status==='steady'?'good':''}">${esc(qualityTitles[q.status]||q.status)}</span></div><p class="muted">${esc(!visibleCapture()?.running&&q.status==='insufficient'?'This recording is below the minimum of 1 minute and 30 usable kills. Record a new normal route.':q.message)}</p><div class="pace-chart" role="img" aria-label="Kills in each complete one-minute window">${rates.slice(-30).map((r,i)=>`<div title="Minute ${Math.max(0,rates.length-30)+i+1}: ${r} kills" style="height:${Math.max(2,100*r/max)}%"><span>${r}</span></div>`).join('')||'<span class="muted">Waiting for the first complete minute…</span>'}</div><div class="label-row"><span>Observed one-minute pace</span><strong>${q.observed_min==null?'—':num(q.observed_min)+'–'+num(q.observed_max)+' kills'}</strong></div><p class="settings-note">${esc(q.note)} Live counts are provisional until packet eligibility is checked on save.</p>`;
}
function extrasPanel(){
  const host=document.querySelector('.workspace'),footer=host?.querySelector('.footer');if(!host)return;
  let html='';
  if(view==='measure'){
    html+=disclosure('validation-tools','Validate a saved profile',`<section class="card"><span class="eyebrow">OPTIONAL ACCURACY CHECK</span><h2>Validate your farming pace</h2><p class="muted">Freeze a profile’s prediction, then record a separate run with the same hero, loadout and region. Your original profile is preserved. This separate accuracy check requires 10 minutes and 200 kills in each recording; it is not required to start an expedition.</p><label for="validation-profile">Reference profile</label><select class="settings-input" id="validation-profile">${data.profiles.filter(p=>p.usable&&sameHero(p.character,current())).map(p=>`<option value="${esc(p.id)}">${esc(roomName(p.room))} · ${num(p.kills_per_min,1)} / min</option>`).join('')||'<option value="">Calibrate first</option>'}</select><div class="actions"><button class="quiet" data-validation ${busy()||data.calibration?.running||calibrationReadiness()||!data.profiles.some(p=>p.usable&&sameHero(p.character,current()))?'disabled':''}>Record validation run</button><button class="quiet" data-action="restart_region" ${busy()||!regularRegion(data.live?.room)||!sameHero(data.live?.character,current())?'disabled':''}>Restart region</button></div><p class="settings-note">Restart region returns through town using the game’s normal room transition. Town and loading time are excluded. Finish the validation with the recording button above.</p>${(data.validations||[]).slice(0,3).map(v=>`<div class="validation-result"><strong>${esc(roomName(v.room))} · ${esc(v.status.replaceAll('_',' '))}</strong><p>Predicted ${num(v.metrics?.kills_per_min?.predicted)} kills · observed ${num(v.metrics?.kills_per_min?.observed)} kills</p><small>${esc((v.issues||[]).join(' ')||v.note)}</small></div>`).join('')}</section>`,'Optional · separate recording');
    document.querySelectorAll('.table tbody tr').forEach((tr,i)=>{const p=data.profiles[i];if(!p)return;const button=document.createElement('button');button.className='favorite';button.dataset.favoriteProfile=p.id;button.setAttribute('aria-label','Favorite '+roomName(p.room));button.setAttribute('aria-pressed',favoriteProfiles.includes(p.id));button.textContent=favoriteProfiles.includes(p.id)?'★':'☆';tr.firstElementChild?.prepend(button);});
  }
  if(view==='explore'&&data.recovery&&!['not_started','paused'].includes(data.recovery.status)&&!busy()){
    const r=data.recovery;
    html+=`<section class="card extra-section"><span class="eyebrow">DELIVERY RECOVERY</span><h2>${r.recoverable?'Your rewards were already saved':'This claim needs review'}</h2><p class="muted">${r.recoverable?'The native save, exact plan and item records agree. Reconcile the claim without generating rewards again.':esc(r.reasons.join(' '))}</p><div class="actions">${r.recoverable?'<button class="primary" data-action="recover">Recover saved claim</button>':''}<a class="quiet" href="/api/recovery-report?id=${encodeURIComponent(r.id)}" target="_blank" rel="noopener">Open recovery report</a></div>${r.recoverable?'':`<p class="settings-note">Close as partial delivery, in the expedition panel above, keeps what was delivered (${num(r.calls_done||0)} of ${num(r.calls_total||0)} reward calls and their items) and gives up the rest. Nothing is generated again, and the expedition clock becomes free.</p>`}</section>`;
  }
  if(view==='explore')html+=regionCard();
  if(view==='loot'&&data.rewards.length){
    if(!data.rewards.some(r=>r.id===lootExpedition))lootExpedition=data.rewards[0].id;
    const r=data.rewards.find(r=>r.id===lootExpedition);
    html+=`<section class="card extra-section loot-journal"><div class="journal-heading"><div><span class="eyebrow">EXPEDITION JOURNAL</span><h2>${esc(r.character?.name||'Hero')} · ${esc(roomName(r.room))}</h2></div><div class="journal-select"><label for="loot-expedition">Choose expedition</label><select class="settings-input" id="loot-expedition">${data.rewards.map(r=>`<option value="${esc(r.id)}" ${r.id===lootExpedition?'selected':''}>${esc(r.character?.name||'Hero')} · ${esc(roomName(r.room))} · ${esc(r.id)}</option>`).join('')}</select></div></div>${summaryCard(r)}<div class="section-heading"><h3>Item collection</h3></div><div class="loot-filters"><input class="settings-input" id="loot-search" type="search" placeholder="Search your items" aria-label="Search your items" value="${esc(lootSearch)}"><select class="settings-input" id="loot-rarity" aria-label="Item rarity"><option value="">All rarities</option></select><label><input type="checkbox" id="loot-hidden" ${includeHidden?'checked':''}> Show filtered items</label></div><div id="loot-cards" class="loot-grid"></div><p id="loot-count" class="settings-note"></p></section>`;
  }
  if(view==='loot')html+=`<div class="content-grid loot-options">${filteredItemsCard()}${vaultFilterCard()}</div>`+disclosure('reward-coverage','What rewards are included?', '<p class="muted">Regular region kills use the game’s own drop system. Event completion rewards, guaranteed boss drops and ForgePact kill triggers are not fully reproduced. Unverified packets cannot be selected for expeditions.</p>');
  if(view==='settings'){
    html+=`<div class="content-grid extra-section"><section class="card"><span class="eyebrow">YOUR HERO</span><h2 class="section-title">Character portrait</h2><p class="muted">Use your own PNG screenshot. It stays visible while the game is closed.</p>${data.portraits?.[selected]?`<img class="portrait-preview" src="/portraits/${data.portraits[selected]}.png" alt="Your saved character screenshot">`:''}<label class="quiet portrait-upload">Choose PNG screenshot<input id="portrait-file" type="file" accept="image/png"></label><p class="settings-note">For the selected hero · Up to 4 MB / 4096 × 4096 pixels.<br>A saved image, not a live equipment render.</p></section>${notificationCard()||'<section class="card"><h2>Ready when you return</h2><p class="muted">Your timer continues with this panel closed. Open Explore to check its progress.</p></section>'}</div>`;
    html+=disclosure('setup-details','Connection diagnostics',`<section class="card">${(data.installation.checks||[]).map(c=>`<div class="setup-check"><span class="badge ${c.ok?'good':''}">${c.ok?'Ready':'Action needed'}</span><div><strong>${esc(c.name)}</strong><p>${esc(c.detail)}</p></div></div>`).join('')}<p class="settings-note">Install Aurie and YYToolkit for your game build first. Presence checks do not prove a compatible runtime; the live connection also checks native interception.</p></section>`,'Game files & plugin checks');
    html+=disclosure('supported-mechanics','Supported mechanics',`<div class="support-grid">${(data.support||[]).map(s=>`<article><span class="badge ${['Available','Checked'].includes(s.status)?'good':''}">${esc(s.status)}</span><h3>${esc(s.name)}</h3><p>${esc(s.detail)}</p></article>`).join('')}</div>`,'Capabilities & limits');
  }
  if(data.support_warnings?.length)html=`<div class="notice extra-section">${data.support_warnings.map(esc).join('<br>')}</div>`+html;
  footer.insertAdjacentHTML('beforebegin',html);
  const portrait=data.portraits?.[selected];if(portrait){const avatar=document.querySelector('.avatar');if(avatar)avatar.innerHTML=`<img src="/portraits/${portrait}.png" alt="">`;}
  renderLootCards();
}
function renderLootCards(){
  const host=document.querySelector('#loot-cards');if(!host)return;
  const loot=data.rewards.find(r=>r.id===lootExpedition)?.loot||{items:[]};
  const rarity=document.querySelector('#loot-rarity');
  const names=Object.keys(loot.rarities||{}).sort();
  rarity.innerHTML='<option value="">All rarities</option>'+names.map(r=>`<option ${r===lootRarity?'selected':''}>${esc(r)}</option>`).join('');
  const rows=loot.items.filter(i=>(includeHidden||!i.filtered)&&(!lootRarity||i.rarity===lootRarity)&&i.name.toLowerCase().includes(lootSearch.toLowerCase()));
  host.innerHTML=rows.map(i=>{const key=lootExpedition+':'+i.seq;return `<article class="loot-card"><div class="item-art">${i.icon?`<img src="${esc(i.icon)}" alt="" loading="lazy">`:svg('chest')}</div><div><small>${esc(i.rarity)}${i.filtered?' · Filtered':''}</small><h3>${esc(i.name)}</h3><span>Item #${num(i.seq)}</span></div><button class="favorite" data-favorite-item="${esc(key)}" aria-label="Favorite ${esc(i.name)}" aria-pressed="${favoriteItems.includes(key)}">${favoriteItems.includes(key)?'★':'☆'}</button></article>`;}).join('')||'<p class="muted">No items match these filters.</p>';
  document.querySelector('#loot-count').textContent=`${rows.length} shown · ${loot.total||0} generated · ${loot.filtered||0} hidden by the game filter.${loot.truncated?' The first 500 records are previewed; the full spool remains available for transfer.':''}${loot.unreadable?' Some item records need review.':''}`;
}
function updateExtras(){const quality=document.querySelector('#quality-card');if(quality){const next=JSON.stringify(visibleCapture()?.quality);if(quality.dataset.value!==next){quality.dataset.value=next;quality.innerHTML=qualityContent(visibleCapture()?.quality);}}}
document.addEventListener('click',event=>{
  const b=event.target.closest('button');if(!b||b.disabled)return;
  if(b.dataset.regionSort){regionSort=b.dataset.regionSort;render();return;}
  if(b.hasAttribute('data-validation'))action('validate_start',{profile:document.querySelector('#validation-profile').value});
  if(b.hasAttribute('data-settle-partial')){const r=data.recovery||{};if(confirm(`Close this claim as a partial delivery?\n\nYou keep what was delivered: ${num(r.calls_done||0)} of ${num(r.calls_total||0)} reward calls and their items. The remaining ${num(Math.max(0,(r.calls_total||0)-(r.calls_done||0)))} are given up. Nothing is generated again. Whether the game saved the delivered XP and gold is not confirmed.`))action('settle_partial');}
  if(b.hasAttribute('data-accept-position')){const r=data.recovery||{};if(confirm(`Continue this delivery from its recorded position?\n\n${num(r.calls_done||0)} of ${num(r.calls_total||0)} reward calls were delivered before the interruption, and the item records end exactly there. The remaining ${num(Math.max(0,(r.calls_total||0)-(r.calls_done||0)))} are delivered when you claim again with the same hero in the same region. Delivered calls are not repeated. Whether the game saved the delivered XP and gold is not confirmed.${r.records_after_checkpoint?` ${num(r.records_after_checkpoint)} item records written after the recorded position are set aside in a separate file, because their reward calls are delivered again.`:''}`))action('accept_position');}
  if(b.hasAttribute('data-vault-filter-save')){const settings=vaultFilterSettings();action('save_loot_filter',{settings}).then(()=>{vaultFilterDraft=null;});}
  if(b.dataset.favoriteProfile){const id=b.dataset.favoriteProfile;favoriteProfiles=favoriteProfiles.includes(id)?favoriteProfiles.filter(x=>x!==id):[...favoriteProfiles,id];localStorage.setItem('afk-favorite-profiles',JSON.stringify(favoriteProfiles));render();}
  if(b.dataset.favoriteItem){const id=b.dataset.favoriteItem;favoriteItems=favoriteItems.includes(id)?favoriteItems.filter(x=>x!==id):[...favoriteItems,id];localStorage.setItem('afk-favorite-items',JSON.stringify(favoriteItems));renderLootCards();}
});
document.addEventListener('change',event=>{if(event.target.name==='filtered-items'&&event.target.checked)action('save_preferences',{filtered_items:event.target.value});});
document.addEventListener('input',event=>{if(event.target.id==='loot-search'){lootSearch=event.target.value;renderLootCards();}});
document.addEventListener('change',event=>{if(event.target.id==='ready-notification')action('save_preferences',{ready_notification:event.target.checked});});
document.addEventListener('change',async event=>{
  const el=event.target;
  if(el.id==='loot-expedition'){lootExpedition=el.value;lootRarity='';signature='';render();}
  if(el.dataset.vaultRarity||el.dataset.vaultOption){
    const s=JSON.parse(JSON.stringify(vaultFilterSettings()));s.rarities=s.rarities||{};
    if(el.dataset.vaultRarity)s.rarities[el.dataset.vaultRarity]=el.checked;else s[el.dataset.vaultOption]=el.checked;
    vaultFilterDraft=s;const status=document.getElementById('vault-filter-status');if(status)status.textContent=vaultFilterDirty()?'Unsaved changes.':'Saved · applies to the next transfer.';
  }
  if(el.id==='loot-rarity'){lootRarity=el.value;renderLootCards();}
  if(el.id==='loot-hidden'){includeHidden=el.checked;renderLootCards();}
  if(el.id==='portrait-file'&&el.files[0]){
    const file=el.files[0];if(file.size>4*1024*1024){toast('Choose a PNG screenshot up to 4 MB.');return;}
    const reader=new FileReader();reader.onload=()=>action('portrait',{png:String(reader.result).split(',')[1]});reader.readAsDataURL(file);
  }
});

'use strict';
let lootExpedition='',lootSearch='',lootRarity='',includeHidden=false;
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
    html+=`<div class="content-grid extra-section"><section class="card" id="quality-card">${qualityContent(visibleCapture()?.quality)}</section><section class="card"><span class="eyebrow">A SECOND LOOK</span><h2>Validate your farming pace</h2><p class="muted">Freeze a profile’s prediction, then record a separate run with the same hero, loadout and region. Your original profile is preserved. This separate accuracy check requires 10 minutes and 200 kills in each recording; it is not required to start an expedition.</p><label for="validation-profile">Reference profile</label><select class="settings-input" id="validation-profile">${data.profiles.filter(p=>p.usable&&sameHero(p.character,current())).map(p=>`<option value="${esc(p.id)}">${esc(roomName(p.room))} · ${num(p.kills_per_min,1)} / min</option>`).join('')||'<option value="">Calibrate first</option>'}</select><div class="actions"><button class="quiet" data-validation ${busy()||data.calibration?.running||calibrationReadiness()||!data.profiles.some(p=>p.usable&&sameHero(p.character,current()))?'disabled':''}>Record validation run</button><button class="quiet" data-action="restart_region" ${busy()||!regularRegion(data.live?.room)||!sameHero(data.live?.character,current())?'disabled':''}>Restart region</button></div><p class="settings-note">Restart region returns through town using the game’s normal room transition. Town and loading time are excluded. Finish the validation with the recording button above.</p>${(data.validations||[]).slice(0,3).map(v=>`<div class="validation-result"><strong>${esc(roomName(v.room))} · ${esc(v.status.replaceAll('_',' '))}</strong><p>Predicted ${num(v.metrics?.kills_per_min?.predicted)} kills · observed ${num(v.metrics?.kills_per_min?.observed)} kills</p><small>${esc((v.issues||[]).join(' ')||v.note)}</small></div>`).join('')}</section></div>`;
    document.querySelectorAll('.table tbody tr').forEach((tr,i)=>{const p=data.profiles[i];if(!p)return;const button=document.createElement('button');button.className='favorite';button.dataset.favoriteProfile=p.id;button.setAttribute('aria-label','Favorite '+roomName(p.room));button.setAttribute('aria-pressed',favoriteProfiles.includes(p.id));button.textContent=favoriteProfiles.includes(p.id)?'★':'☆';tr.firstElementChild?.prepend(button);});
  }
  if(view==='explore'&&data.recovery?.status!=='not_started'&&data.recovery&&!busy()){
    const r=data.recovery;
    html+=`<section class="card extra-section"><span class="eyebrow">DELIVERY RECOVERY</span><h2>${r.recoverable?'Your rewards were already saved':'This claim needs review'}</h2><p class="muted">${r.recoverable?'The native save, exact plan and item records agree. Reconcile the claim without generating rewards again.':esc(r.reasons.join(' '))}</p><div class="actions">${r.recoverable?'<button class="primary" data-action="recover">Recover saved claim</button>':''}<a class="quiet" href="/api/recovery-report?id=${encodeURIComponent(r.id)}" target="_blank" rel="noopener">Open recovery report</a></div></section>`;
  }
  if(view==='loot'&&data.rewards.length){
    if(!data.rewards.some(r=>r.id===lootExpedition))lootExpedition=data.rewards[0].id;
    const total=data.rewards.reduce((a,r)=>({items:a.items+(r.loot?.total||0),gold:a.gold+(r.gold||0),xp:a.xp+(r.exp||0)}),{items:0,gold:0,xp:0});
    html+=`<section class="card extra-section"><span class="eyebrow">EXPEDITION JOURNAL</span><div class="metric-row"><div class="metric"><strong>${num(total.items)}</strong><span>generated items</span></div><div class="metric"><strong>${num(total.gold)}</strong><span>recorded gold</span></div><div class="metric"><strong>${num(total.xp)}</strong><span>recorded XP</span></div></div><p class="settings-note">Totals cover the ${data.rewards.length} recent expeditions shown, including items hidden by your loot filter.</p><label for="loot-expedition">Expedition</label><select class="settings-input" id="loot-expedition">${data.rewards.map(r=>`<option value="${esc(r.id)}" ${r.id===lootExpedition?'selected':''}>${esc(r.character?.name||'Hero')} · ${esc(roomName(r.room))} · ${esc(r.id)}</option>`).join('')}</select><div class="loot-filters"><input class="settings-input" id="loot-search" type="search" placeholder="Search your items" aria-label="Search your items" value="${esc(lootSearch)}"><select class="settings-input" id="loot-rarity" aria-label="Item rarity"><option value="">All rarities</option></select><label><input type="checkbox" id="loot-hidden" ${includeHidden?'checked':''}> Show filtered items</label></div><div id="loot-cards" class="loot-grid"></div><p id="loot-count" class="settings-note"></p></section>`;
  }
  if(view==='settings'){
    html+=`<div class="content-grid extra-section"><section class="card"><span class="eyebrow">SETUP CHECKLIST</span><h2>Ready to explore?</h2>${(data.installation.checks||[]).map(c=>`<div class="setup-check"><span class="badge ${c.ok?'good':''}">${c.ok?'Ready':'Action needed'}</span><div><strong>${esc(c.name)}</strong><p>${esc(c.detail)}</p></div></div>`).join('')}<p class="settings-note">Install Aurie and YYToolkit for your game build first, then use Install AFK plugin above. Presence checks do not prove a compatible runtime; the live connection also checks native interception.</p></section><section class="card"><span class="eyebrow">YOUR HERO</span><h2>Use your character screenshot</h2><p class="muted">Choose a PNG screenshot of your hero. It is kept on this computer and stays visible while the game is closed. This is a saved image, not a live equipment render.</p>${data.portraits?.[selected]?`<img class="portrait-preview" src="/portraits/${data.portraits[selected]}.png" alt="Your saved character screenshot">`:''}<label class="quiet portrait-upload">Choose PNG screenshot<input id="portrait-file" type="file" accept="image/png"></label><p class="settings-note">Up to 4 MB and 4096 × 4096 pixels. Select your hero at the top before choosing the image.</p></section></div><section class="card extra-section"><span class="eyebrow">WHAT YOUR EXPEDITION INCLUDES</span><h2>Supported mechanics</h2><div class="support-grid">${(data.support||[]).map(s=>`<article><span class="badge ${['Available','Checked'].includes(s.status)?'good':''}">${esc(s.status)}</span><h3>${esc(s.name)}</h3><p>${esc(s.detail)}</p></article>`).join('')}</div></section>`;
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
  if(b.hasAttribute('data-validation'))action('validate_start',{profile:document.querySelector('#validation-profile').value});
  if(b.dataset.favoriteProfile){const id=b.dataset.favoriteProfile;favoriteProfiles=favoriteProfiles.includes(id)?favoriteProfiles.filter(x=>x!==id):[...favoriteProfiles,id];localStorage.setItem('afk-favorite-profiles',JSON.stringify(favoriteProfiles));render();}
  if(b.dataset.favoriteItem){const id=b.dataset.favoriteItem;favoriteItems=favoriteItems.includes(id)?favoriteItems.filter(x=>x!==id):[...favoriteItems,id];localStorage.setItem('afk-favorite-items',JSON.stringify(favoriteItems));renderLootCards();}
});
document.addEventListener('input',event=>{if(event.target.id==='loot-search'){lootSearch=event.target.value;renderLootCards();}});
document.addEventListener('change',async event=>{
  const el=event.target;
  if(el.id==='loot-expedition'){lootExpedition=el.value;lootRarity='';renderLootCards();}
  if(el.id==='loot-rarity'){lootRarity=el.value;renderLootCards();}
  if(el.id==='loot-hidden'){includeHidden=el.checked;renderLootCards();}
  if(el.id==='portrait-file'&&el.files[0]){
    const file=el.files[0];if(file.size>4*1024*1024){toast('Choose a PNG screenshot up to 4 MB.');return;}
    const reader=new FileReader();reader.onload=()=>action('portrait',{png:String(reader.result).split(',')[1]});reader.readAsDataURL(file);
  }
});

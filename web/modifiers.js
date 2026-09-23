'use strict';
let rewardDraft=null;
function modifierSettings(){return rewardDraft||data.reward_modifiers||{};}
function rewardsDirty(){return (data.modifier_fields||[]).some(f=>(modifierSettings()[f.key]??1)!==(data.reward_modifiers?.[f.key]??1));}
function rewardsStatus(){return rewardsDirty()?'Unsaved changes · save before starting an expedition.':'Saved · applies to your next expedition.';}
function rewardsSummary(settings=data.reward_modifiers){
 const fields=data.modifier_fields||[];
 const changed=fields.filter(f=>(settings?.[f.key]??1)!==1);
 return changed.length?changed.map(f=>`${esc(f.label)} ×${num(settings[f.key],2)}`).join(' · '):'Native rewards · all multipliers ×1';
}
function modifiers(){
 const settings=modifierSettings(),p=profile(),base=p?.reward_baseline?.magic_find;
 const cards=(data.modifier_fields||[]).map((field,i)=>`<section class="modifier-card ${i<3?'modifier-primary':''}"><div class="modifier-title">${svg(i===1?'timer':i===2?'chest':'settings')}<label for="reward-${esc(field.key)}">${esc(field.label)}</label></div><p>${esc(field.description)}</p><div class="modifier-control"><button type="button" class="icon-button" data-reward-step="${esc(field.key)}" data-delta="-1" aria-label="Decrease ${esc(field.label)}">−</button><span>×</span><input id="reward-${esc(field.key)}" data-reward="${esc(field.key)}" aria-label="${esc(field.label)} multiplier" type="number" min="1" max="100" step="0.5" value="${settings[field.key]??1}"><button type="button" class="icon-button" data-reward-step="${esc(field.key)}" data-delta="1" aria-label="Increase ${esc(field.label)}">+</button></div>${field.key==='magic_find'?`<small id="effective-mf">${Number.isFinite(base)?`Recorded base ${num(base)} · Estimated MF ≈ ${num(base*(settings.magic_find||1))}`:'Record a new calibration to measure your native Magic Find.'}</small>`:''}</section>`);
 return heading('YOUR EXPEDITION, YOUR REWARDS','Reward modifiers','Independent rewards. ForgePact is optional.')+jobBanner()+`
 <div class="modifier-intro card"><div><span class="eyebrow">NATIVE GAME REWARDS</span><h2>Set the rewards for your next expedition.</h2><p class="muted">×1 preserves native rates. Your measured farming pace stays the same. Magic Find uses the game’s own calculation; it is not a guaranteed rarity chance. Its final value includes native rounding and is confirmed when claiming.</p><p id="reward-save-status" role="status">${rewardsStatus()}</p></div><div class="actions"><button class="quiet" data-rewards-reset>Reset to ×1</button><button class="primary" data-rewards-save ${busy()?'disabled':''}>Save modifiers</button></div></div>
 ${data.reward_modifiers_error?`<div class="notice">${esc(data.reward_modifiers_error)} Choose and save valid settings before starting.</div>`:''}
 ${data.armed?'<div class="notice modifier-notice">An expedition is active. These changes apply to future expeditions; its recorded modifiers stay unchanged.</div>':''}
 <div class="modifier-stats">${cards.slice(0,3).join('')}</div><div class="section-heading"><h2>Drop rates</h2><p class="muted">Native pools and region conditions still apply. Mining Ore and extra Angelic/Unholy item rolls are excluded.</p></div><div class="modifier-grid">${cards.slice(3).join('')}</div>
 <p class="muted modifier-footnote">With a compatible ForgePact plugin, reward bonuses are isolated during AFK delivery so multipliers do not stack. Older ForgePact plugins need an update. New calibration is required once for profiles recorded before independent rewards.</p>`;
}
document.addEventListener('input',event=>{
 const key=event.target.dataset?.reward;if(!key)return;
 rewardDraft={...modifierSettings(),[key]:Number(event.target.value)};
 const status=document.getElementById('reward-save-status');if(status)status.textContent=rewardsStatus();
 const base=profile()?.reward_baseline?.magic_find, label=document.getElementById('effective-mf');
 if(label&&Number.isFinite(base))label.textContent=`Recorded base ${num(base)} · Estimated MF ≈ ${num(base*(rewardDraft.magic_find||1))}`;
});
document.addEventListener('click',async event=>{
 const button=event.target.closest('button');if(!button||button.disabled)return;
 if(button.hasAttribute('data-rewards-reset')){rewardDraft=Object.fromEntries((data.modifier_fields||[]).map(f=>[f.key,1]));render();}
 if(button.dataset.rewardStep){const key=button.dataset.rewardStep;rewardDraft={...modifierSettings(),[key]:Math.max(1,Math.min(100,Number(modifierSettings()[key]||1)+Number(button.dataset.delta)))};render();}
 if(button.hasAttribute('data-rewards-save')){
   const settings={...modifierSettings()};
   if(Object.values(settings).some(v=>typeof v!=='number'||!Number.isFinite(v)||v<1||v>100)){toast('Choose multipliers between 1 and 100.');return;}
   await action('save_modifiers',{settings});
 }
});

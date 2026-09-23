'use strict';
// Notifications and the tab title: tell the player when an expedition is ready
// to claim and when its delivery finishes, pauses or stops. Browser only; the
// panel page must be open. Nothing here starts or changes an action.
let notifyOn=(()=>{try{return localStorage.getItem('afk-notify')==='1';}catch{return false;}})();
const notified=new Set((()=>{try{return JSON.parse(localStorage.getItem('afk-notified')||'[]');}catch{return [];}})());
function rememberNotice(key){notified.add(key);try{localStorage.setItem('afk-notified',JSON.stringify([...notified].slice(-50)));}catch{}}
function chime(){
  try{
    const audio=new (window.AudioContext||window.webkitAudioContext)(),tone=audio.createOscillator(),gain=audio.createGain();
    tone.type='sine';tone.frequency.value=880;gain.gain.value=.06;tone.connect(gain).connect(audio.destination);
    tone.start();tone.frequency.setValueAtTime(1175,audio.currentTime+.18);
    gain.gain.exponentialRampToValueAtTime(.0001,audio.currentTime+.6);tone.stop(audio.currentTime+.62);
  }catch{}
}
function announce(key,title,body,system=true){
  if(notified.has(key))return;
  rememberNotice(key);toast(title+'. '+body);
  if(!notifyOn||!system)return;
  chime();
  if('Notification' in window&&Notification.permission==='granted'){try{new Notification(title,{body,icon:'/assets/emblem.svg',tag:key});}catch{}}
}
function watchExpedition(){
  if(!data)return;
  const armed=data.armed,pr=data.progress||{},job=data.job;
  if(armed&&!pr.state&&Date.now()>=Date.parse(armed.started_at)+armed.hours*3600000)
    announce('ready:'+armed.expedition_id,'Expedition complete','Return to the recorded hero and region to claim your rewards.',
             data.notification?.scheduled?.expedition_id!==armed.expedition_id);
  const recent=job&&job.finished_at&&Date.now()/1000-job.finished_at<600;
  if(recent&&['claim','claim_background'].includes(job.action)){
    if(job.state==='done'&&pr.resumable)announce('paused:'+job.id,'Delivery paused','Your progress is saved. Claim again to continue.');
    else if(job.state==='done')announce('claimed:'+job.id,'Rewards delivered','Your items are on the Loot page.');
    else if(job.state==='error')announce('failed:'+job.id,'Delivery stopped','Open AFK FARM to see what happened.');
  }
  document.title=deliveryActive()&&pr.calls_total?`${Math.floor(pr.percent||0)}% · ${pr.state==='paused'?'Paused':'Delivering'} · AFK FARM`:'AFK FARM · Hero Siege';
}
function notifyToggle(){
  return `<label class="notify-toggle"><input type="checkbox" id="notify-toggle" ${notifyOn?'checked':''}> Notify me when the expedition is ready and when delivery finishes</label>`;
}
document.addEventListener('change',async event=>{
  if(event.target.id!=='notify-toggle')return;
  notifyOn=event.target.checked;
  if(notifyOn&&'Notification' in window&&Notification.permission==='default'){try{await Notification.requestPermission();}catch{}}
  try{localStorage.setItem('afk-notify',notifyOn?'1':'0');}catch{}
  if(notifyOn)chime();
});
setInterval(()=>{if(data)watchExpedition();},2000);

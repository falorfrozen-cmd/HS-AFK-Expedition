'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');
const hero={identity_version:2,slot:2,name:'Suh',class:8,class_name:'Samurai',level:100};
const other={...hero,slot:5,name:'SeraphTest',class:14,class_name:'White Mage'};
function fixture(){
 const elements=new Map(),timers=[];
 const element=key=>{if(!elements.has(key))elements.set(key,{innerHTML:'',textContent:'',style:{},addEventListener(){},classList:{add(){},remove(){}}});return elements.get(key);};
 const context=vm.createContext({console,localStorage:{getItem(){return null;}},window:{addEventListener(){}},document:{querySelector:element,addEventListener(){},activeElement:null,hidden:false},fetch:()=>new Promise(()=>{}),setInterval(){},clearTimeout(){},setTimeout(fn,ms){timers.push(ms);return timers.length;}});
 vm.runInContext(source,context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/modifiers.js'),'utf8'),context);
 const data={version:'0.4.1',characters:[hero,other],profiles:[],live:{character:other,room:'Act_01_01',capture_on:false},game_running:true,calibration:{character:hero,room:'Act_03_03',running:false,seconds:10,kills:2,rate:12},progress:{},job:null,armed:null,rewards:[],installation:{checks:[]},config:{},validations:[],portraits:{}};
 const set=d=>{context.payload=d;vm.runInContext('data=payload;zones=[{room:"Act_01_01",name:"Forest",act:1,world:"old",x:50,y:50},{room:"Act_03_03",name:"Desert",act:3,world:"old",x:55,y:55}];',context);};set(data);
 return {context,data,elements,timers,set,run:code=>vm.runInContext(code,context)};
}
test('finished recording never supplies another selected hero’s name, counters or quality',()=>{
 const f=fixture();f.run('selected=5;view="measure"');assert.equal(f.run('visibleCapture()'),null);
 const html=f.run('measurement()');assert.match(html,/<h2>SeraphTest<\/h2>/);assert.match(html,/In game: SeraphTest · Forest/);assert.doesNotMatch(html,/id="recording-context"/);assert.match(html,/id="capture-kills">0<\/strong>/);assert.equal(f.run('calibrationReadiness()'),'');
});
test('selected hero, live hero and previous recording are explicitly distinct',()=>{
 const f=fixture();f.data.live.room='Town_01_rm';f.set(f.data);f.run('selected=2;view="measure"');
 const html=f.run('measurement()');assert.match(html,/In game: SeraphTest · Town/);assert.match(html,/Last recording: Suh · Desert/);assert.match(html,/select Suh \(slot 3\)/);assert.match(html,/data-action="capture_start" disabled/);
 assert.doesNotMatch(html,/data-action="launch"/);assert.match(html,/data-view="explore">.*Open expedition planner/);
});
test('an active expedition does not lock or silently reset selected hero',()=>{
 const f=fixture();f.data.armed={hours:1,started_at:new Date().toISOString()};f.data.plan={character:hero,zones:[{room:'Act_03_03'}]};f.set(f.data);f.run('selected=5;view="explore";extrasPanel=()=>{};bindMap=()=>{};updateLive=()=>{};render()');
 assert.equal(f.run('selected'),5);const html=f.elements.get('#app').innerHTML;assert.match(html,/<select id="character">/);assert.match(html,/Expedition hero: <strong>Suh<\/strong>/);assert.match(html,/Playing SeraphTest/);assert.match(html,/data-action="claim" disabled/);
});
test('last errors are scoped to their page and dismissible without losing the log',()=>{
 const f=fixture();f.data.job={id:'j',action:'capture_start',state:'error',error:'Old region warning'};f.set(f.data);
 assert.equal(f.run('view="explore";jobBanner()'),'');assert.match(f.run('view="measure";jobBanner()'),/Last attempt: Old region warning/);
 assert.equal(f.run('dismissedJob="j";jobBanner()'),'');assert.equal(f.run('data.job.error'),'Old region warning');
});
test('overlapping polls share one request and keep active progress at 500 ms',async()=>{
 const f=fixture();let resolve,calls=0;f.context.fetch=()=>{calls++;return new Promise(r=>resolve=r);};
 f.run('render=()=>{};updateLive=()=>{};toast=()=>{}');f.data.job={id:'j',state:'running'};
 const both=f.run('Promise.all([poll(),poll()])');assert.equal(calls,1);resolve({ok:true,json:async()=>f.data});await both;assert.equal(f.timers.at(-1),500);
 f.data.job=null;const idle=f.run('poll()');resolve({ok:true,json:async()=>f.data});await idle;assert.equal(f.timers.at(-1),2000);
});
test('stopped or unrelated capture never disables a valid saved profile start',()=>{
 const f=fixture();f.data.live=null;f.data.game_running=false;f.data.profiles=[{id:'suh',room:'Act_03_03',character:hero,usable:true,kills_per_min:20,basis_seconds:240,coverage:1}];f.set(f.data);f.run('selected=2');
 const html=f.run('explore()');assert.match(html,/data-action="start" >|data-action="start">/);assert.match(html,/Play another hero or close the game/);
});

test('game launch is distinct from offline profile use and only offered when it can work',()=>{
 const f=fixture();f.run('selected=2');
 assert.equal(f.run('gameLaunchButton()'),'');
 f.data.profiles=[{id:'suh',room:'Act_03_03',character:hero,usable:true,kills_per_min:20,basis_seconds:240,coverage:1}];f.set(f.data);
 const html=f.run('explore()');assert.match(html,/data-action="start" >|data-action="start">/);assert.doesNotMatch(html,/data-action="launch"/);
 f.data.live={character:null,room:'Game_Start_rm'};f.set(f.data);assert.equal(f.run('gameLaunchButton()'),'');
 f.data.live.room='Chose_rm';f.set(f.data);assert.match(f.run('gameLaunchButton()'),/Open selected hero in game/);
 f.data.live=null;f.data.game_running=false;f.set(f.data);assert.match(f.run('gameLaunchButton()'),/Launch game for calibration \/ claim/);
});
test('saved settings and unsaved drafts stay separate from the active reward policy',()=>{
 const f=fixture();f.data.modifier_fields=[{key:'magic_find',label:'Magic Find'},{key:'experience',label:'Experience'}];
 f.data.reward_modifiers={magic_find:40,experience:2};f.data.armed={hours:1};f.data.plan={character:hero,zones:[{room:'Act_03_03'}],reward_modifiers:{magic_find:40,experience:2}};f.set(f.data);
 assert.match(f.run('rewardsStatus()'),/^Saved/);f.run('rewardDraft={magic_find:40,experience:3}');assert.match(f.run('rewardsStatus()'),/^Unsaved/);
 assert.match(f.run('expeditionPanel()'),/Magic Find ×40 · Experience ×2/);
 assert.doesNotMatch(f.run('expeditionPanel()'),/Experience ×3/);
 f.data.reward_modifiers.experience=3;f.set(f.data);assert.match(f.run('rewardsStatus()'),/^Saved/);
});

test('a stopped short recording shows why there is no profile on calibration and on its map region',()=>{
 const f=fixture();f.data.calibration={character:hero,room:'Act_03_03',session:'current.ndjson',running:false,seconds:46.667,kills:790,rate:1015.7,
  outcome:{status:'insufficient',title:'No profile saved — below the minimum',message:'Recorded 47 of 60 required seconds and 790 of 30 required kills. The raw recording is preserved.',min_seconds:60,min_kills:30,minimum_ready:false}};
 f.set(f.data);f.run('selected=2');
 assert.match(f.run('measurement()'),/No profile saved — below the minimum/);assert.match(f.run('measurement()'),/0:46 \/ 1:00/);
 const planner=f.run('explore()');assert.match(planner,/Recorded 47 of 60/);assert.doesNotMatch(planner,/No current calibration/);
 f.data.job={action:'capture_stop',state:'done'};f.set(f.data);assert.doesNotMatch(f.run('completedActionMessage()'),/completed|Calibration saved/i);
 f.run('selected=5');assert.doesNotMatch(f.run('explore()'),/Recorded 47 of 60/);
 f.data.calibration.running=true;f.set(f.data);assert.doesNotMatch(f.run('explore()'),/Recorded 47 of 60/);
});

test('finish remains disabled before the minimum with an explicit way to stop, then enables without restarting capture',()=>{
 const f=fixture();f.data.live={character:hero,room:'Act_03_03'};f.data.calibration={character:hero,room:'Act_03_03',running:true,seconds:47,kills:790,outcome:{status:'recording',minimum_ready:false}};
 f.set(f.data);f.run('selected=2');
 let html=f.run('calibrationActions()');assert.match(html,/class="primary" data-action="capture_stop" disabled/);assert.match(html,/Stop without saving a profile/);assert.match(html,/Restart region/);
 f.data.calibration.outcome={status:'ready',minimum_ready:true};f.set(f.data);
 html=f.run('calibrationActions()');assert.match(html,/class="primary" data-action="capture_stop" >/);assert.doesNotMatch(html,/Stop without saving/);
});

test('a saved calibration links to its exact hero and region even when another map region was selected',()=>{
 const f=fixture();const p={id:'saved',character:hero,room:'Act_03_03',usable:true,problems:[],basis_seconds:240,kills_per_min:20};f.data.profiles=[p];
 f.data.calibration={character:hero,room:'Act_03_03',running:false,seconds:240,kills:80,outcome:{status:'saved',profile_id:'saved',title:'Calibration saved',message:'Ready.',min_seconds:60,min_kills:30}};
 f.set(f.data);f.run('selected=2');assert.match(f.run('calibrationActions()'),/data-use-profile="saved"/);
 f.context.localStorage.setItem=()=>{};f.run('render=()=>{};centerMap=()=>{};selected=5;room="Act_01_01";useSavedProfile("saved")');
 assert.equal(f.run('selected'),2);assert.equal(f.run('room'),'Act_03_03');assert.equal(f.run('view'),'explore');assert.equal(f.run('profile().id'),'saved');
});

function delivering(){
 const f=fixture();f.data.live=null;f.data.armed={expedition_id:'farm',hours:2,started_at:new Date().toISOString()};
 f.data.plan={character:hero,zones:[{room:'Act_03_03'}]};f.data.job={id:'claim',action:'claim',state:'running'};
 f.data.progress={expedition_id:'farm_claim',state:'running',calls_done:100,calls_total:1000,items:12,items_filtered:9,percent:10};
 f.data.recovery={status:'needs_review',recoverable:false,reasons:['Native delivery has no completed checkpoint.']};f.set(f.data);return f;
}
test('an active claim is shown as delivery, without false recovery or disconnected-game advice',()=>{
 const f=delivering();const html=f.run('expeditionPanel()');
 assert.doesNotMatch(html,/uncertain outcome|Play another hero or close the game|Load Suh.*to claim|Return early/);
 assert.match(html,/Delivering rewards/);assert.match(html,/Keep Suh in Desert/);assert.match(html,/data-action="claim" disabled/);
 assert.match(f.run('explore()'),/Generating your rewards/);assert.doesNotMatch(f.run('explore()'),/Play any hero or close the game/);
 assert.equal(f.run('gameStatus()'),'Delivering rewards');
 assert.match(f.run('loot()'),/id="delivery-items">12/);
});
test('paused and finishing delivery have distinct truthful messages',()=>{
 const f=delivering();f.data.progress.state='paused';f.data.progress.pause='no player instance in this room';f.set(f.data);
 assert.match(f.run('expeditionPanel()'),/Delivery paused/);assert.match(f.run('expeditionPanel()'),/Return as Suh to Desert/);assert.doesNotMatch(f.run('expeditionPanel()'),/uncertain outcome/);
 f.data.progress.state='done';f.set(f.data);assert.match(f.run('expeditionPanel()'),/Finishing reward delivery/);
});
test('native errors and orphaned progress retain recovery warnings',()=>{
 const f=delivering();f.data.progress.reconciliation_required=true;f.set(f.data);assert.match(f.run('expeditionPanel()'),/uncertain outcome/);
 f.data.progress.reconciliation_required=false;f.data.progress.state='error';f.set(f.data);assert.match(f.run('expeditionPanel()'),/uncertain outcome/);
 f.data.progress.state='running';f.data.job.state='error';f.set(f.data);assert.match(f.run('expeditionPanel()'),/uncertain outcome/);
 assert.match(f.run('explore()'),/Reward delivery needs review/);assert.doesNotMatch(f.run('expeditionPanel()'),/Return early|Load Suh.*to claim/);
 assert.equal(f.run('deliveryCard()'),'');
});

'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');
const hero={identity_version:2,slot:2,name:'Suh',class:8,class_name:'Samurai',level:100};
const other={...hero,slot:5,name:'SeraphTest',class:14,class_name:'White Mage'};
function fixture(){
 const elements=new Map(),timers=[],listeners=new Map(),bootRequests=[];
 const element=key=>{if(!elements.has(key))elements.set(key,{innerHTML:'',textContent:'',style:{},addEventListener(){},classList:{add(){},remove(){}}});return elements.get(key);};
 const context=vm.createContext({console,localStorage:{getItem(){return null;}},window:{addEventListener(){}},document:{querySelector:element,querySelectorAll:()=>[],addEventListener(type,callback){if(!listeners.has(type))listeners.set(type,[]);listeners.get(type).push(callback);},activeElement:null,hidden:false},fetch:url=>{bootRequests.push(url);return new Promise(()=>{});},setInterval(){},clearTimeout(){},setTimeout(fn,ms){timers.push(ms);return timers.length;}});
 vm.runInContext(source,context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/modifiers.js'),'utf8'),context);
 const data={version:'0.4.1',characters:[hero,other],profiles:[],live:{character:other,room:'Act_01_01',capture_on:false},game_running:true,calibration:{character:hero,room:'Act_03_03',running:false,seconds:10,kills:2,rate:12},progress:{},job:null,armed:null,rewards:[],installation:{checks:[]},config:{},validations:[],portraits:{}};
 const set=d=>{context.payload=d;vm.runInContext('data=payload;zones=[{room:"Act_01_01",name:"Forest",act:1,world:"old",x:50,y:50},{room:"Act_03_03",name:"Desert",act:3,world:"old",x:55,y:55}];',context);};set(data);
 return {context,data,elements,timers,listeners,bootRequests,set,run:code=>vm.runInContext(code,context)};
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

const speeds=[{id:'normal',label:'Normal',calls_per_second:50,seconds:1200},{id:'fast',label:'Fast',calls_per_second:80,seconds:750},{id:'max',label:'Maximum',calls_per_second:100,seconds:600}];
function armedHere(){
 const f=fixture();f.data.live={character:hero,room:'Act_03_03',game_build:'B',farm_context:{hash:'a'}};f.data.armed={expedition_id:'farm',hours:2,started_at:new Date(Date.now()-3*3600e3).toISOString()};
 f.data.plan={character:hero,zones:[{room:'Act_03_03'}],game_build:'B',farm_context:{hash:'a'}};f.data.progress={};f.data.delivery={calls:60000,speeds,active_speed:'normal'};
 f.data.preferences={delivery_speed:'fast'};f.data.background={available:true};f.data.recovery={status:'not_started'};f.set(f.data);return f;
}
test('claim offers a delivery speed with this computer’s time estimate and a background claim',()=>{
 const f=armedHere();const html=f.run('expeditionPanel()');
 assert.match(html,/id="delivery-speed"/);assert.match(html,/value="fast" selected>Fast · about 13 min/);assert.match(html,/Maximum · about 10 min/);
 assert.match(html,/data-action="claim" >|data-action="claim">/);assert.match(html,/Claim in background/);assert.match(html,/closes the game again/);
 f.data.background={available:false,reason:'Another hero is loaded. Return to the main menu or close the game first.'};f.set(f.data);
 assert.match(f.run('expeditionPanel()'),/data-action="claim_background" disabled/);assert.match(f.run('expeditionPanel()'),/Another hero is loaded/);
});
test('a saved pause continues instead of asking for review, keeping its original speed',()=>{
 const f=armedHere();f.data.progress={expedition_id:'farm_claim',state:'aborted',resumable:true,calls_done:400,calls_total:1000,percent:40};f.data.delivery.active_speed='max';
 f.data.recovery={status:'paused',resumable:true,recoverable:false,reasons:['Delivery was paused at a saved position.']};f.set(f.data);
 const html=f.run('expeditionPanel()');
 assert.match(html,/Delivery paused safely/);assert.match(html,/400 of 1,000 reward calls/);assert.match(html,/Continue delivery/);
 assert.match(html,/Maximum \(kept from the paused delivery\)/);assert.doesNotMatch(html,/uncertain outcome|Review required/);
 assert.equal(f.run('deliveryNeedsReview()'),false);
});
test('an active delivery shows time left and a pause button',()=>{
 const f=delivering();f.data.delivery={speeds,active_speed:'normal'};f.data.progress.calls_total=55000;f.set(f.data);
 const card=f.run('deliveryCard()');assert.match(card,/id="delivery-eta">about 18 min \(estimate\)/);assert.match(card,/data-action="pause_delivery" >|data-action="pause_delivery">/);
 assert.match(card,/Closing the game window pauses and saves it/);
 f.run('etaSamples.push({at:Date.now()-10000,done:0},{at:Date.now(),done:1000})');assert.equal(f.run('deliveryEtaText()'),'about 9 min');
 f.data.progress.state='paused';f.set(f.data);assert.equal(f.run('deliveryEtaText()'),'Paused');
});
test('a claim that needs review can be closed as a partial delivery after a confirmation, then transferred',()=>{
 const f=delivering();f.data.job=null;f.data.recovery={id:'farm_claim',status:'needs_review',recoverable:false,reasons:['Native delivery has no completed checkpoint.'],calls_done:80223,calls_total:102006};f.set(f.data);
 const listeners={};f.context.document.addEventListener=(type,fn)=>{listeners[type]=fn;};
 let inserted='';f.elements.set('.workspace',{querySelector:()=>({insertAdjacentHTML:(_,html)=>{inserted+=html;}})});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/extras.js'),'utf8'),f.context);
 const panel=f.run('expeditionPanel()');
 assert.match(panel,/uncertain outcome at 80,223 of 102,006 reward calls/);assert.match(panel,/data-settle-partial >|data-settle-partial>/);
 assert.match(panel,/gives up the remaining 21,783 reward calls/);assert.match(panel,/recovery-report\?id=farm_claim/);assert.doesNotMatch(panel,/Review required|data-action="claim"/);
 f.run('view="explore";extrasPanel()');
 assert.match(inserted,/This claim needs review/);assert.match(inserted,/in the expedition panel above/);assert.doesNotMatch(inserted,/data-settle-partial/,'the action is not repeated below');
 const calls=[];f.context.recorded=calls;f.run('action=(name,args)=>{recorded.push(name);return Promise.resolve();}');
 let asked='';const button={disabled:false,dataset:{},hasAttribute:n=>n==='data-settle-partial'};
 f.context.confirm=m=>{asked=m;return false;};listeners.click({target:{closest:()=>button}});
 assert.match(asked,/remaining 21,783 are given up/);assert.deepEqual(calls,[]);
 f.context.confirm=()=>true;listeners.click({target:{closest:()=>button}});assert.deepEqual(calls,['settle_partial']);
 f.run('pending=true');assert.match(f.run('expeditionPanel()'),/data-settle-partial disabled/);f.run('pending=false');
 f.data.recovery={status:'saved',recoverable:true,reasons:[]};f.set(f.data);assert.doesNotMatch(f.run('expeditionPanel()'),/data-settle-partial/);
 f.data.job=null;f.data.editor='http://127.0.0.1:8791';f.data.rewards=[{id:'farm_claim',state:'partial',partial:true,save_confirmed:false,items:5,gold:0,stages:{ingest:'pending'}}];f.set(f.data);
 const chest=f.run('loot()');assert.match(chest,/<span class="badge ">Partial<\/span>/);assert.match(chest,/data-ingest="farm_claim" >Transfer to Vault/);assert.doesNotMatch(chest,/Review save receipt/);
});
test('a delivery a crash cut short offers to continue from its recorded position, then shows it accepted',()=>{
 const f=delivering();f.data.job=null;
 f.data.recovery={id:'farm_claim',status:'needs_review',recoverable:false,continuable:true,continue_blockers:[],reasons:['Native delivery has no completed checkpoint.'],calls_done:80223,calls_total:102006};f.set(f.data);
 const listeners={};f.context.document.addEventListener=(type,fn)=>{listeners[type]=fn;};
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/extras.js'),'utf8'),f.context);
 let html=f.run('expeditionPanel()');
 assert.match(html,/item records end exactly at that position/);assert.match(html,/data-accept-position >|data-accept-position>/);
 assert.match(html,/Continue from recorded position/);assert.match(html,/Delivers the remaining 21,783 reward calls/);assert.match(html,/data-settle-partial/);
 const calls=[];f.context.recorded=calls;f.run('action=(name,args)=>{recorded.push(name);return Promise.resolve();}');
 let asked='';const button={disabled:false,dataset:{},hasAttribute:n=>n==='data-accept-position'};
 f.context.confirm=m=>{asked=m;return true;};listeners.click({target:{closest:()=>button}});
 assert.match(asked,/remaining 21,783 are delivered when you claim again/);assert.match(asked,/not confirmed/);assert.deepEqual(calls,['accept_position']);
 f.data.recovery={...f.data.recovery,continuable:false,continue_blockers:['The item records hold 4 items but the checkpoint counts 3.']};f.set(f.data);
 html=f.run('expeditionPanel()');assert.match(html,/It cannot continue from there: The item records hold 4 items/);assert.doesNotMatch(html,/data-accept-position/);
 const a=armedHere();a.data.progress={expedition_id:'farm_claim',state:'running',resumable:true,resume_accepted:true,calls_done:80223,calls_total:102006,percent:78.6};
 a.data.recovery={status:'paused',resumable:true,accepted:true,recoverable:false,reasons:['You accepted the recorded position.']};a.set(a.data);
 html=a.run('expeditionPanel()');
 assert.match(html,/Recorded position accepted/);assert.match(html,/80,223 of 102,006 reward calls were delivered/);assert.match(html,/Continue delivery/);
 assert.match(html,/Continues from the recorded position/);assert.match(html,/Close as partial delivery instead/);assert.doesNotMatch(html,/delivered and saved|uncertain outcome/);
});
test('region comparison lists the selected hero’s regions, marks the best and sorts by a column',()=>{
 const f=fixture();vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/extras.js'),'utf8'),f.context);
 f.data.regions=[{character:hero,rows:[
   {profile:'glacier',usable:true,room:'Act_02_05',name:'The Glacial Trail',kills_per_min:778.7,xp_per_hour:1306084290,gold_per_hour:234923,rarities_per_hour:{Unholy:0,Angelic:0,Heroic:59.8,Satanic:3820.3},expeditions:1,hours:1.57,magic_find:[10]},
   {profile:'desert',usable:false,room:'Act_03_03',name:"Mos'Arathim Desert",kills_per_min:201.4,xp_per_hour:48329858,gold_per_hour:14686,rarities_per_hour:{},expeditions:0,hours:0,magic_find:[]}]},
  {character:other,rows:[{profile:'x',usable:true,room:'Act_01_01',name:'Other place',kills_per_min:1,xp_per_hour:1,gold_per_hour:null,rarities_per_hour:{},expeditions:0,hours:0,magic_find:[]}]}];
 f.set(f.data);f.run('selected=2');
 let html=f.run('regionCard()');
 assert.match(html,/Where Suh farms best/);assert.doesNotMatch(html,/Other place/);
 assert.match(html,/<td class="best">1,306,084,290<\/td>/);assert.match(html,/1\.57 h · 1 expedition · MF ×10/);assert.match(html,/Calibration only/);
 assert.match(html,/data-use-profile="glacier">Plan here/);assert.match(html,/Needs recalibration/);assert.match(html,/Mos&#39;Arathim Desert|Mos'Arathim Desert/);
 assert.ok(html.indexOf('The Glacial Trail')<html.indexOf('Arathim'),'highest XP per hour first');
 f.run('regionSort="kills_per_min"');html=f.run('regionCard()');assert.ok(html.indexOf('The Glacial Trail')<html.indexOf('Arathim'));
 f.data.regions[0].rows[1].kills_per_min=900;f.set(f.data);html=f.run('regionCard()');assert.ok(html.indexOf('Arathim')<html.indexOf('The Glacial Trail'),'sorted by the chosen column');
 f.run('selected=9');assert.equal(f.run('regionCard()'),'','no card for a hero without calibrations');
});
test('the loot page offers the filtered-items choice and the summary shows what it did',()=>{
 const f=fixture();const listeners={};f.context.document.addEventListener=(type,fn)=>{(listeners[type]=listeners[type]||[]).push(fn);};
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/extras.js'),'utf8'),f.context);
 let html=f.run('filteredItemsCard()');
 assert.match(html,/value="convert" checked/);assert.doesNotMatch(html,/value="keep" checked/);assert.match(html,/stacks of up to 999/);assert.match(html,/offline and not connected/);
 f.data.preferences={filtered_items:'keep'};f.set(f.data);assert.match(f.run('filteredItemsCard()'),/value="keep" checked/);
 const calls=[];f.context.recorded=calls;f.run('action=(name,args)=>{recorded.push([name,args]);return Promise.resolve();}');
 for(const fn of listeners.change||[])fn({target:{name:'filtered-items',value:'convert',checked:true,id:'',dataset:{}}});
 assert.deepEqual(JSON.parse(JSON.stringify(calls)),[['save_preferences',{filtered_items:'convert'}]]);
 const line=f.run(`conversionLine(${JSON.stringify({enabled:true,sold_items:61733,sell_gold:3544084,prospected_items:2390,output_stacks:39,kept_items:3,created:{'14:60':38154},pending:{},note:''})})`);
 assert.match(line,/Sold 61,733 filtered items for <b>3,544,084<\/b> gold/);assert.match(line,/broke 2,390 down into <b>38,154<\/b> fragments \(39 stacks\)/);assert.match(line,/3 Satanic and above kept/);
 assert.match(f.run(`conversionLine(${JSON.stringify({enabled:true,sold_items:0,prospected_items:0,created:{},pending:{'14:60':12},note:'selling stopped: the game is online'})})`),/12 fragments still pending.*selling stopped: the game is online/s);
 assert.equal(f.run('conversionLine({enabled:false})'),'');assert.equal(f.run('conversionLine(undefined)'),'');
});
test('farm again restarts the last settled expedition and the planner warns about a changed loadout',()=>{
 const f=fixture();const p={id:'suh',room:'Act_03_03',character:hero,usable:true,kills_per_min:20,basis_seconds:240,coverage:1,problems:[]};f.data.profiles=[p];
 f.data.repeat={room:'Act_03_03',hours:2,slot:2,name:'Suh',profile:'suh'};f.set(f.data);f.run('selected=2;room="Act_03_03"');
 let html=f.run('explore()');assert.match(html,/FARM AGAIN/);assert.match(html,/Desert · 2 h/);assert.match(html,/data-action="repeat"/);
 f.data.profile_live_matches={suh:false};f.set(f.data);html=f.run('explore()');assert.match(html,/Your loadout changed since this calibration/);assert.equal(f.run('loadoutChanged(profile())'),true);
 f.data.profile_live_matches={suh:true};f.set(f.data);assert.doesNotMatch(f.run('explore()'),/Your loadout changed|loadout are checked/);assert.match(f.run('explore()'),/Saved profile ready/);
 f.data.repeat={room:'Act_03_03',hours:2,slot:2,name:'Suh',profile:null,reason:'Recalibrate first.'};f.set(f.data);assert.doesNotMatch(f.run('explore()'),/data-action="repeat"/);
});


test('disclosures keep open and closed choices across rerenders and ignore detached toggle events',()=>{
 const f=fixture();f.run('extrasPanel=()=>{};bindMap=()=>{};updateLive=()=>{}');
 const detail={dataset:{disclosure:'expedition-help'},open:true,isConnected:true,matches:()=>true};
 f.context.document.querySelectorAll=()=>[detail];f.run('render()');
 assert.match(f.run("disclosure('expedition-help','Help','Contents')"),/data-disclosure="expedition-help" open/);
 // Closing it immediately before a render must win even before the toggle event fires.
 detail.open=false;f.run('render()');
 assert.doesNotMatch(f.run("disclosure('expedition-help','Help','Contents','',true)"),/data-disclosure="expedition-help" open/);
 for(const toggle of f.listeners.get('toggle'))toggle({target:{...detail,open:true,isConnected:false}});
 assert.doesNotMatch(f.run("disclosure('expedition-help','Help','Contents')"),/data-disclosure="expedition-help" open/);
 f.context.document.querySelectorAll=()=>[];f.run('view="loot";render();view="explore";render()');
 assert.doesNotMatch(f.run("disclosure('expedition-help','Help','Contents')"),/data-disclosure="expedition-help" open/);
});

test('control focus survives a rerender and explicit page navigation focuses the page title',()=>{
 const f=fixture();f.run('extrasPanel=()=>{};bindMap=()=>{};updateLive=()=>{}');
 let focusCalls=0;
 f.context.document.activeElement={id:'reward-magic_find-increase',matches:()=>false};
 f.elements.set('#reward-magic_find-increase',{focus(options){focusCalls++;assert.equal(options.preventScroll,true);}});
 f.run('render()');assert.equal(focusCalls,1);
 let headingFocus=false,scrolled=false;
 f.elements.set('#page-title',{focus(options){headingFocus=options.preventScroll;}});
 f.context.window.scrollTo=options=>{scrolled=options.top===0;};
 f.run('switchView("measure")');assert.equal(headingFocus,true);assert.equal(scrolled,true);
});

test('duration slider keeps pressed preset states and estimates synchronized',()=>{
 const f=fixture();f.data.profiles=[{id:'suh',room:'Act_03_03',character:hero,usable:true,kills_per_min:20}];f.set(f.data);
 const buttons=[1,2,4,8].map(hours=>({dataset:{hours:String(hours)},classList:{toggle(key,on){this[key]=on;}},setAttribute(key,value){this[key]=value;}}));
 f.context.document.querySelectorAll=()=>buttons;
 for(const input of f.listeners.get('input'))input({target:{id:'duration',value:'4',dataset:{}}});
 assert.deepEqual(buttons.map(b=>b['aria-pressed']),['false','false','true','false']);
 assert.equal(f.elements.get('#estimated-kills').textContent,'4,800');
});

test('delivery history keeps partial and untransferred rewards actionable and distinguishes save from Vault status',()=>{
 const f=fixture();f.data.rewards=[{id:'r1',character:hero,room:'Act_03_03',state:'partial',partial:true,save_confirmed:false,items:2,stages:{ingest:'pending'}}];f.set(f.data);
 let html=f.run('loot()');assert.match(html,/data-disclosure="delivery-history" open/);assert.match(html,/>Partial<\/span>/);assert.match(html,/data-ingest="r1"/);
 f.data.rewards[0]={...f.data.rewards[0],partial:false,save_confirmed:true,stages:{ingest:'done'}};f.set(f.data);
 html=f.run('loot()');assert.doesNotMatch(html,/data-disclosure="delivery-history" open/);assert.match(html,/>Saved<\/span>/);assert.match(html,/>Transferred<\/span>/);assert.match(html,/Transfer again/);
});


test('startup waits for deferred UI modules before fetching and rendering state',async()=>{
 const f=fixture();assert.deepEqual(f.bootRequests,[]);
 const requests=[];f.context.fetch=async url=>{requests.push(url);return {ok:true,json:async()=>url==='/zones.json'?[{room:'Act_03_03',name:'Desert',act:3,world:'old'}]:f.data};};
 f.run('let rendered=false;extrasPanel=()=>{};bindMap=()=>{};updateLive=()=>{};centerMap=()=>{};render=()=>{rendered=true;};');
 await f.listeners.get('DOMContentLoaded')[0]();
 assert.deepEqual(requests,['/zones.json','/api/state']);assert.equal(f.run('rendered'),true);
});

test('settings name the reminder task and open diagnostics while a setup check needs action',()=>{
 const f=fixture();let inserted='';f.elements.set('.workspace',{querySelector:()=>({insertAdjacentHTML:(_,html)=>{inserted+=html;}})});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/extras.js'),'utf8'),f.context);
 f.data.notification={windows:true};f.data.preferences={ready_notification:true};f.data.support=[];
 f.data.installation={checks:[{name:'Game folder',ok:true,detail:'Found'},{name:'AFK plugin',ok:false,detail:'Not installed'}]};f.set(f.data);
 f.run('view="settings";extrasPanel()');
 assert.match(inserted,/Windows scheduled task \(AFK FARM\\Expedition ready\)/);
 assert.match(inserted,/data-disclosure="setup-details" open>/);assert.match(inserted,/1 check needs action/);
 inserted='';f.data.installation.checks.push({name:'Aurie',ok:false,detail:'Missing'});f.set(f.data);f.run('extrasPanel()');assert.match(inserted,/2 checks need action/);
 inserted='';f.data.installation.checks=f.data.installation.checks.map(c=>({...c,ok:true}));f.set(f.data);f.run('extrasPanel()');
 assert.match(inserted,/data-disclosure="setup-details" >/);assert.match(inserted,/Game files & plugin checks/);
 inserted='';f.run('disclosureState.set("setup-details",false)');f.data.installation.checks[0].ok=false;f.set(f.data);f.run('extrasPanel()');
 assert.match(inserted,/data-disclosure="setup-details" >/,'a section the player closed stays closed');
});

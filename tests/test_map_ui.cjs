'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');

function fixture(){
  let now=1000;
  const captured=new Set(),classes=new Set(),listeners=new Map(),windowEvents=new Map(),documentEvents=new Map();
  const target=(isNode=false)=>({
    dataset:isNode?{room:'Act_01_01'}:{},
    closest(selector){return isNode&&['.map-node','button,a.brand'].includes(selector)?this:null;},
    setPointerCapture(id){captured.add(id);},hasPointerCapture:id=>captured.has(id),releasePointerCapture:id=>captured.delete(id)
  });
  const viewport={...target(),clientWidth:800,clientHeight:500,classList:{add:s=>classes.add(s),remove:s=>classes.delete(s)},
    getBoundingClientRect(){return {left:100,top:80,width:this.clientWidth,height:this.clientHeight};},
    addEventListener(name,fn){if(!listeners.has(name))listeners.set(name,[]);listeners.get(name).push(fn);}
  };
  const node=target(true),stage={offsetWidth:1450,offsetHeight:799,parentElement:viewport,style:{}},label={},out={},inside={};
  const elements={'map-stage':stage,'map-viewport':viewport,'map-zoom':label};
  const generic={addEventListener(){},style:{},textContent:'',innerHTML:''};
  const context=vm.createContext({console,Date:class extends Date{static now(){return now;}},
    localStorage:{getItem(){return null;},setItem(){}},
    window:{addEventListener:(name,fn)=>windowEvents.set(name,fn)},
    document:{getElementById:id=>elements[id]||null,querySelector:s=>s==='[data-map="out"]'?out:s==='[data-map="in"]'?inside:generic,
      activeElement:null,hidden:false,addEventListener:(name,fn)=>documentEvents.set(name,fn)},
    fetch:()=>new Promise(()=>{}),setTimeout(){return 1;},clearTimeout(){},setInterval(){}
  });
  vm.runInContext(source,context);
  const run=code=>vm.runInContext(code,context);
  run('zones=[{room:"Act_03_03",x:50,y:50}];zoom=1;bindMap()');
  const event=(props={})=>({pointerId:1,isPrimary:true,button:0,buttons:1,pointerType:'mouse',clientX:450,clientY:330,target:viewport,detail:1,
    preventDefault(){this.defaultPrevented=true;},stopImmediatePropagation(){this.stopped=true;},...props});
  const fire=(name,props={})=>{const e=event(props);for(const fn of listeners.get(name)||[]){fn(e);if(e.stopped)break;}return e;};
  return {run,context,viewport,stage,node,captured,classes,label,out,inside,fire,windowEvents,documentEvents,event,
    advance:ms=>{now+=ms;},pan:()=>JSON.parse(run('JSON.stringify(pan)'))};
}

test('background and region markers both follow a held pointer without selecting while dragging',()=>{
  for(const onNode of [false,true]){
    const f=fixture(),target=onNode?f.node:f.viewport;
    f.fire('pointerdown',{target});assert.ok(f.captured.has(1));assert.ok(f.classes.has('dragging'));
    f.fire('pointermove',{target,clientX:530,clientY:365});assert.deepEqual(f.pan(),{x:80,y:35});
    f.fire('pointermove',{target,clientX:550,clientY:355});assert.deepEqual(f.pan(),{x:100,y:25});
    f.fire('pointerup',{target});assert.equal(f.captured.size,0);assert.equal(f.classes.size,0);
    const click=f.fire('click',{target});assert.ok(click.defaultPrevented);assert.ok(click.stopped);
  }
});

test('small mouse jitter remains a region click; a new click and keyboard activation work after dragging',()=>{
  const f=fixture();f.fire('pointerdown',{target:f.node});f.fire('pointermove',{clientX:452,clientY:331});
  f.fire('pointerup');assert.deepEqual(f.pan(),{x:0,y:0});assert.equal(f.fire('click',{target:f.node}).stopped,undefined);
  f.fire('pointerdown');f.fire('pointermove',{clientX:500});f.fire('pointerup');
  assert.equal(f.fire('click',{target:f.node,detail:0}).stopped,undefined);
  f.fire('pointerdown',{target:f.node});f.fire('pointerup');assert.equal(f.fire('click',{target:f.node}).stopped,undefined);
});

test('pan bounds use actual map dimensions, with immediate reversal at the edge',()=>{
  const f=fixture();f.viewport.clientWidth=400;f.run('zoom=2.4;mapTransform()');
  f.fire('pointerdown');f.fire('pointermove',{clientX:5000,clientY:5000});
  assert.deepEqual(f.pan(),{x:1540,y:708.8});
  f.fire('pointermove',{clientX:4980,clientY:4980});assert.deepEqual(f.pan(),{x:1520,y:688.8});
});

test('wheel zoom anchors the map point under the cursor and updates the zoom controls',()=>{
  const f=fixture();f.run('pan={x:30,y:-10};mapTransform()');
  const e=f.fire('wheel',{deltaY:-100,deltaMode:0,clientX:620,clientY:280});
  const zoom=f.run('zoom'),p=f.pan();assert.ok(e.defaultPrevented);assert.ok(zoom>1);
  assert.ok(Math.abs((120-p.x)/zoom-90)<1e-9);assert.ok(Math.abs((-50-p.y)/zoom+40)<1e-9);
  assert.equal(f.label.textContent,Math.round(zoom*100)+'%');
  f.run('zoomMap(100)');assert.equal(f.run('zoom'),2.4);assert.equal(f.inside.disabled,true);
  f.run('zoomMap(0)');assert.equal(f.out.disabled,true);assert.ok(f.stage.offsetHeight*f.run('zoom')>=f.viewport.clientHeight);
});

test('wheel delta modes agree, horizontal-only scrolling does not change zoom, and a drag cannot be disrupted by zoom',()=>{
  const a=fixture(),b=fixture();a.fire('wheel',{deltaY:-16,deltaMode:0});b.fire('wheel',{deltaY:-1,deltaMode:1});
  assert.equal(a.run('zoom'),b.run('zoom'));const before=a.run('zoom');
  a.fire('wheel',{deltaY:0,deltaX:100,deltaMode:0});assert.equal(a.run('zoom'),before);
  a.fire('pointerdown');a.fire('wheel',{deltaY:-100,deltaMode:0});assert.equal(a.run('zoom'),before);
});

test('cancel, capture loss and window blur release dragging; secondary inputs cannot hijack it',()=>{
  for(const end of ['pointercancel','lostpointercapture','blur']){
    const f=fixture();f.fire('pointerdown',{button:2});assert.equal(f.captured.size,0);
    f.fire('pointerdown',{isPrimary:false});assert.equal(f.captured.size,0);
    f.fire('pointerdown');f.fire('pointermove',{pointerId:2,clientX:800});assert.deepEqual(f.pan(),{x:0,y:0});
    f.fire('pointercancel',{pointerId:2});assert.ok(f.captured.has(1));
    if(end==='blur')f.windowEvents.get('blur')();else f.fire(end);
    assert.equal(f.captured.size,0);assert.equal(f.classes.size,0);assert.equal(f.run('mapGesture'),null);
  }
});

test('a primary touch pointer pans without depending on mouse button state',()=>{
  const f=fixture();f.fire('pointerdown',{target:f.node,pointerType:'touch'});
  f.fire('pointermove',{pointerType:'touch',buttons:0,clientX:510,clientY:350});
  assert.deepEqual(f.pan(),{x:60,y:20});f.fire('pointerup',{pointerType:'touch'});
  assert.ok(f.fire('click',{pointerType:'touch',target:f.node}).stopped);
});

test('keyboard navigation moves only the focused map and Home restores the selected region',()=>{
  const f=fixture();assert.ok(f.fire('keydown',{key:'ArrowRight'}).defaultPrevented);assert.equal(f.pan().x,-50);
  f.fire('keydown',{key:'ArrowDown',shiftKey:true});assert.equal(f.pan().y,-120);
  f.fire('keydown',{key:'+',target:f.node});assert.equal(f.run('zoom'),1);
  f.fire('keydown',{key:'+'});assert.equal(f.run('zoom'),1.18);
  f.fire('keydown',{key:'Home'});assert.deepEqual(f.pan(),{x:0,y:0});
  assert.equal(f.fire('keydown',{key:'Tab'}).defaultPrevented,undefined);
});

test('responsive resize reclamps the camera without exposing space beyond the map',()=>{
  const f=fixture();f.run('pan={x:10000,y:10000};mapTransform()');
  f.viewport.clientWidth=1600;f.viewport.clientHeight=950;f.windowEvents.get('resize')();
  const zoom=f.run('zoom'),p=f.pan();assert.ok(zoom*f.stage.offsetWidth>=1600);assert.ok(zoom*f.stage.offsetHeight>=950);
  assert.ok(Math.abs(p.x)<=(zoom*f.stage.offsetWidth-1600)/2+.001);
  assert.ok(Math.abs(p.y)<=(zoom*f.stage.offsetHeight-950)/2+.001);
});

test('state polling keeps updating while a pointer is held and only rebuilds after release',async()=>{
  const f=fixture(),state={characters:[],profiles:[],rewards:[],progress:{},job:null};
  f.context.fetch=async()=>({ok:true,json:async()=>state});
  f.run('var renders=0,liveUpdates=0;render=()=>renders++;updateLive=()=>liveUpdates++;');
  f.fire('pointerdown',{target:f.node});await f.run('poll()');
  assert.equal(f.run('renders'),0);assert.equal(f.run('liveUpdates'),1);assert.ok(f.captured.has(1));
  f.fire('pointerup');f.advance(201);await f.run('poll()');assert.equal(f.run('renders'),1);
});

test('a full render restores map keyboard focus without scrolling the page',()=>{
  const f=fixture();let focusOptions;
  const query=f.context.document.querySelector;
  f.context.document.querySelector=s=>s==='#map-viewport'?f.viewport:query(s);
  f.context.document.activeElement={id:'map-viewport',matches(){return false;}};
  f.viewport.focus=options=>{focusOptions=options;};
  f.run('data={characters:[],version:"0.5.0"};explore=()=>"";extrasPanel=()=>{};updateLive=()=>{};render()');
  assert.equal(focusOptions.preventScroll,true);
});

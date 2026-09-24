'use strict';
// AFK FARM share card (0.7.0): draws one delivered expedition's summary from
// /api/share on a canvas and saves it as a PNG. Pages call
// AfkShare.download(claimId); AfkShare.draw(summary, canvas) renders only.
window.AfkShare=(()=>{
  const W=1200,H=630;
  const COLORS={Unholy:'#ff4d6d',Angelic:'#ffd166',Heroic:'#c77dff',Set:'#57cc99',Satanic:'#ff7b3a',Other:'#9aa3ad'};
  const ORDER=['Unholy','Angelic','Heroic','Set','Satanic'];
  const short=n=>{if(n===null||n===undefined||n==='')return '—';n=Number(n);if(!Number.isFinite(n))return '—';const a=Math.abs(n);
    return a>=1e9?(n/1e9).toFixed(1)+'B':a>=1e6?(n/1e6).toFixed(1)+'M':a>=1e4?(n/1e3).toFixed(1)+'K':Math.round(n).toLocaleString('en-US');};
  const hours=h=>{const m=Math.round(Number(h||0)*60);return m<60?`${m} min`:`${Math.floor(m/60)} h${m%60?` ${m%60} min`:''}`;};
  function image(src){return new Promise(done=>{if(!src)return done(null);const i=new Image();i.onload=()=>done(i);i.onerror=()=>done(null);i.src=src;});}
  function fit(ctx,text,max){text=String(text||'');if(ctx.measureText(text).width<=max)return text;
    while(text.length>1&&ctx.measureText(text+'…').width>max)text=text.slice(0,-1);return text+'…';}
  function wrap2(ctx,text,max){
    const words=String(text||'').split(/\s+/).filter(Boolean);let first='';
    while(words.length&&ctx.measureText((first?first+' ':'')+words[0]).width<=max)first=(first?first+' ':'')+words.shift();
    if(!first&&words.length)return [fit(ctx,words.join(' '),max),''];
    return [first,fit(ctx,words.join(' '),max)];
  }
  function lines(summary){
    const hero=summary.hero||{},level=hero.level_before&&hero.level_now&&hero.level_now!==hero.level_before?`Level ${hero.level_before} → ${hero.level_now}`:hero.level_now?`Level ${hero.level_now}`:'';
    return {title:hero.name||'Hero',sub:[level,hero.class_name,summary.region,hours(summary.hours)+(summary.partial?' (partial)':'')].filter(Boolean).join(' · ')};
  }
  async function draw(summary,canvas){
    canvas.width=W;canvas.height=H;const ctx=canvas.getContext('2d');
    const bg=ctx.createLinearGradient(0,0,W,H);bg.addColorStop(0,'#16110c');bg.addColorStop(1,'#2b1c10');ctx.fillStyle=bg;ctx.fillRect(0,0,W,H);
    ctx.strokeStyle='#8a6a3f';ctx.lineWidth=4;ctx.strokeRect(10,10,W-20,H-20);
    ctx.textBaseline='top';ctx.fillStyle='#c9a96e';ctx.font='600 22px Georgia, serif';ctx.fillText('AFK FARM · HERO SIEGE',44,40);
    const when=summary.delivered_at?new Date(summary.delivered_at).toLocaleDateString('en-US',{year:'numeric',month:'short',day:'numeric'}):'';
    ctx.textAlign='right';ctx.fillText(when,W-44,40);ctx.textAlign='left';
    const {title,sub}=lines(summary);
    ctx.fillStyle='#f4e7cf';ctx.font='700 58px Georgia, serif';ctx.fillText(fit(ctx,title,760),44,82);
    ctx.fillStyle='#d8c29a';ctx.font='26px Georgia, serif';ctx.fillText(fit(ctx,sub,1110),44,152);
    if(summary.mode==='siege'&&summary.siege){
      // Waves fought (faced), not waves_held (waves whose demand the hero fully met).
      const s=summary.siege,badge=`SIEGE L${s.level} · ${s.waves_fought} WAVES${s.fell?' · FELL':' · HELD'}${s.record?' · NEW RECORD':''}`;
      ctx.font='700 22px Georgia, serif';const bw=ctx.measureText(badge).width+36;
      ctx.fillStyle='#5a1e1e';ctx.fillRect(W-44-bw,90,bw,44);ctx.fillStyle='#ffd7a8';ctx.fillText(badge,W-44-bw+18,101);
    }
    const stats=[['KILLS',short(summary.kills)],['XP',short(summary.exp)],['GOLD',short(summary.gold)],['ITEMS',short(summary.items)]];
    stats.forEach(([label,value],i)=>{const x=44+i*280;ctx.fillStyle='rgba(255,255,255,.06)';ctx.fillRect(x,206,258,96);
      ctx.fillStyle='#b69a6a';ctx.font='600 18px Georgia, serif';ctx.fillText(label,x+18,220);ctx.fillStyle='#fff3dc';ctx.font='700 40px Georgia, serif';ctx.fillText(value,x+18,248);});
    let x=44;ctx.font='600 20px Georgia, serif';
    for(const r of ORDER){const n=(summary.visible_rarities||{})[r];if(!n)continue;const label=`${r} ${n}`;const w=ctx.measureText(label).width+28;
      ctx.fillStyle='rgba(0,0,0,.35)';ctx.fillRect(x,322,w,36);ctx.fillStyle=COLORS[r];ctx.fillText(label,x+14,330);x+=w+10;}
    const best=(summary.best||[]).slice(0,8),icons=await Promise.all(best.map(b=>image(b.icon)));
    best.forEach((b,i)=>{const bx=44+i*140,by=372,c=COLORS[b.group]||COLORS[b.rarity]||COLORS.Other;
      ctx.fillStyle='rgba(0,0,0,.4)';ctx.fillRect(bx,by,124,124);ctx.strokeStyle=c;ctx.lineWidth=3;ctx.strokeRect(bx,by,124,124);
      if(icons[i]){const k=Math.min(96/icons[i].width,96/icons[i].height,3);ctx.imageSmoothingEnabled=false;
        ctx.drawImage(icons[i],bx+62-icons[i].width*k/2,by+62-icons[i].height*k/2,icons[i].width*k,icons[i].height*k);}
      if(b.count>1){ctx.fillStyle='#fff3dc';ctx.font='700 18px Georgia, serif';ctx.textAlign='right';ctx.fillText('×'+b.count,bx+118,by+100);ctx.textAlign='left';}
      ctx.fillStyle=c;ctx.font='16px Georgia, serif';const [one,two]=wrap2(ctx,b.name,132);ctx.fillText(one,bx,by+130);if(two)ctx.fillText(two,bx,by+149);});
    const notes=[];
    if((summary.wishlist_hits||[]).length)notes.push('★ Wishlist: '+summary.wishlist_hits.map(h=>h.name).join(', '));
    if(summary.new_finds_total)notes.push(`+${summary.new_finds_total} new collection ${summary.new_finds_total===1?'entry':'entries'}`);
    ctx.fillStyle='#ffd166';ctx.font='700 22px Georgia, serif';ctx.fillText(fit(ctx,notes.join('   ·   '),1110),44,556);
    ctx.fillStyle='#8f7a57';ctx.font='17px Georgia, serif';
    ctx.fillText(fit(ctx,`Every item was generated by the game's own drop calls · offline · AFK FARM v${summary.version||''}`,1110),44,594);
    return canvas;
  }
  async function load(id){const response=await fetch('/api/share?id='+encodeURIComponent(id));const body=await response.json();
    if(!response.ok)throw Error(body.error||'Share card unavailable.');return body;}
  async function download(id){
    const summary=await load(id),canvas=await draw(summary,document.createElement('canvas'));
    const blob=await new Promise(done=>canvas.toBlob(done,'image/png'));if(!blob)throw Error('The share card could not be created.');
    const link=document.createElement('a');link.href=URL.createObjectURL(blob);
    link.download=`AFK-FARM-${String(summary.hero?.name||'hero').replace(/[^A-Za-z0-9_-]+/g,'_')}-${String(summary.delivered_at||'').slice(0,10)||'expedition'}.png`;
    document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(link.href),10000);return summary;
  }
  return {draw,load,download,lines,short,hours,wrap2};
})();

(() => {
const $=id=>document.getElementById(id);
const profiles=[['直板手机',1080,2400],['横屏平板',2560,1600],['折叠内屏',2200,2200],['小屏手机',1080,1920]];
let plan=null, active=null, busy=false;
const viewMap=new Map();
function status(s){$('status').textContent=s}
async function api(name,data={}){
 const r=await fetch('/api/'+name,{method:'POST',headers:{'Content-Type':'application/json','X-Preview-Token':PREVIEW_TOKEN},body:JSON.stringify(data)});
 const result=await r.json();if(!r.ok)throw Error(result.error||r.status);return result;
}
function esc(t){return String(t).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function sample(t,T){const a=t.keyframes||[];if(!a.length)return {};let k=T-(t.delay||0);const duration=t.duration||0;if(duration>0)k=t.loop?(k%duration):Math.min(duration,k);if(k<=a[0].t)return a[0];for(let i=1;i<a.length;i++){if(k<=a[i].t){const x=a[i-1],y=a[i],q=Math.max(0,Math.min(1,(k-x.t)/Math.max(0.0001,y.t-x.t)));const out={};for(const key of Object.keys(y)){const u=x[key],v=y[key];out[key]=typeof u==='number'&&typeof v==='number'?u+(v-u)*q:q<1?u:v;}return out;}}return a[a.length-1];}
function createViews(){
 $('devices').replaceChildren();viewMap.clear();
 for(const [name,w,h] of profiles){
  const card=document.createElement('article');card.className='card';
  const heading=document.createElement('h2');heading.textContent=name+' · '+w+'×'+h;
  const meta=document.createElement('div');meta.className='meta';meta.textContent='待导入';
  const wrap=document.createElement('div');wrap.className='canvas-wrap';
  const frame=document.createElement('div');frame.className='phone';
  const screen=document.createElement('div');screen.className='screen';
  frame.append(screen);wrap.append(frame);card.append(heading,meta,wrap);$('devices').append(card);
  const view={name,w,h,frame,screen,wrap,meta,items:[],sx:1,sy:1,pix:1};
  viewMap.set(name,view);
  frame.addEventListener('pointerdown',e=>{if(!plan)return;frame.setPointerCapture(e.pointerId);const p=toPoint(view,e);active={view,start:p,last:p,time:performance.now(),moved:0,drag:false,down:false};});
  frame.addEventListener('pointermove',async e=>{if(!active||active.view!==view)return;const p=toPoint(view,e);active.last=p;active.moved=Math.max(active.moved,Math.hypot(p.x-active.start.x,p.y-active.start.y));if(active.moved>8&&!active.down&&!busy){active.down=true;try{const hit=await api('press',active.start);if(hit.slider){const got=await api('drag',{phase:'start',...active.start,owner:hit.slider});active.drag=!!got.active;}}catch(err){status(err.message);}}});
  frame.addEventListener('pointerup',async e=>{if(!active||active.view!==view)return;const a=active;active=null;const p=toPoint(view,e);if(busy)return;busy=true;try{let result;if(a.drag){await api('drag',{phase:'move',...p});result=await api('drag',{phase:'end',...p});}else{const duration=performance.now()-a.time;const d=Math.hypot(p.x-a.start.x,p.y-a.start.y);const kind=d>12?'swipe':duration>=500?'long-press':'tap';result=await api('gesture',{kind,x:a.start.x,y:a.start.y,toX:p.x,toY:p.y,durationMs:duration});}if(result.plan)render(result.plan);}catch(err){status(err.message)}finally{busy=false;}});
 }
}
function toPoint(v,e){const r=v.frame.getBoundingClientRect();const px=(e.clientX-r.left-7)/v.pix,py=(e.clientY-r.top-7)/v.pix;return {x:px/v.sx,y:py/v.sy};}
function geometry(v){
 if(!plan)return;const src=plan.canvas,method=$('mode').value;
 const f=Math.min((v.wrap.clientWidth-24)/v.w,(v.wrap.clientHeight-24)/v.h);
 v.pix=Math.max(.1,f);v.frame.style.width=v.w*v.pix+14+'px';v.frame.style.height=v.h*v.pix+14+'px';
 let sx=v.w/src.width,sy=v.h/src.height,tx=0,ty=0;
 if(method==='contain'){sx=sy=Math.min(sx,sy);tx=(v.w-src.width*sx)/2;ty=(v.h-src.height*sy)/2;}
 v.sx=sx;v.sy=sy;v.tx=tx;v.ty=ty;
 v.screen.style.width=v.w+'px';v.screen.style.height=v.h+'px';v.screen.style.transform='scale('+v.pix+')';
}
function render(next){
 plan=next;
 for(const v of viewMap.values()){
  geometry(v);v.screen.replaceChildren();v.items=[];
  next.elements.forEach((item,index)=>{
   const r=item.rect;let el;
   if(item.kind==='video'){el=document.createElement('video');el.autoplay=true;el.loop=true;el.muted=true;el.playsInline=true;}
   else if(item.url){el=document.createElement('img');el.src=item.url;}
   else{el=document.createElement('div');if(item.text){el.className='txt';el.textContent=item.text;el.style.color=item.color||'#fff';el.style.fontSize=Math.max(6,item.size||14)*v.sy+'px';}}
   el.style.left=(v.tx+r.x*v.sx)+'px';el.style.top=(v.ty+r.y*v.sy)+'px';
   el.style.width=Math.max(0,r.w*v.sx)+'px';el.style.height=Math.max(0,r.h*v.sy)+'px';
   el.style.zIndex=index;el.style.opacity=item.alpha;v.screen.append(el);
   v.items.push({el,item});
  });
  v.meta.textContent=next.elements.length+' 个可见元素 · '+next.counts.triggers+' 个触发器';
 }
 status(next.name+' · 源画布 '+next.canvas.width+'×'+next.canvas.height+' · '+next.counts.assets+' 个素材');
}
function tick(){
 if(plan){
  const T=plan.timeMs+(performance.now()-(window.__epoch||performance.now()));
  for(const v of viewMap.values())for(const {el,item} of v.items){
   if(!item.tracks?.length)continue;let dx=0,dy=0,dw=0,dh=0,a=1,url=null;
   for(const track of item.tracks){const cur=sample(track,T),base=sample(track,plan.timeMs);
    if(track.kind==='pos'){dx+=(cur.x||0)-(base.x||0);dy+=(cur.y||0)-(base.y||0);}
    if(track.kind==='size'){dw+=(cur.w||0)-(base.w||0);dh+=(cur.h||0)-(base.h||0);}
    if(track.kind==='alpha'&&base.a>0)a*=Math.max(0,cur.a||0)/base.a;
    if(track.kind==='frames')url=cur.url||null;
   }
   el.style.transform='translate('+(dx*v.sx)+'px,'+(dy*v.sy)+'px)';
   el.style.opacity=Math.max(0,Math.min(1,item.alpha*a));
   el.style.width=Math.max(0,(item.rect.w+dw)*v.sx)+'px';
   el.style.height=Math.max(0,(item.rect.h+dh)*v.sy)+'px';
   if(url&&el.tagName==='IMG'&&el.src!==new URL(url,location.href).href)el.src=url;
  }
 }
 requestAnimationFrame(tick);
}
const originalRender=render;render=(p)=>{window.__epoch=performance.now();originalRender(p);};
$('upload').onclick=()=>$('file').click();
async function importFile(f){if(!f)return;status('正在解析 '+f.name);try{const r=await fetch('/api/import',{method:'POST',headers:{'X-Preview-Token':PREVIEW_TOKEN,'X-Filename':encodeURIComponent(f.name)},body:await f.arrayBuffer()});const p=await r.json();if(!r.ok)throw Error(p.error||r.status);render(p);}catch(e){status('导入失败：'+e.message)}}
$('file').onchange=e=>importFile(e.target.files[0]);
$('reset').onclick=async()=>{try{render(await api('reset'))}catch(e){status(e.message)}};
$('mode').onchange=()=>{if(plan)render(plan)};
window.addEventListener('resize',()=>{if(plan)render(plan)});
document.addEventListener('dragover',e=>e.preventDefault());
document.addEventListener('drop',e=>{e.preventDefault();importFile(e.dataTransfer.files[0]);});
createViews();requestAnimationFrame(tick);setInterval(async()=>{if(!plan||busy||active)return;try{render(await api('plan'))}catch{}},1000);
})();
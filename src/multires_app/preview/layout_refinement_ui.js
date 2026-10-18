// Offline JSON exchange stays local. Image matching uses the loopback service.
let refinementProposal=null,referenceFile=null;
function downloadLayoutJSON(value,name){
 const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));
 const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function componentBoxes(){
 const groups=new Map();
 for(const row of layout().rows){const cid=DATA.nodeComponents[DATA.nodes[row[0]].id];if(!cid||row[3]<=0||row[4]<=0)continue;const b={x:row[1],y:row[2],w:row[3],h:row[4]};
  if(!groups.has(cid))groups.set(cid,b);else{const old=groups.get(cid),x=Math.min(old.x,b.x),y=Math.min(old.y,b.y);groups.set(cid,{x,y,w:Math.max(old.x+old.w,b.x+b.w)-x,h:Math.max(old.y+old.h,b.y+b.h)-y});}}
 return [...groups].map(([id,sourceBBox])=>{const c=compMap.get(id),t=layout().transforms[id]||identity;
  return {id,name:c.name,role:c.role,semanticRole:c.semanticRole||null,group:c.layout?.group||null,
   locked:['background','gesture-layer'].includes(c.role),sourceBBox,
   bbox:{x:sourceBBox.x*t.sx+t.tx,y:sourceBBox.y*t.sy+t.ty,w:sourceBBox.w*t.sx,h:sourceBBox.h*t.sy}};});
}
async function localRefinementRequest(){
 const d=device(),components=componentBoxes(),vp=d.viewport;
 const fixed=v=>(Math.floor(v*1000000+.5)/1000000).toFixed(6);
 const lines=['layout-refinement-v1',d.profile.id,stateId,fixed(vp.width)+','+fixed(vp.height)];
 for(const c of [...components].sort((a,b)=>a.id<b.id?-1:a.id>b.id?1:0))lines.push(c.id+'\t'+['x','y','w','h'].map(k=>fixed(c.bbox[k])).join(','));
 if(!crypto.subtle)throw Error('浏览器不支持本地摘要计算，请使用 layout_refinement.py export');
 const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(lines.join('\n')));
 const baseDigest=[...new Uint8Array(bytes)].map(n=>n.toString(16).padStart(2,'0')).join('');
 const insets=d.profile.safeInsetsPx||{},unit=vp.width/d.profile.resolutionPx.width;
 const safeRegion={x:(insets.left||0)*unit,y:(insets.top||0)*unit,w:vp.width-((insets.left||0)+(insets.right||0))*unit,h:vp.height-((insets.top||0)+(insets.bottom||0))*unit};
 return {schemaVersion:1,kind:'layout-refinement-request',target:d.profile.id,state:stateId,viewport:vp,resolutionPx:d.profile.resolutionPx,
  coordinateUnit:'theme-engine-logical',baseDigest,components,constraints:{safeRegion,preserveAspectRatio:true,moveWholeComponents:true,changeGeometryOnly:true},
  instructions:'只返回 layout-refinement-proposal JSON，保留 schemaVersion/target/state/baseDigest；components 仅含需调整项的 id 和 bbox{x,y,w,h}，保持等比、安全区、同组关系，不要输出代码或命令。',
  responseTemplate:{schemaVersion:1,kind:'layout-refinement-proposal',target:d.profile.id,state:stateId,baseDigest,applyScope:'matching-components',components:[]}};
}
async function refinementRequest(){return live?api('refinerequest',{session:liveSession}):localRefinementRequest();}
function showRefinement(){
 $('refinementStatus').textContent=refinementProposal?`待审查 ${refinementProposal.components?.length||0} 项调整`:'未加载布局建议';
 $('applyRefinement').disabled=!refinementProposal;$('downloadProposal').disabled=!refinementProposal;
 if(refinementProposal)$('detail').textContent=JSON.stringify(refinementProposal,null,2);
 draw();
 if(refinementProposal?.target!==device().profile.id||refinementProposal?.state!==stateId)return;
 for(const c of refinementProposal.components||[]){const b=c.bbox;if(!b||!['x','y','w','h'].every(k=>Number.isFinite(b[k])))continue;
  $('screen').append(make('rect',{x:b.x,y:b.y,width:b.w,height:b.h,fill:'none',stroke:'#ffb52b','stroke-width':3,'stroke-dasharray':'10 7','pointer-events':'none'}));}
}
function overlapBox(a,b){return Math.max(0,Math.min(a.x+a.w,b.x+b.w)-Math.max(a.x,b.x))*Math.max(0,Math.min(a.y+a.h,b.y+b.h)-Math.max(a.y,b.y));}
function validateProposal(request,proposal){
 if(proposal.schemaVersion!==1||proposal.kind!=='layout-refinement-proposal'||proposal.target!==request.target||proposal.state!==request.state||proposal.baseDigest!==request.baseDigest)throw Error('建议不属于当前设备/状态，或布局已变化，请重新导出');
 if(!['state','matching-components'].includes(proposal.applyScope||'matching-components'))throw Error('applyScope 无效');
 if(!Array.isArray(proposal.components)||proposal.components.length>512)throw Error('components 格式无效');
 const current=new Map(request.components.map(c=>[c.id,c])),seen=new Set(),result=new Map(),region=request.constraints.safeRegion;
 for(const c of proposal.components){const old=current.get(c.id),b=c.bbox;
  if(!old||old.locked||seen.has(c.id))throw Error('未知、锁定或重复的组件：'+c.id);seen.add(c.id);
  if(!b||Object.keys(b).sort().join(',')!=='h,w,x,y'||!['x','y','w','h'].every(k=>Number.isFinite(b[k]))||b.w<=0||b.h<=0)throw Error('bbox 数值无效');
  if(b.x<region.x-.5||b.y<region.y-.5||b.x+b.w>region.x+region.w+.5||b.y+b.h>region.y+region.h+.5)throw Error('bbox 超出安全区');
  const sx=b.w/old.sourceBBox.w,sy=b.h/old.sourceBBox.h;
  if(Math.abs(sx-sy)>Math.max(1e-5,Math.max(sx,sy)*.005))throw Error('bbox 改变了组件宽高比');
  const scale=Math.min(sx,sy);result.set(c.id,{sx:scale,sy:scale,tx:b.x-old.sourceBBox.x*scale,ty:b.y-old.sourceBBox.y*scale});}
 const groups=new Map();for(const c of current.values())if(c.group){if(!groups.has(c.group))groups.set(c.group,[]);groups.get(c.group).push(c.id);}
 for(const [group,members] of groups){if(!members.some(id=>seen.has(id)))continue;if(!members.every(id=>seen.has(id)))throw Error('同组组件必须一起修改：'+group);const first=result.get(members[0]);if(members.some(id=>Object.keys(first).some(k=>Math.abs(result.get(id)[k]-first[k])>1e-4)))throw Error('同组图层须共用变换');}
 const boxes=[...current.values()].filter(c=>!c.locked);for(let i=0;i<boxes.length;i++)for(let j=0;j<i;j++){const a=boxes[i],b=boxes[j];if(!seen.has(a.id)&&!seen.has(b.id))continue;const aa=proposal.components.find(c=>c.id===a.id)?.bbox||a.bbox,bb=proposal.components.find(c=>c.id===b.id)?.bbox||b.bbox;
  if(overlapBox(aa,bb)>Math.min(aa.w*aa.h,bb.w*bb.h)*.05&&!overlapBox(a.bbox,b.bbox))throw Error('建议产生新的组件重叠');}
 return result;
}
$('exportRefinement').onclick=async()=>{try{downloadLayoutJSON(await refinementRequest(),'layout-refinement-request.json');$('refinementStatus').textContent='请求已导出；将 responseTemplate 结构的建议 JSON 导入预览';}catch(e){$('refinementStatus').textContent=e.message;}};
$('importRefinement').onchange=async e=>{try{const file=e.target.files[0];if(!file)return;if(file.size>1024*1024)throw Error('JSON 不能超过 1MB');const proposal=JSON.parse(await file.text());validateProposal(await refinementRequest(),proposal);refinementProposal=proposal;showRefinement();}catch(error){$('refinementStatus').textContent=error.message;}finally{e.target.value='';}};
$('applyRefinement').onclick=async()=>{if(!refinementProposal||busy)return;busy=true;try{const request=await refinementRequest(),transforms=validateProposal(request,refinementProposal);if(live)acceptLive(await api('refineapply',{session:liveSession,proposal:refinementProposal}));else{Object.assign(layout().transforms,Object.fromEntries(transforms));frames=[];frameIndex=-1;draw();}refinementProposal=null;$('applyRefinement').disabled=true;$('refinementStatus').textContent=live?'已应用到会话中的匹配组件，导出 HWT 将保存修改后的项目':'已应用到当前画面的预览；完整范围写回请使用 layout_refinement.py apply';}catch(e){$('refinementStatus').textContent=e.message;}finally{busy=false;}};
$('downloadProposal').onclick=()=>{if(refinementProposal)downloadLayoutJSON(refinementProposal,'layout-refinement-proposal.json');};
$('referenceImage').onchange=e=>{referenceFile=e.target.files[0]||null;$('matchReference').disabled=!live||!referenceFile;$('refinementStatus').textContent=live?'参考图已选择，点击匹配生成建议':'参考图匹配需本地服务；可用 layout_refinement.py match 离线运行';};
$('matchReference').onclick=async()=>{try{if(!referenceFile||busy)return;if(referenceFile.size>10*1024*1024)throw Error('参考图不能超过 10MB');busy=true;$('refinementStatus').textContent='正在本地匹配参考图…';const image=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(referenceFile);});refinementProposal=await api('match',{session:liveSession,image});showRefinement();}catch(e){$('refinementStatus').textContent=e.message;}finally{busy=false;}};

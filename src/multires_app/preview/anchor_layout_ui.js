// Anchor edits are proposals. The server resolves every reachable scene before accepting.
let anchorDraft=null,anchorDraftTarget=null;
const anchorNames=['top_left','top_center','top_right','center_left','center_center','center_right','bottom_left','bottom_center','bottom_right'];
function anchorGroups(){let l=layout(),seen=new Set();while(l?.adaptationPlan?.reusedFrom&&!seen.has(l.state)){seen.add(l.state);l=device().layouts.find(x=>x.state===l.adaptationPlan.reusedFrom);}return l?.adaptationPlan?.groups||[];}
function anchorConfig(){if(anchorDraftTarget!==device().profile.id){anchorDraft=structuredClone(device().profile.adaptation?.anchorLayout||{});anchorDraftTarget=device().profile.id;}return anchorDraft;}
function anchorSelected(){return anchorGroups().find(g=>g.id===$('anchorGroup').value);}
function showAnchorDetails(){const g=anchorSelected();if(!g)return;$('anchorPoint').value=typeof g.anchor.parent_point==='string'?g.anchor.parent_point:'center_center';$('anchorContainer').value=g.container.type;$('anchorDetail').textContent=JSON.stringify({confidence:g.confidence,anchor:g.anchor,members:g.members,frozenIds:layout().adaptationPlan?.frozenIds,validation:layout().adaptationPlan?.validation},null,2);}
function drawAnchors(surface){
 if(!$('anchorGrid').checked)return;const svg=surface==='source'?$('sourceScreen'):$('screen'),vp=viewport(surface);
 for(const k of [1/3,2/3]){svg.append(make('line',{x1:vp.width*k,y1:0,x2:vp.width*k,y2:vp.height,stroke:'#88aaff','stroke-dasharray':'6 8','pointer-events':'none'}));svg.append(make('line',{x1:0,y1:vp.height*k,x2:vp.width,y2:vp.height*k,stroke:'#88aaff','stroke-dasharray':'6 8','pointer-events':'none'}));}
 for(const g of anchorGroups()){
  const b=surface==='source'?g.sourceBounds:g.targetBounds;if(!b)continue;
  svg.append(make('rect',{x:b.x,y:b.y,width:b.w,height:b.h,fill:'none',stroke:'#e5b5ff','stroke-width':3,'pointer-events':'none'}));
  const name=g.anchor.self_point||g.anchor.parent_point||'center_center',parts=typeof name==='string'?name.split('_'):null;
  const u=Array.isArray(name)?name[0]:parts?.includes('left')?0:parts?.includes('right')?1:.5,v=Array.isArray(name)?name[1]:parts?.includes('top')?0:parts?.includes('bottom')?1:.5;
  svg.append(make('circle',{cx:b.x+u*b.w,cy:b.y+v*b.h,r:8,fill:'#e5b5ff','pointer-events':'none'}));
 }
}
const originalDrawStages=drawStages;drawStages=function(){originalDrawStages();drawAnchors('source');drawAnchors('target');};
const originalDraw=draw;draw=function(){originalDraw();const quality=device().layoutQuality||{},qualityStatus=$('layoutQualityStatus');if(qualityStatus){qualityStatus.textContent=quality.selection==='baseline-rollback'?'此设备的自动锚点候选已回退；当前保留较好的旧候选。组与锚点作为修改建议显示。':quality.selection==='authored-constraints'?'当前采用你的布局规则；请检查各交互状态和软约束提示。':'当前采用锚点与容器布局；自动分组及构图需要确认。';qualityStatus.className=quality.selection==='baseline-rollback'?'warning':'';}
 const previous=$('anchorGroup').value;$('anchorGroup').replaceChildren();for(const g of anchorGroups()){const o=document.createElement('option');o.value=g.id;const names=g.members.map(id=>compMap.get(id)?.name||id.split('/').pop()).slice(0,2).join(' + ');o.textContent=names+' · '+g.container.type+' · 依据分数 '+Math.round(g.confidence*100)+'%';$('anchorGroup').append(o);}if(anchorGroups().some(g=>g.id===previous))$('anchorGroup').value=previous;
 $('anchorMembers').replaceChildren();for(const c of DATA.components.filter(c=>!['background','gesture-layer','decoration','interaction-layer'].includes(c.role))){const o=document.createElement('option');o.value=c.id;o.textContent=c.name;$('anchorMembers').append(o);}showAnchorDetails();};
const anchorLabels=['左上','上中','右上','左中','正中','右中','左下','下中','右下'];for(const [i,name] of anchorNames.entries()){const o=document.createElement('option');o.value=name;o.textContent=anchorLabels[i];$('anchorPoint').append(o);}
$('anchorGroup').onchange=showAnchorDetails;$('anchorGrid').onchange=drawStages;
$('anchorEdit').onclick=()=>{const g=anchorSelected();if(!g)return;const config=anchorConfig(),replacement={id:g.id,members:g.members,container:{...g.container,type:$('anchorContainer').value},anchor:{...g.anchor,parent_point:$('anchorPoint').value,self_point:$('anchorPoint').value,offset:undefined},size_policy:'uniform_scale'};delete replacement.anchor.offset;config.groups=(config.groups||[]).filter(x=>x.id!==g.id);config.groups.push(replacement);$('anchorStatus').textContent='已记录建议。应用时会重新推断该锚点在原画布中的偏移，并求解全部采样场景。';};
$('anchorMerge').onclick=()=>{const ids=[...$('anchorMembers').selectedOptions].map(o=>o.value);if(ids.length<2){$('anchorStatus').textContent='先选择至少两个组件。';return;}const config=anchorConfig();config.groups=(config.groups||[]).filter(g=>!g.members.some(id=>ids.includes(id)));config.groups.push({id:'manual:'+Date.now(),members:ids,container:{type:$('anchorContainer').value},anchor:{parent_point:$('anchorPoint').value,self_point:$('anchorPoint').value}});config.ungroupedIds=(config.ungroupedIds||[]).filter(id=>!ids.includes(id));$('anchorStatus').textContent='合组建议已记录。';};
$('anchorSplit').onclick=()=>{const g=anchorSelected();if(!g)return;const config=anchorConfig();config.groups=(config.groups||[]).filter(x=>x.id!==g.id);config.ungroupedIds=[...new Set([...(config.ungroupedIds||[]),...g.members])];$('anchorStatus').textContent='拆组建议已记录。';};
$('anchorDownload').onclick=()=>downloadLayoutJSON(anchorConfig(),'anchor-config.json');
$('anchorIR').onclick=()=>downloadLayoutJSON(layout().adaptationPlan||{},'anchor-layout-ir.json');
$('anchorApply').onclick=async()=>{if(!live){$('anchorStatus').textContent='离线页面请下载规则，在 relayout 中使用 --anchor-config；实时页面可直接应用。';return;}if(busy)return;busy=true;try{const r=await api('anchorapply',{session:liveSession,configuration:anchorConfig(),reason:'preview designer edit'});device().profile.adaptation={...device().profile.adaptation,anchorLayout:structuredClone(anchorConfig())};acceptLive(r);$('anchorStatus').textContent='全部采样场景求解通过，已应用；导出前请检查各状态。';}catch(e){$('anchorStatus').textContent='建议被拒绝，原布局保留：'+e.message;}finally{busy=false;}};
draw();

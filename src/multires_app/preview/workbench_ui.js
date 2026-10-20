const $=id=>document.getElementById(id),kindLabels={'full-reflow':'整页重排','local-reflow':'局部重排','overlay':'弹窗叠加','reuse':'布局复用'};
const statusLabels={'awaiting-upload':'等待上传','uploading':'上传中','uploaded':'已上传','queued':'排队中','running':'处理中','ready':'可使用','failed':'处理失败','upload-failed':'上传失败'};
let jobs=[],current=null,busy=false,polling=false,proposal=null,matchContext=null,matchSession=null,referenceURL=null,initialSelection=true;
function message(text,error=false){$('message').textContent=text;$('message').classList.toggle('error',error);}
function node(tag,text,attrs={}){const n=document.createElement(tag);n.textContent=text;for(const [k,v] of Object.entries(attrs))n.setAttribute(k,v);return n;}
function active(){return current&&['queued','running','uploading'].includes(current.status);}
function available(){return !!current?.analysis&&!active()&&!busy;}
function controls(){
 for(const id of ['buildStrategy','matchLayout','generateCurrent','activateRevision'])$(id).disabled=!available();
 $('generateMatched').disabled=!available()||!proposal?.components?.length;
 $('downloadProposal').disabled=!proposal;$('uploadTheme').disabled=busy;
 $('retryAnalysis').hidden=!current?.sourceUploaded||current?.revision!=null||!['failed','uploaded'].includes(current?.status);$('retryAnalysis').disabled=busy;
}
async function request(url,body){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-Scene-Token':CONFIG.token},body:JSON.stringify(body)});let value;try{value=await r.json();}catch{throw Error('服务连接中断，请刷新后重试');}if(!r.ok)throw Error(value.error||'操作失败');return value;}
const api=(name,body={})=>request(CONFIG.api+name,body);
function clearMatch(){proposal=null;matchContext=null;$('resolvedImage').removeAttribute('src');$('resolvedImage').hidden=true;$('matchingReport').hidden=true;$('matchingDetails').textContent='';$('step4').classList.remove('done');controls();}
function scope(){return [current?.id,current?.revision,$('target').value,$('scene').value,$('matcher').value,$('confidence').value,$('keepReferenceRelations').checked,$('reuseMatches').checked].join('|');}
function prefix(){return '/'+current.slug+'/scenes/';}
function chooseView(){
 if(!current?.analysis)return;const d=current.analysis.devices.find(d=>d.id===$('target').value);if(!d)return;
 const old=$('scene').value,valid=new Set(d.layouts.map(l=>l.state));$('scene').replaceChildren();
 for(const s of current.analysis.states.filter(s=>valid.has(s.id)))$('scene').append(node('option',s.id+' · '+s.label,{value:s.id}));
 if(valid.has(old))$('scene').value=old;
 const uri=current.analysis.previewURL+'?target='+encodeURIComponent(d.id)+'&state='+encodeURIComponent($('scene').value);
 if($('previewFrame').getAttribute('src')!==uri)$('previewFrame').src=uri;$('openPreview').href=uri;
 $('sceneRows').replaceChildren();for(const l of d.layouts){
  const tr=node('tr','');for(const text of [l.state,kindLabels[l.kind]||l.kind,(l.clusterId||'—')+(l.baseState?' / '+l.baseState:''),l.groupCount+' / '+l.fixedCount,l.centeredGroups+' / '+l.edgeGroups])tr.append(node('td',String(text)));
  tr.onclick=()=>{if(busy)return;$('scene').value=l.state;clearMatch();chooseView();};$('sceneRows').append(tr);
 }
 $('sourceInfo').textContent='源画布：'+current.analysis.sourceProfile.resolutionPx.width+' × '+current.analysis.sourceProfile.resolutionPx.height+'；目标逻辑画布：'+d.viewport.width+' × '+d.viewport.height.toFixed(1);
}
function applyDetail(value){
 if(current?.id===value.id&&Date.parse(value.updated)<Date.parse(current.updated))return;
 const changed=current?.id!==value.id||current?.revision!==value.revision,statusChanged=current?.status!==value.status,errorChanged=current?.error!==value.error;current=value;
 $('projectArea').hidden=!value.analysis;$('empty').hidden=!!value.analysis;
 $('projectTitle').textContent=value.name+' · r'+(value.revision??'—');
 $('taskStatus').textContent=(statusLabels[value.status]||value.status)+(value.task?' · '+({analyze:'场景探索与聚类',strategy:'布局求解',generate:'脚本与主题包生成'}[value.task.operation]||value.task.operation):'');
 $('taskLog').textContent=value.log||value.error||'当前版本已就绪';
 if(value.error&&(changed||statusChanged||errorChanged))message(value.error,true);
 else if(active()&&(changed||statusChanged))message('正在处理 '+value.name+'。可以在任务列表查看其他主题，完成后会更新此页面。');
 else if(changed&&value.analysis)message('已载入 '+value.name+' 的 r'+value.revision+' 版本。选择设备与场景，继续调整策略或匹配参考图。');
 if(changed){
  clearMatch();
  if(value.analysis){
   const a=value.analysis,old=$('target').value;$('target').replaceChildren();
   for(const d of a.devices)$('target').append(node('option',d.name+' / '+d.orientation+' · '+d.resolution.width+'×'+d.resolution.height,{value:d.id}));
   if(a.devices.some(d=>d.id===old))$('target').value=old;
   $('stats').replaceChildren();for(const [label,number] of [['交互场景',a.states.length],['交互跳转',a.transitions],['组件',a.components],['目标方向',a.devices.length]]){const s=node('div','',{class:'stat'});s.append(node('b',String(number)),node('small',label));$('stats').append(s);}
   $('analysisNotes').textContent=JSON.stringify({coverage:a.coverage,limitations:a.limitations,warnings:value.history.flatMap(h=>h.result?.warnings||[])},null,2);
   $('analysisJSON').href=prefix()+'workbench-analysis.json';$('strategyJSON').href=prefix()+'workbench-strategies.json';
   const s=value.config.strategy;$('placement').value=s.placementPolicy;$('scaling').value=s.resourceScalePolicy;$('scaleFactor').value=s.resourceScaleFactor;$('inferRelations').checked=s.inferScreenRelations;$('keepConfirmed').checked=s.keepConfirmed;
   chooseView();
  }
  $('revision').replaceChildren();for(const h of value.history){$('revision').append(node('option','r'+h.revision+' · '+({analyze:'初次分析',strategy:'布局策略',generate:'脚本生成',import:'导入已有项目'}[h.operation]||h.operation),{value:h.revision}));}$('revision').value=value.revision;
  $('downloads').replaceChildren();for(const output of value.delivery?.outputs||[]){
   const group=node('div','');group.append(node('b',output.target+' '));for(const [key,label] of [['script','下载 XML'],['package','下载 HWT'],['patch','脚本补丁'],['resources','资源清单']])if(output[key])group.append(node('a',label,{href:prefix()+output[key],download:''}));$('downloads').append(group);
  }
 }
 for(const [id,done] of [['step1',value.sourceUploaded],['step2',!!value.analysis],['step3',value.history.some(h=>['strategy','import','analyze'].includes(h.operation))],['step4',!!proposal],['step5',!!value.analysis],['step6',!!value.delivery]])$(id).classList.toggle('done',done);
 controls();renderJobs();
}
function renderJobs(){
 $('jobs').replaceChildren();if(!jobs.length){$('jobs').append(node('small','还没有任务，上传主题即可开始。'));return;}
 for(const j of jobs){const b=node('button',j.name,{class:'job'+(current?.id===j.id?' active':'')});b.append(node('small',(statusLabels[j.status]||j.status)+(j.revision!=null?' · r'+j.revision:'')));b.onclick=async()=>{if(busy)return;try{applyDetail(await api('detail',{job:j.id}));}catch(e){message(e.message,true);}};$('jobs').append(b);}
}
async function refresh(){
 if(polling)return;polling=true;try{const list=await api('list');jobs=list.jobs;renderJobs();
  if(!$('presets').children.length)for(const p of list.presets){const label=node('label',p.name),input=node('input','',{type:'checkbox',value:p.id});input.checked=['tablet','dual-fold'].includes(p.id);label.prepend(input);$('presets').append(label);}
  if(initialSelection){initialSelection=false;if(jobs.length&&!current)applyDetail(await api('detail',{job:jobs[0].id}));}
  else if(current){const identity=current.id,result=await api('detail',{job:identity});if(current?.id===identity)applyDetail(result);}
 }catch(e){message(e.message,true);}finally{polling=false;}
}
function strategy(){return {placementPolicy:$('placement').value,resourceScalePolicy:$('scaling').value,resourceScaleFactor:Number($('scaleFactor').value),inferScreenRelations:$('inferRelations').checked,keepConfirmed:$('keepConfirmed').checked};}
async function action(fn){if(busy)return;busy=true;controls();try{await fn();}catch(e){message(e.message,true);}finally{busy=false;controls();}}
function uploadRaw(job,file){return new Promise((resolve,reject)=>{const xhr=new XMLHttpRequest();xhr.open('POST',CONFIG.api+'upload?job='+job);xhr.setRequestHeader('X-Scene-Token',CONFIG.token);xhr.setRequestHeader('Content-Type','application/octet-stream');xhr.upload.onprogress=e=>{if(e.lengthComputable)$('uploadProgress').value=e.loaded/e.total*100;};xhr.onload=()=>{try{const result=JSON.parse(xhr.responseText);if(xhr.status<200||xhr.status>=300)reject(Error(result.error||'上传失败'));else resolve(result);}catch{reject(Error('上传响应无效'));}};xhr.onerror=()=>reject(Error('上传中断，请检查本地服务'));xhr.send(file);});}
$('uploadTheme').onclick=()=>action(async()=>{
 const file=$('themeFile').files[0];if(!file)throw Error('请先选择主题包');if(file.size>100*1024*1024)throw Error('主题包不能超过 100MB');
 const sw=$('sourceWidth').value,sh=$('sourceHeight').value;if(!!sw!==!!sh)throw Error('源宽和源高需要同时填写');
 const body={filename:file.name,targets:[...$('presets').querySelectorAll('input:checked')].map(n=>n.value),orientations:['portrait','landscape'].filter(id=>$(id).checked),
  custom:{width:Number($('customWidth').value),height:Number($('customHeight').value)},sourceDimensions:sw?{width:Number(sw),height:Number(sh)}:null,maxStates:Number($('maxStates').value),maxDepth:Number($('maxDepth').value),strategy:strategy()};
 applyDetail(await api('create',body));$('uploadProgress').hidden=false;$('uploadProgress').value=0;message('正在上传 '+file.name+'…');
 const result=await uploadRaw(current.id,file);applyDetail(await api('detail',{job:result.id}));await refresh();
});
$('retryAnalysis').onclick=()=>action(async()=>{await api('analyze',{job:current.id});applyDetail(await api('detail',{job:current.id}));});
$('buildStrategy').onclick=()=>action(async()=>{await api('strategy',{job:current.id,baseRevision:current.revision,strategy:strategy()});applyDetail(await api('detail',{job:current.id}));});
$('target').onchange=()=>{clearMatch();chooseView();};$('scene').onchange=()=>{clearMatch();chooseView();};
$('referenceFile').onchange=()=>{clearMatch();if(referenceURL)URL.revokeObjectURL(referenceURL);const file=$('referenceFile').files[0];referenceURL=file?URL.createObjectURL(file):null;$('matchResult').hidden=!file;if(file)$('expectedImage').src=referenceURL;};
for(const id of ['matcher','confidence','keepReferenceRelations','reuseMatches'])$(id).onchange=clearMatch;
$('matchLayout').onclick=()=>action(async()=>{
 const file=$('referenceFile').files[0];if(!file)throw Error('请上传目标参考图');if(file.size>10*1024*1024)throw Error('参考图不能超过 10MB');
 const captured=scope(),slug=current.slug,job=current.id,revision=current.revision,target=$('target').value,state=$('scene').value;clearMatch();
 const result=await request('/api/'+slug+'/session',{target,state,replaceSession:matchSession});matchSession=result.session;
 const image=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=()=>reject(Error('图片读取失败'));r.readAsDataURL(file);});
 const start=Date.now(),timer=setInterval(()=>message('正在匹配组件组并求解布局… '+Math.floor((Date.now()-start)/1000)+' 秒'),1000);
 try{
  const value=await request('/api/'+slug+'/matchtarget',{session:matchSession,image,strategy:$('matcher').value,minimumConfidence:Number($('confidence').value),preserveRelations:$('keepReferenceRelations').checked,applyScope:$('reuseMatches').checked?'matching-components':'state'});
  if(captured!==scope()||file!==$('referenceFile').files[0])throw Error('设备、场景、版本或参考图已变化，请重新匹配');
  proposal=value;matchContext={scope:captured,job,revision,target,state};
  const safe=p=>typeof p==='string'&&/^target-matching\/[0-9a-f]{12}\/(?:after\.png|report\.html)$/.test(p),base='/'+slug+'/scenes/';
  if(safe(value.artifacts?.after)){$('resolvedImage').src=base+value.artifacts.after;$('resolvedImage').hidden=false;}
  if(safe(value.artifacts?.report)){$('matchingReport').href=base+value.artifacts.report;$('matchingReport').hidden=false;}
  $('matchingDetails').textContent=JSON.stringify(value.matching,null,2);$('matchResult').hidden=false;
  message('匹配完成：采纳 '+value.matching.acceptedGroups+' 组，'+value.components.length+' 个组件。请审查对比图，再应用生成脚本。');$('step4').classList.add('done');
 }finally{clearInterval(timer);}
});
function downloadJSON(value,name){const u=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'})),a=node('a','',{href:u,download:name});a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);}
$('downloadProposal').onclick=()=>{if(proposal)downloadJSON(proposal,'layout-proposal.json');};
async function generate(useMatch){
 if(useMatch&&(!proposal||matchContext?.scope!==scope()))throw Error('匹配建议已过期，请重新匹配');
 const body={job:current.id,baseRevision:current.revision,targets:$('exportAll').checked?current.analysis.devices.map(d=>d.id):[$('target').value],includePackage:$('exportHWT').checked};
 if(useMatch)body.proposal=proposal;await api('generate',body);applyDetail(await api('detail',{job:current.id}));
}
$('generateCurrent').onclick=()=>action(()=>generate(false));$('generateMatched').onclick=()=>action(()=>generate(true));
$('activateRevision').onclick=()=>action(async()=>{await api('activate',{job:current.id,revision:Number($('revision').value)});applyDetail(await api('detail',{job:current.id}));});
$('reloadPreview').onclick=()=>{if(current?.analysis)$('previewFrame').src=$('openPreview').href;};
$('newTheme').onclick=()=>{if(busy)return;current=null;clearMatch();for(let i=1;i<=6;i++)$('step'+i).classList.remove('done');$('projectArea').hidden=true;$('empty').hidden=false;$('taskStatus').textContent='暂无处理任务';$('taskLog').textContent='等待上传…';$('upload').scrollIntoView({behavior:'smooth'});message('请选择主题包和目标画布。');renderJobs();};
$('refreshJobs').onclick=refresh;refresh();setInterval(refresh,2500);

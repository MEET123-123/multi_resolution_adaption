(()=>{const $=id=>document.getElementById(id);const log=$('log');
function show(v){log.textContent=JSON.stringify(v,null,2).slice(0,50000);}
function state(v){const x=v.page||v;if('running'in x)$('status').textContent=x.running?(x.title+'\n'+x.url+'\n标签数量：'+x.tabs):'浏览器未运行'}
async function api(action,body={}){
 log.textContent='正在执行 '+action+' …';
 try {const r=await fetch('/api/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Browser-Token':CONTROL_TOKEN},body:JSON.stringify(body)});
 const v=await r.json();if(!r.ok)throw Error(v.error||r.status);if(v.image){$('shot').src='data:image/png;base64,'+v.image;$('shot').hidden=false;delete v.image;}
 state(v);show(v);return v;
 }catch(e){show({error:e.message});return null;}
}
function loc(){return {by:$('by').value,role:$('role').value,query:$('query').value,nth:Number($('nth').value||0)}}
$('launch').onclick=()=>api('launch',{url:$('url').value,channel:$('channel').value});
$('navigate').onclick=()=>api('navigate',{url:$('url').value});
$('statusBtn').onclick=()=>api('status');
$('click').onclick=()=>api('click',loc());$('fill').onclick=()=>api('fill',{...loc(),value:$('value').value});
$('select').onclick=()=>api('select',{...loc(),value:$('value').value});$('scroll').onclick=()=>api('scroll',{y:500});
$('inspect').onclick=()=>api('inspect');$('screenshot').onclick=()=>api('screenshot');$('network').onclick=()=>api('network');
$('export').onclick=()=>api('export');$('stop').onclick=()=>api('stop');
$('playbook').onclick=()=>{try{api('playbook',{steps:JSON.parse($('steps').value)});}catch(e){show({error:'JSON 格式错误：'+e.message});}}
})();
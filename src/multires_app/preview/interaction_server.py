"""Loopback interactive runtime: execute concrete gestures, reuse layout templates."""
from __future__ import annotations

import copy
import base64
import json
import math
import mimetypes
import re
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from multires_app.interaction.interaction_semantics import perform_action, replay_human_state, hit_test
from multires_app.pipeline.scene_pipeline import load_scene_algorithm, export_package
from multires_app.pipeline.scene_clusters import schedule_device
from multires_app.pipeline.source_comparison import source_viewport, packed_snapshot
from multires_app.interaction.interaction_history import InteractionHistory, runtime_state, restore_runtime, model_fingerprint, needs_runtime_sync
from multires_app.preview.layout_refinement import export_request, validate_and_apply


class InteractiveSession:
    def __init__(self,project,target,state_id=None):
        self.project=copy.deepcopy({k:v for k,v in project.items() if k!="devices" and not k.startswith("_")})
        device=copy.deepcopy(next(d for d in project["devices"] if d["profile"]["id"]==target))
        self.project["devices"]=[device];self.device=device
        self.model=self.project["model"];self.model["assetNames"]=list(project["assets"])
        if self.project["graph"].get("interactionMode")!="human-gesture-transactions":raise ValueError("请先用新版 scenes 重新生成项目")
        state=next((s for s in project["graph"]["states"] if s["id"]==(state_id or "state-000")),None)
        if state is None:raise ValueError("未知初始场景")
        self.runtime=replay_human_state(self.model,device["viewport"]["width"],device["viewport"]["height"],state,project["assets"],verify_hit=False)
        self.source_viewport=source_viewport(project)
        self.source_runtime=replay_human_state(self.model,self.source_viewport['width'],self.source_viewport['height'],{**state,'trace':state.get('sourceTrace',state['trace'])},project['assets'],verify_hit=True)
        self.process_frames=[];self.comparison={}
        self.source_trace=copy.deepcopy(state.get('sourceTrace',state['trace']))
        self.state_id=state["id"];self.trace=copy.deepcopy(state["trace"])
        self.counter=max([0]+[int(s['id'][5:]) for s in self.project['graph']['states'] if re.fullmatch(r'live-\d+',s['id'])])
        self.algorithm=load_scene_algorithm(project.get("algorithmSpec","reflow"));self.lock=threading.Lock();self.touched=time.monotonic()
        self.journal=InteractionHistory(self.capture_state(),metadata={'modelHash':model_fingerprint(self.model),'target':target,
                                       'viewport':device['viewport'],'sourceViewport':self.source_viewport,
                                       'scope':'offline interpreter; external effects are records only'})

    def capture_state(self):
        return {'target':runtime_state(self.runtime),'source':runtime_state(self.source_runtime),
                'view':{'stateId':self.state_id,'trace':copy.deepcopy(self.trace),'sourceTrace':copy.deepcopy(self.source_trace),
                        'comparison':copy.deepcopy(self.comparison)}}

    def history_summary(self):
        return {'cursor':self.journal.cursor,'eventCount':len(self.journal.records)-1,
                'canUndo':self.journal.parent is not None,'branches':self.journal.children()}

    def load_history(self,document):
        journal=InteractionHistory.from_dict(document)
        for key in ('modelHash','target','viewport','sourceViewport'):
            if journal.metadata.get(key)!=self.journal.metadata.get(key):raise ValueError('交互记录与当前项目不匹配：'+key)
        states={l['state'] for l in self.device['layouts']}
        if any(snapshot['view']['stateId'] not in states for snapshot in journal.checkpoints.values()):
            raise ValueError('请同时载入保存的 project.json；缺少历史布局')
        for node in journal.records:
            for change in node['changes']:
                if change['path']==['view','stateId'] and change['after'].get('value') not in states:
                    raise ValueError('请同时载入保存的 project.json；缺少历史布局')
        self.journal=journal
        return self.checkout(journal.cursor)

    def checkout(self,event_id):
        state=self.journal.checkout(event_id)
        restore_runtime(self.runtime,state['target']);restore_runtime(self.source_runtime,state['source'])
        self.state_id=state['view']['stateId'];self.trace=state['view']['trace'];self.source_trace=state['view']['sourceTrace']
        self.comparison=state['view']['comparison'];self.process_frames=[]
        return self.response()

    def response(self,edge=None):
        self.touched=time.monotonic()
        layout=next(l for l in self.device["layouts"] if l["state"]==self.state_id)
        return {"target":self.device['profile']['id'],"state":{"id":self.state_id,"label":"实时交互 / "+self.state_id},"layout":layout,"edge":edge,
                'source':{'viewport':self.source_viewport,'layout':packed_snapshot(self.project,self.source_runtime,self.state_id),'diagnostics':dict(self.source_runtime.issues)},
                'processFrames':self.process_frames,'comparison':self.comparison,
                "clustering":self.device.get("layoutClusters",{}).get("summary",{}),"diagnostics":dict(self.runtime.issues),
                'history':self.history_summary()}

    def media_preview(self,duration_ms=2400,step_ms=100):
        """Read-only phase samples. Never create scenes or call a layout plugin."""
        duration_ms=int(duration_ms);step_ms=int(step_ms)
        if not 0<duration_ms<=10000 or not 40<=step_ms<=1000 or duration_ms//step_ms>100:
            raise ValueError('媒体采样限制：时长 1–10000ms、步长 40–1000ms、最多 101 帧')
        layout=next(l for l in self.device['layouts'] if l['state']==self.state_id)
        clones=[copy.deepcopy(rt) for rt in (self.source_runtime,self.runtime)]
        for rt in clones:
            # Evaluator.lookup is a bound method; deepcopy preserves the clone.
            for playback in rt.state.get('__animations',{}).values():playback['started']=0
        samples=sorted(set([*range(0,duration_ms+1,step_ms),duration_ms]));frames=[]
        for phase in samples:
            frame={'phase':'媒体采样','timeMs':phase}
            for surface,rt in zip(('source','target'),clones):
                rt.time_ms=phase;rt.system['time']=phase;rt.cache.clear()
                frame[surface]=packed_snapshot(self.project,rt,self.state_id,layout['transforms'] if surface=='target' else None)
            frames.append(frame)
        frames.append({'phase':'settled','timeMs':duration_ms,'source':packed_snapshot(self.project,copy.deepcopy(self.source_runtime),self.state_id),'target':layout})
        # response() evaluates source diagnostics; a preview must not mutate
        # the journaled runtime even through this read-side evaluation.
        saved=[(dict(rt.cache),dict(rt.issues)) for rt in (self.source_runtime,self.runtime)]
        try:result=self.response()
        finally:
            for rt,(cache,issues) in zip((self.source_runtime,self.runtime),saved):rt.cache=cache;rt.issues=issues
        result['processFrames']=frames
        result['mediaPolicy']={'layout':'first-frame-affine','solverCalls':0,'newInteractionStates':0,'sampleFrames':len(samples)}
        return result

    def gesture(self,action):
        # Failed layout plugins must not leave a half-applied user gesture.
        old=(copy.deepcopy(self.runtime.state),copy.deepcopy(self.runtime.system),self.runtime.time_ms,
             self.state_id,copy.deepcopy(self.trace),self.counter,list(self.runtime.events),dict(self.runtime.issues))
        sizes=(len(self.project['graph']['states']),len(self.project['graph']['transitions']),len(self.device['layouts']))
        source_layouts=self.project.setdefault('sourceDevice',{'viewport':self.source_viewport,'layouts':[]})['layouts']
        source_size=len(source_layouts)
        clusters=self.device.get('layoutClusters')
        source_old=(copy.deepcopy(self.source_runtime.state),copy.deepcopy(self.source_runtime.system),self.source_runtime.time_ms,
                    list(self.source_runtime.events),dict(self.source_runtime.issues),self.process_frames,self.comparison,copy.deepcopy(self.source_trace))
        before=self.capture_state()
        self.runtime.command_log=[];self.source_runtime.command_log=[]
        try:
            result=self._gesture(action)
            after=self.capture_state()
            commands=[{'surface':surface,**c} for surface,rt in [('source',self.source_runtime),('target',self.runtime)] for c in rt.command_log]
            event={'type':'gesture','action':action,'sourceTimeline':self.comparison['sourceOutcome'].get('timeline',[]),
                   'targetTimeline':self.comparison['targetOutcome'].get('timeline',[])}
            effects=[{'surface':surface,'delivery':'recorded-only',**e} for surface,rt in [('source',self.source_runtime),('target',self.runtime)] for e in rt.events]
            json.dumps([before,after,commands,event,effects],allow_nan=False)
            if needs_runtime_sync(self.journal.current,before):self.journal.commit({'type':'runtime-sync'},before)
            entry=self.journal.commit(event,after,commands=commands,effects=effects)
            result['edge']['historyEvent']=entry['id'];result['history']=self.history_summary()
            return result
        except Exception:
            self.runtime.state,self.runtime.system,self.runtime.time_ms,self.state_id,self.trace,self.counter,self.runtime.events,self.runtime.issues=old
            self.runtime.cache.clear()
            del self.project['graph']['states'][sizes[0]:];del self.project['graph']['transitions'][sizes[1]:];del self.device['layouts'][sizes[2]:]
            del source_layouts[source_size:]
            self.device['layoutClusters']=clusters
            self.source_runtime.state,self.source_runtime.system,self.source_runtime.time_ms,self.source_runtime.events,self.source_runtime.issues,self.process_frames,self.comparison,self.source_trace=source_old
            self.source_runtime.cache.clear()
            raise
        finally:
            self.runtime.command_log=None;self.source_runtime.command_log=None

    def _gesture(self,action):
        surface=action.get('surface','target')
        if surface not in {'source','target'}:raise ValueError('无效操作画布')
        if self.counter>=500:raise ValueError("当前会话达到 500 次操作，请导出或重置")
        if action.get("kind") not in {"tap","swipe","long-press","double-tap"}:raise ValueError("无效操作类型")
        sample=action.get("sample")
        duration=action.get("durationMs",0)
        if not isinstance(duration,(int,float)) or not math.isfinite(duration) or not 0<=duration<=120000:raise ValueError("无效手势时长")
        if not isinstance(sample,list) or len(sample)!=4 or not all(isinstance(n,(int,float)) and math.isfinite(n) and -2<=n<=3 for n in sample):raise ValueError("无效手势坐标")
        if self.model["nodes"].get(action.get("owner"),{}).get("tag")!="Button":raise ValueError("无效按钮")
        layout=next(l for l in self.device["layouts"] if l["state"]==self.state_id)
        display=[]
        for e in self.runtime.snapshot(self.project["assets"],visible_only=True):
            if e["tag"]!="Button":continue
            cid=self.project["nodeComponents"].get(e["id"]);t=layout["transforms"].get(cid,{"sx":1,"sy":1,"tx":0,"ty":0});r=e["rect"]
            display.append({**e,"rect":{"x":r["x"]*t["sx"]+t["tx"],"y":r["y"]*t["sy"]+t["ty"],"w":r["w"]*t["sx"],"h":r["h"]*t["sy"]}})
        target=next((e for e in display if e["id"]==action["owner"] and e["visible"]),None)
        point=(target['rect']['x']+sample[2]*target['rect']['w'],target['rect']['y']+sample[3]*target['rect']['h']) if target else (-1,-1)
        dx=(sample[0]-sample[2])*target['rect']['w'] if target else 0
        dy=(sample[1]-sample[3])*target['rect']['h'] if target else 0
        direction=('left' if dx<0 else 'right') if abs(dx)>abs(dy) else ('up' if dy<0 else 'down')
        target_hit=bool(target and 0<=point[0]<=self.device['viewport']['width'] and 0<=point[1]<=self.device['viewport']['height'] and hit_test(display,*point,model=self.model,viewport=self.device['viewport'],gesture=direction if action['kind']=='swipe' else None)==action['owner'])
        if surface=='target' and not target_hit:raise ValueError('该位置超出屏幕、按钮不可见或被其他按钮遮挡')
        old={"state":copy.deepcopy(self.runtime.state),"system":copy.deepcopy(self.runtime.system),"time":self.runtime.time_ms,"id":self.state_id,"trace":copy.deepcopy(self.trace)}
        old['source']={'state':copy.deepcopy(self.source_runtime.state),'system':copy.deepcopy(self.source_runtime.system),'time':self.source_runtime.time_ms}
        old['source']['trace']=copy.deepcopy(self.source_trace)
        old['comparison']=self.comparison
        before={'phase':'before','timeMs':0,'target':packed_snapshot(self.project,self.runtime,self.state_id,layout['transforms']),
                'source':packed_snapshot(self.project,self.source_runtime,self.state_id)}
        source_frames=[];target_frames=[]
        def capture(frames,transforms):
            def callback(phase,elapsed,position,runtime):
                point=list(position)
                if transforms:
                    t=transforms.get(self.project['nodeComponents'].get(action['owner']),{'sx':1,'sy':1,'tx':0,'ty':0})
                    point=[point[0]*t['sx']+t['tx'],point[1]*t['sy']+t['ty']]
                frames.append({'phase':phase,'timeMs':elapsed,'point':point,'layout':packed_snapshot(self.project,runtime,self.state_id,transforms)})
            return callback
        self.source_runtime.events=[]
        source_outcome=perform_action(self.source_runtime,action,self.project['assets'],verify_hit=True,on_event=capture(source_frames,{}))
        if surface=='source' and not source_outcome['valid']:raise ValueError('原始直板机不能执行该手势：'+source_outcome['reason'])
        if not source_outcome['valid']:
            source_frames=[];self.source_runtime.state=old['source']['state'];self.source_runtime.system=old['source']['system'];self.source_runtime.time_ms=old['source']['time'];self.source_runtime.cache.clear()
        self.runtime.events=[]
        outcome=perform_action(self.runtime,action,self.project["assets"],verify_hit=False,on_event=capture(target_frames,layout['transforms'])) if target_hit else {'valid':False,'reason':'target-hit-unavailable','executed':[],'timeline':[]}
        if surface=='target' and not outcome["valid"]:raise ValueError("手势无法执行："+outcome["reason"])
        self.counter+=1
        sid=f"live-{self.counter:04d}"
        if outcome['valid']:self.trace.append({"humanAction":action})
        if source_outcome['valid']:self.source_trace.append({'humanAction':action})
        elements=self.runtime.snapshot(self.project["assets"],visible_only=True)
        nodes={uid:i for i,uid in enumerate(self.project["nodeOrder"])};assets={uid:i for i,uid in enumerate(self.project["assetOrder"])}
        rows=[]
        for e in elements:
            if not e["visible"] or e["tag"]=="Group" or e['id'] not in nodes:continue
            r=e["rect"];rows.append([nodes[e["id"]],*[round(r[k],3) for k in ("x","y","w","h")],round(e["alpha"],4),assets.get(e["asset"],-1),e["text"],e["size"],e['asset'],e.get('media')])
        controls={name:self.runtime.lookup(name) for name in set(self.model["controls"])|set(self.project['graph']['semantics'].get('guardVariables',[]))}
        state={"id":sid,"label":"实时 "+action["kind"],"overrides":copy.deepcopy(self.runtime.state),"controlValues":controls,"depth":len(self.trace),"trace":copy.deepcopy(self.trace),"visibleElements":[e["id"] for e in elements if e["visible"]]}
        state['sourceTrace']=copy.deepcopy(self.source_trace)
        self.project["graph"]["states"].append(state)
        edge={"from":self.state_id,"to":sid,"owner":action["owner"],"action":action["kind"],"gesture":action.get("gesture","pointer"),"sample":sample,
              "trigger":outcome["executed"][-1] if outcome['executed'] else None,"triggers":list(dict.fromkeys(outcome["executed"])),"timeline":outcome.get("timeline",[]),"events":copy.deepcopy(self.runtime.events),
              "changes":{k:v for k,v in self.runtime.state.items() if old["state"].get(k)!=v and k!="__animations"},"bounded":False,"witnesses":[{"sample":sample,"gesture":"pointer"}]}
        self.project["graph"]["transitions"].append(edge)
        from multires_app.media.media_layout import reference
        next_layout={"state":sid,"rows":rows,"transforms":{},"controlValues":controls,"overrides":copy.deepcopy(self.runtime.state),'timeMs':self.runtime.time_ms}
        ref=reference(self.runtime,self.project['assets'],self.project['nodeOrder'],self.project['assetOrder'])
        if ref:next_layout['mediaLayout']=ref
        self.device["layouts"].append(next_layout)
        # New human states need the corresponding original-phone stable pose
        # for active-resource classification and source anchor inference.
        self.project['sourceDevice']['layouts'].append(packed_snapshot(self.project,self.source_runtime,sid))
        schedule_device(self.project,self.device,self.algorithm,preserve_existing=True)
        self.state_id=sid
        self.comparison={'surface':surface,'sourceOutcome':source_outcome,'targetOutcome':outcome,
                         'warnings':(["原始直板机："+source_outcome['reason']] if not source_outcome['valid'] else [])+(["目标设备："+outcome['reason']] if not outcome['valid'] else [])}
        self.process_frames=[before]
        for i in range(max(len(source_frames),len(target_frames))):
            sf=source_frames[min(i,len(source_frames)-1)] if source_frames else None;tf=target_frames[min(i,len(target_frames)-1)] if target_frames else None
            active=tf or sf
            self.process_frames.append({'phase':active['phase'],'timeMs':active['timeMs'],'source':sf['layout'] if sf else before['source'],
                                        'target':tf['layout'] if tf else before['target'],'sourcePoint':sf['point'] if sf else None,'targetPoint':tf['point'] if tf else None})
        self.process_frames.append({'phase':'settled','timeMs':self.runtime.time_ms-old['time'],'source':packed_snapshot(self.project,self.source_runtime,sid),
                                    'target':next(l for l in self.device['layouts'] if l['state']==sid)})
        return self.response(edge)

    def back(self):
        # Debugger undo, not an authored page-back action. Never execute effects.
        parent=self.journal.parent
        return self.checkout(parent or self.journal.cursor)

    def select(self,state_id):
        state=next((s for s in self.project["graph"]["states"] if s["id"]==state_id),None)
        if state is None:raise ValueError("未知状态")
        self.runtime=replay_human_state(self.model,self.device["viewport"]["width"],self.device["viewport"]["height"],state,self.project["assets"],verify_hit=False)
        self.source_runtime=replay_human_state(self.model,self.source_viewport['width'],self.source_viewport['height'],{**state,'trace':state.get('sourceTrace',state['trace'])},self.project['assets'],verify_hit=True)
        self.source_trace=copy.deepcopy(state.get('sourceTrace',state['trace']))
        self.process_frames=[];self.comparison={}
        self.state_id=state_id;self.trace=copy.deepcopy(state["trace"])
        self.journal.commit({'type':'scene-select','state':state_id},self.capture_state())
        return self.response()

    def refinement_request(self):
        return export_request(self.project,self.device['profile']['id'],self.state_id)

    def apply_anchor_configuration(self,configuration,reason):
        from multires_app.layout.anchor_tools import apply_configuration
        try:updated=apply_configuration(self.project,self.device['profile']['id'],configuration,reason)
        except ValueError as exc:
            self.project.setdefault('anchorFeedback',[]).append({'target':self.device['profile']['id'],'configuration':copy.deepcopy(configuration),'accepted':False,'reason':str(exc)})
            raise
        self.project=updated;self.device=updated['devices'][0]
        self.algorithm=load_scene_algorithm('anchor');self.process_frames=[]
        return self.response()

    def apply_refinement(self,proposal):
        if proposal.get('state')!=self.state_id or proposal.get('target')!=self.device['profile']['id']:
            raise ValueError('请切换到建议所属的设备与状态后再应用')
        updated,report=validate_and_apply(self.project,proposal)
        proposed=next(l for l in updated['devices'][0]['layouts'] if l['state']==self.state_id)
        index=next(i for i,l in enumerate(self.device['layouts']) if l['state']==self.state_id)
        self.device['layouts'][index]=proposed
        if updated['devices'][0].get('manualComponentAnchors'):
            self.device['manualComponentAnchors']=updated['devices'][0]['manualComponentAnchors']
            self.device['layouts']=updated['devices'][0]['layouts']
        self.process_frames=[]
        return {**self.response(),'refinement':report}

    def save(self,directory):
        directory.mkdir(parents=True,exist_ok=True)
        project=copy.deepcopy(self.project);project["model"].pop("assetNames",None)
        for key in list(project):
            if key.startswith("_"):project.pop(key)
        path=directory/"project.json";path.write_text(json.dumps(project,ensure_ascii=False,separators=(",",":")),encoding="utf-8")
        result=export_package(path,self.device["profile"]["id"],directory/"adapted.hwt")
        (directory/'interaction-history.json').write_text(json.dumps(self.journal.to_dict(),ensure_ascii=False,indent=2),encoding='utf-8')
        return {"target":result["target"],"changedAttributes":result["changedAttributes"],"intrinsicSizingWarnings":len(result["intrinsicSizingWarnings"]),"package":"adapted.hwt",'historyFile':'interaction-history.json'}


def serve_project(project_path,port=8765):
    catalog=project_path.is_dir()
    root=project_path.resolve() if catalog else project_path.resolve().parent
    project=None if catalog else json.loads(project_path.read_text(encoding="utf-8"))
    token=secrets.token_urlsafe(32);sessions={};guard=threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        def send_json(self,status,payload):
            data=json.dumps(payload,ensure_ascii=False,separators=(",",":")).encode()
            self.send_response(status);self.send_header("Content-Type","application/json; charset=utf-8");self.send_header("Cache-Control","no-store");self.send_header("Content-Length",str(len(data)));self.end_headers();self.wfile.write(data)

        def do_GET(self):
            if self.headers.get("Host")!=f"127.0.0.1:{self.server.server_port}":self.send_error(403);return
            name=unquote(urlsplit(self.path).path).lstrip("/") or ("index.html" if catalog else "preview.html")
            artifacts={'preview.html','validation.json','live-validation.json','report.json','layout-clusters.json','components.json','scene-graph.json','README.md','interaction-ir.json','interaction-bindings.json','engine-compatibility.json','scene-analysis.json'}
            pattern=r'(?:[A-Za-z0-9_-]+/scenes/)?(?:'+'|'.join(re.escape(n) for n in artifacts)+r'|assets/.+|scripts/.+|live/.+)'
            allowed=(catalog and name=='index.html') or (bool(re.fullmatch(pattern,name)) if catalog else name in artifacts or name.startswith(("assets/","scripts/","live/")))
            if not allowed:self.send_error(404);return
            path=(root/name).resolve()
            if not path.is_relative_to(root) or not path.is_file():self.send_error(404);return
            data=None;status=200;start=0;end=path.stat().st_size-1;total=end+1
            if self.headers.get('Range') and 'assets/' in name:
                match=re.fullmatch(r'bytes=(\d*)-(\d*)',self.headers['Range'])
                if not match or not any(match.groups()):self.send_error(416);return
                left,right=match.groups()
                if left:start=int(left);end=min(end,int(right)) if right else end
                else:start=max(0,total-int(right))
                if start>end or start>=total:
                    self.send_response(416);self.send_header('Content-Range',f'bytes */{total}');self.end_headers();return
                status=206
            if name.endswith("preview.html"):
                data=path.read_bytes()
                slug=name.split('/')[0] if catalog else None
                config=json.dumps({"token":token,"api":"/api/"+(slug+'/' if slug else '')})
                data=data.replace(b"const DATA=",("const LIVE_CONFIG="+config+";\nconst DATA=").encode(),1)
            asset_cache='assets/' in name and bool(re.search(r'(?:\?|&)v=[0-9a-f]{12}(?:&|$)',self.path))
            self.send_response(status);self.send_header("Content-Type",(mimetypes.guess_type(path.name)[0] or "application/octet-stream")+("; charset=utf-8" if path.suffix in {".html",".xml",".json"} else ""));self.send_header("Content-Length",str(len(data) if data is not None else end-start+1));self.send_header("Cache-Control",'private, max-age=31536000, immutable' if asset_cache else 'no-store');self.send_header('Accept-Ranges','bytes')
            if status==206:self.send_header('Content-Range',f'bytes {start}-{end}/{total}')
            self.end_headers()
            if self.command=='HEAD':return
            if data is not None:self.wfile.write(data)
            else:
                with path.open('rb') as stream:
                    stream.seek(start);remaining=end-start+1
                    while remaining:
                        chunk=stream.read(min(1024*1024,remaining))
                        if not chunk:break
                        try:self.wfile.write(chunk)
                        except (BrokenPipeError,ConnectionResetError):break
                        remaining-=len(chunk)

        def do_HEAD(self):self.do_GET()

        def do_POST(self):
            expected=f"127.0.0.1:{self.server.server_port}"
            if self.headers.get("Host")!=expected or self.headers.get("X-Scene-Token")!=token or self.headers.get("Origin",f"http://{expected}")!=f"http://{expected}":self.send_json(403,{"error":"invalid origin/token"});return
            try:length=int(self.headers.get("Content-Length","0"))
            except ValueError:self.send_json(400,{"error":"invalid content length"});return
            limit=14*1024*1024 if urlsplit(self.path).path.endswith('/match') else 1024*1024
            if length<1 or length>limit:self.send_json(413,{"error":"request too large"});return
            try:
                body=json.loads(self.rfile.read(length));operation=urlsplit(self.path).path
                output_root=root;selected_project=project
                if catalog:
                    match=re.fullmatch(r'/api/([A-Za-z0-9_-]+)/([a-z]+)',operation)
                    if not match:raise ValueError('无效主题路径')
                    slug,operation_name=match.groups();operation='/api/'+operation_name;output_root=root/slug/'scenes'
                    if operation=='/api/session':selected_project=json.loads((output_root/'project.json').read_text(encoding='utf-8'))
                if operation=="/api/session":
                    with guard:
                        expired=[key for key,s in sessions.items() if time.monotonic()-s.touched>1800]
                        for key in expired:sessions.pop(key)
                        if body.get("replaceSession") in sessions:sessions.pop(body["replaceSession"])
                        if len(sessions)>=8:raise ValueError("会话数量达到上限，请稍后重试")
                        session=InteractiveSession(selected_project,body["target"],body.get("state"));session.output_root=output_root;sid=uuid.uuid4().hex;sessions[sid]=session
                    self.send_json(200,{"session":sid,**session.response()});return
                sid=body.get("session");session=sessions.get(sid)
                if session is None:raise ValueError("会话已过期，请重置")
                if session.output_root!=output_root:raise ValueError('会话不属于当前主题')
                with session.lock:
                    if operation=="/api/gesture":result=session.gesture(body["action"])
                    elif operation=="/api/mediapreview":result=session.media_preview(body.get('durationMs',2400),body.get('stepMs',100))
                    elif operation=="/api/back":result=session.back()
                    elif operation=="/api/checkout":result=session.checkout(body['event'])
                    elif operation=="/api/history":result=session.journal.to_dict()
                    elif operation=="/api/select":result=session.select(body["state"])
                    elif operation=="/api/refinerequest":result=session.refinement_request()
                    elif operation=="/api/refineapply":result=session.apply_refinement(body['proposal'])
                    elif operation=="/api/anchorapply":result=session.apply_anchor_configuration(body['configuration'],body.get('reason','designer correction'))
                    elif operation=="/api/match":
                        from multires_app.preview.reference_matching import match_reference
                        data=body.get('image','')
                        if not isinstance(data,str) or not re.fullmatch(r'data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=\r\n]+',data):raise ValueError('参考图格式无效')
                        try:raw=base64.b64decode(data.split(',',1)[1],validate=True)
                        except ValueError:raise ValueError('参考图编码无效')
                        result=match_reference(session.project,session.device['profile']['id'],session.state_id,raw,session.output_root)
                    elif operation=="/api/export":
                        result=session.save(output_root/"live"/sid);result["url"]="live/"+sid+"/adapted.hwt";result['historyUrl']='live/'+sid+'/interaction-history.json'
                    else:self.send_error(404);return
                self.send_json(200,result)
            except (ValueError,KeyError,StopIteration) as exc:self.send_json(400,{"error":str(exc)})
            except Exception as exc:self.send_json(500,{"error":"运行失败："+str(exc)})

    server=ThreadingHTTPServer(("127.0.0.1",port),Handler)
    print(json.dumps({"url":f"http://127.0.0.1:{server.server_port}/","project":str(project_path.resolve())}),flush=True)
    try:server.serve_forever()
    finally:server.server_close()

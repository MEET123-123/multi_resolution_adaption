"""Local live-preview bridge. Long model work never holds the gesture lock."""
import json
import threading
import uuid
from pathlib import Path

from multires_app.reconstruction.workflow import load,prepare,run,file_digest,save
from multires_app.reconstruction.templates import catalog


class ReconstructionJobs:
    def __init__(self,output,config=None):
        self.output=Path(output).resolve()/'reconstruction';self.config=load(config) if config else None
        self.jobs={};self.lock=threading.Lock()

    def inspect(self,path,session=None):
        path=Path(path).resolve()
        if not path.is_file() or path.name!='project.json' or path.stat().st_size>200_000_000:raise ValueError('请选择已分析场景目录的 project.json（最多 200MB）')
        p=load(path)
        if session is not None:
            original=Path(p['source'])
            if session.source.resolve()!=original.resolve() and (not original.is_file() or not session.source.is_file() or file_digest(original)!=file_digest(session.source)):
                raise ValueError('场景项目与当前导入主题不一致，请先导入对应原始主题或返回原主题')
        states=[{'id':s['id'],'label':s.get('label',s['id']),'depth':s.get('depth',0)} for s in p['graph']['states']]
        recommended=None
        if session is not None:
            names=p['graph'].get('semantics',{}).get('behaviorControls',[])
            compatible=[s for s in p['graph']['states'] if names and all(session.runtime.lookup(n)==s.get('controlValues',s.get('overrides',{})).get(n) for n in names)]
            if compatible:recommended=min(compatible,key=lambda s:(s.get('depth',0),s['id']))['id']
        return {'project':str(path),'targets':[d['profile']['id'] for d in p['devices'] if d['viewport']['width']>d['viewport']['height']],
                'states':states,'recommendedState':recommended,'recommendationScope':'按行为变量筛选代表场景，请核对原始图；不将动画帧自动视为新场景',
                'templates':catalog(),'openaiConfigured':self.config is not None}

    def start(self,body,session=None):
        if body.get('provider','mock') not in {'mock','openai'}:raise ValueError('未知模型服务')
        if body.get('provider')=='openai' and not self.config:raise ValueError('启动 live 时请使用 --vlm-config 配置模型服务')
        if body.get('template') and body['template'] not in {t['id'] for t in catalog()}:raise ValueError('未知模板')
        info=self.inspect(body.get('project',''),session)
        if body.get('target') not in info['targets'] or body.get('state') not in {s['id'] for s in info['states']}:raise ValueError('未知目标或场景')
        with self.lock:
            if any(j['status']=='running' for j in self.jobs.values()):raise ValueError('已有重构任务运行中，请等待完成')
            key=uuid.uuid4().hex;job={'id':key,'status':'running','message':'正在准备冻结场景与组件图集'};self.jobs[key]=job
        folder=self.output/key
        def progress(message):
            with self.lock:job['message']=message
        def worker():
            try:
                data=prepare(info['project'],body['target'],body['state'],folder)
                result=run(data['request'],config=self.config if body.get('provider')=='openai' else None,
                           template=body.get('template'),rounds=2,progress=progress)
                result['reportUrl']=self.url(key,folder/'report.html')
                result['sourceImageUrl']=self.url(key,folder/'source.png');result['beforeImageUrl']=self.url(key,folder/'before.png')
                if result.get('selected'):result['selectedImageUrl']=self.url(key,folder/(result['selected']+'.png'))
                with self.lock:job.update(status='done',message='已完成',result=result)
            except Exception as exc:
                with self.lock:job.update(status='failed',message=str(exc)[:700])
        threading.Thread(target=worker,daemon=True,name='layout-reconstruction').start()
        return dict(job)

    def analyse(self,session):
        if session is None:raise ValueError('请先导入主题包')
        with self.lock:
            if any(j['status']=='running' for j in self.jobs.values()):raise ValueError('已有分析/重构任务运行中')
            key=uuid.uuid4().hex;job={'id':key,'kind':'analysis','status':'running','message':'正在分析原始交互与组件（最多 24 个状态）'};self.jobs[key]=job
        source=session.source;width,height=session.width,session.height;filename=session.model['file']
        def worker():
            try:
                from multires_app.pipeline.scene_pipeline import create_project
                root=self.output/key;root.mkdir(parents=True,exist_ok=True)
                save(root/'inputs.json',{'interaction':{'coordinateSpace':'source'}})
                create_project(source,{'resolutionPx':{'width':width,'height':height}},
                    {'profiles':[{'id':'tablet-ai','resolutionPx':{'width':2560,'height':1600},'orientations':['landscape']}]},
                    root/'scenes',max_states=24,max_depth=4,inputs_file=root/'inputs.json',lockscreen_file=filename)
                info=self.inspect(root/'scenes/project.json')
                with self.lock:job.update(status='done',message='分析完成，请选择交互场景',result=info)
            except Exception as exc:
                with self.lock:job.update(status='failed',message=str(exc)[:700])
        threading.Thread(target=worker,daemon=True,name='layout-scene-analysis').start()
        return dict(job)

    def status(self,key):
        with self.lock:
            if key not in self.jobs:raise ValueError('未知重构任务')
            return dict(self.jobs[key])

    def artifact(self,url):
        parts=url.strip('/').split('/',2)
        if len(parts)!=3 or parts[0]!='reconstruction':return None
        key,relative=parts[1:]
        if key not in self.jobs:return None
        base=(self.output/key).resolve();path=(base/relative).resolve()
        if not path.is_relative_to(base) or not path.is_file() or path.suffix.lower() not in {'.png','.jpg','.jpeg','.webp','.gif','.mp4','.webm','.html','.json','.hwt','.xml'}:return None
        return path

    def url(self,key,path):return '/reconstruction/'+key+'/'+Path(path).resolve().relative_to(self.output/key).as_posix()

    def activate(self,key):
        job=self.status(key);result=job.get('result',{})
        if job['status']!='done' or not result.get('readbackPassed') or result.get('status') not in {'compiled','needs-review'}:raise ValueError('候选尚未通过脚本回读，不能载入实时预览')
        return result

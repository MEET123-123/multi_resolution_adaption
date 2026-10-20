"""Persistent local workflow coordinator; theme engines stay in existing modules."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import uuid

from multires_app.paths import PROJECT_ROOT, SOURCE_ROOT, CONFIG_ROOT

ACTIVE={'uploading','queued','running'}


def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()


def save_json(path,value):
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8');temporary.replace(path)


def catalog():
    configured=json.loads((CONFIG_ROOT/'device-profiles.example.json').read_text(encoding='utf8'))['profiles']
    return [{'id':'tablet','name':'平板模拟 · 2560×1600'},
            {'id':'dual-fold','name':'双折叠模拟 · 展开宽度为源宽度×2'},
            *[{'id':p['id'],'name':p['device']+' / '+p['displayState'],'resolution':p['resolutionPx']} for p in configured
              if not p.get('mockResolution')],{'id':'custom','name':'自定义画布'}]


def validate_strategy(value):
    if not isinstance(value,dict) or set(value)-{'placementPolicy','resourceScalePolicy','resourceScaleFactor','inferScreenRelations','keepConfirmed'}:raise ValueError('布局策略字段无效')
    result={'placementPolicy':value.get('placementPolicy','relationships'),'resourceScalePolicy':value.get('resourceScalePolicy','balanced'),
        'resourceScaleFactor':value.get('resourceScaleFactor',1.),'inferScreenRelations':value.get('inferScreenRelations',True),
        'keepConfirmed':value.get('keepConfirmed',True)}
    if result['placementPolicy'] not in {'relationships','pack'} or result['resourceScalePolicy'] not in {'balanced','width','contain','fixed'}:raise ValueError('未知布局策略')
    scale=result['resourceScaleFactor']
    if type(scale) not in (int,float) or not .25<=scale<=2:raise ValueError('缩放系数范围为 0.25..2')
    if any(type(result[k]) is not bool for k in ('inferScreenRelations','keepConfirmed')):raise ValueError('布局开关无效')
    return result


def validate_config(body):
    ids=body.get('targets',['tablet'])
    if not isinstance(ids,list) or not 1<=len(ids)<=4 or any(not isinstance(i,str) for i in ids) or len(set(ids))!=len(ids) or not set(ids)<={p['id'] for p in catalog()}:raise ValueError('请选择 1..4 种目标画布')
    directions=body.get('orientations',['portrait','landscape'])
    if not isinstance(directions,list) or not directions or len(directions)>2 or any(not isinstance(d,str) for d in directions) or len(set(directions))!=len(directions) or not set(directions)<={'portrait','landscape'}:raise ValueError('屏幕方向无效')
    states,depth=body.get('maxStates',24),body.get('maxDepth',4)
    interaction_space=body.get('interactionSpace','source')
    if interaction_space not in {'source','target'}:raise ValueError('交互屏幕分支策略无效')
    if type(states) is not int or not 1<=states<=128 or type(depth) is not int or not 0<=depth<=8:raise ValueError('状态数应为 1..128，交互深度应为 0..8')
    def dimensions(value):
        if not isinstance(value,dict) or set(value)!={'width','height'} or any(type(v) is not int or not 240<=v<=8192 for v in value.values()) or value['width']*value['height']>32_000_000:raise ValueError('画布边长应为 240..8192，最多 3200 万像素')
        return value
    return {'targets':ids,'orientations':directions,'maxStates':states,'maxDepth':depth,'interactionSpace':interaction_space,
        'custom':dimensions(body.get('custom',{'width':2560,'height':1600})),
        'sourceDimensions':dimensions(body['sourceDimensions']) if body.get('sourceDimensions') is not None else None,
        'strategy':validate_strategy(body.get('strategy',{}))}


def target_profiles(config,source):
    configured={p['id']:p for p in json.loads((CONFIG_ROOT/'device-profiles.example.json').read_text(encoding='utf8'))['profiles']}
    result=[]
    for identity in config['targets']:
        if identity=='tablet':p={'id':'tablet','device':'平板模拟','displayState':'tablet','resolutionPx':{'width':2560,'height':1600},'mockResolution':True}
        elif identity=='dual-fold':p={'id':'dual-fold','device':'双折叠模拟（展开宽度×2）','displayState':'unfolded','resolutionPx':{'width':round(source['resolutionPx']['width']*2),'height':round(source['resolutionPx']['height'])},'mockResolution':True}
        elif identity=='custom':p={'id':'custom','device':'自定义画布','displayState':'custom','resolutionPx':config['custom'],'mockResolution':True}
        else:p=copy.deepcopy(configured[identity])
        if max(p['resolutionPx'].values())>8192 or p['resolutionPx']['width']*p['resolutionPx']['height']>32_000_000:raise ValueError('展开画布超过支持的尺寸')
        p['orientations']=config['orientations'];p['baseOrientation']='landscape' if p['resolutionPx']['width']>p['resolutionPx']['height'] else 'portrait'
        p['adaptation']={'anchorLayout':{k:v for k,v in config['strategy'].items() if k!='keepConfirmed'}}
        result.append(p)
    return {'profiles':result}


def clone_scene(source,destination):
    """Metadata uses real copies; only read-only asset cache files share inodes."""
    def ignore(directory,names):return [n for n in names if n in {'assets','live','target-matching','packages'} or '.layout-backup-' in n]
    shutil.copytree(source,destination,ignore=ignore)
    if (source/'assets').is_dir():
        def link_or_copy(a,b):
            try:os.link(a,b)
            except OSError:shutil.copy2(a,b)
            return b
        shutil.copytree(source/'assets',destination/'assets',copy_function=link_or_copy)
    relocate_resource_paths(destination,source,destination)


def relocate_resource_paths(scenes,previous,destination):
    """Rebase compiler artifact pointers, leaving authored XML/expressions intact."""
    previous=Path(previous).resolve();destination=Path(destination).resolve()
    for manifest in scenes.rglob('*.resources.json'):
        data=json.loads(manifest.read_text(encoding='utf8'));changed=False
        for entry in data.get('files',{}).values():
            if not isinstance(entry,dict) or not isinstance(entry.get('file'),str):continue
            path=Path(entry['file'])
            if path.is_absolute() and path.is_relative_to(previous):
                entry['file']=str(destination/path.relative_to(previous));changed=True
        if changed:save_json(manifest,data)


def project_summary(project,slug):
    kinds={'full-reflow':'整页重排','local-reflow':'公共组件固定后的局部重排','overlay':'弹窗叠加','reuse':'复用已有布局'}
    devices=[]
    for d in project['devices']:
        layouts=[]
        by_state={l['state']:l for l in d['layouts']}
        for l in d['layouts']:
            request=l.get('layoutRequest',{});plan=l.get('adaptationPlan',{});plan=plan.get('anchorPlan',plan)
            seen={l['state']};cursor=l
            while not plan.get('groups'):
                base=plan.get('reusedFrom') or cursor.get('layoutRequest',{}).get('baseState')
                if not base or base in seen:break
                seen.add(base);cursor=by_state.get(base,{});plan=cursor.get('adaptationPlan',{});plan=plan.get('anchorPlan',plan)
            layouts.append({'state':l['state'],'kind':request.get('kind','full-reflow'),'clusterId':request.get('clusterId'),
                'baseState':request.get('baseState'),'groupCount':len(plan.get('groups',[])),
                'fixedCount':len(plan.get('frozenIds',[])),
                'strategy':kinds.get(request.get('kind'),'锚点约束求解'),
                'requiredOK':plan.get('validation',{}).get('required_ok'),
                'centeredGroups':sum(bool(g.get('centerAxes')) for g in plan.get('groups',[])),
                'edgeGroups':sum(bool(g.get('screenAttachment')) for g in plan.get('groups',[]))})
        devices.append({'id':d['profile']['id'],'name':d['profile']['device'],'orientation':d['profile'].get('orientation'),
            'resolution':d['profile']['resolutionPx'],'viewport':d['viewport'],
            'clusters':[{k:c.get(k) for k in ('id','kind','states','representative')} for c in d.get('layoutClusters',{}).get('clusters',[])],
            'summary':d.get('layoutClusters',{}).get('summary',{}),'layouts':layouts})
    return {'slug':slug,'previewURL':'/'+slug+'/scenes/preview.html','sourceProfile':project['sourceProfile'],
        'components':len(project['components']),'states':[{'id':s['id'],'label':s.get('label',s['id']),'depth':s.get('depth',0)} for s in project['graph']['states']],
        'transitions':len(project['graph'].get('transitions',[])),'coverage':project['graph'].get('coverage',{}),
        'devices':devices,'limitations':project.get('limitations',[])}


class Workbench:
    def __init__(self,root,timeout=900,algorithm_spec='anchor'):
        self.root=Path(root).resolve();self.jobs=self.root/'jobs';self.projects=self.root/'projects'
        self.jobs.mkdir(parents=True,exist_ok=True);self.projects.mkdir(exist_ok=True)
        if not self.jobs.resolve().is_relative_to(self.root) or not self.projects.resolve().is_relative_to(self.root):raise ValueError('工作区子目录不能指向工作区外部')
        self.instance_lock=(self.root/'workbench.lock').open('a+b')
        self.instance_lock.seek(0,2)
        if self.instance_lock.tell()==0:self.instance_lock.write(b'1');self.instance_lock.flush()
        self.instance_lock.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.instance_lock.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.instance_lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.instance_lock.close();raise ValueError('此工作区已有工作台实例运行，请使用另一个 --root')
        self.lock=threading.RLock();self.pool=ThreadPoolExecutor(max_workers=1);self.timeout=timeout;self.processes=set();self.algorithm_spec=algorithm_spec
        for path in self.jobs.glob('*/job.json'):
            record=json.loads(path.read_text(encoding='utf8'))
            if record['status'] in ACTIVE:
                record.update(status='failed',error='服务已重启；已有版本保留，可重试任务');save_json(path,record)

    def job(self,identity):
        if not isinstance(identity,str) or not re.fullmatch('[0-9a-f]{32}',identity):raise ValueError('任务 ID 无效')
        path=self.jobs/identity/'job.json'
        if not path.is_file():raise ValueError('任务不存在')
        return json.loads(path.read_text(encoding='utf8'))

    def save(self,record):
        record['updated']=now();save_json(self.jobs/record['id']/'job.json',record)

    def list(self):
        with self.lock:
            return sorted([json.loads(p.read_text(encoding='utf8')) for p in self.jobs.glob('*/job.json')],key=lambda j:j['created'],reverse=True)

    def create(self,body):
        filename=body.get('filename','')
        if not isinstance(filename,str) or len(filename)>200 or any(c in filename for c in '/\\:\x00') or not filename.lower().endswith(('.hwt','.zip')):raise ValueError('请选择 HWT / ZIP 主题文件')
        config=validate_config(body)
        with self.lock:
            if len(self.list())>=100:raise ValueError('工作区已达 100 个任务，请另建工作区或归档旧任务')
            identity=uuid.uuid4().hex;directory=self.jobs/identity;directory.mkdir()
            record={'id':identity,'name':filename,'created':now(),'updated':now(),'status':'awaiting-upload','config':config,
                'revision':None,'nextRevision':0,'slug':None,'history':[],'sourceUploaded':False,'task':None,'error':None}
            self.save(record);return record

    def upload(self,identity,stream,length):
        if not 0<length<=100*1024*1024:raise ValueError('主题文件应为 1 字节至 100MB')
        with self.lock:
            record=self.job(identity)
            if record['status'] not in {'awaiting-upload','upload-failed'}:raise ValueError('该任务已上传，请新建任务')
            record.update(status='uploading',error=None);self.save(record)
        temporary=self.jobs/identity/'source.part'
        try:
            with temporary.open('wb') as out:
                remaining=length
                while remaining:
                    chunk=stream.read(min(1024*1024,remaining))
                    if not chunk:raise ValueError('主题上传中断，请重新上传')
                    out.write(chunk);remaining-=len(chunk)
            with temporary.open('rb') as file:
                if file.read(4) not in {b'PK\x03\x04',b'PK\x05\x06'}:raise ValueError('上传文件不是 ZIP/HWT 主题容器')
            temporary.replace(self.jobs/identity/'source.hwt')
            with self.lock:
                record=self.job(identity);record.update(sourceUploaded=True,status='uploaded');self.save(record)
            return self.submit(identity,'analyze',{})
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            with self.lock:
                record=self.job(identity);record.update(status='upload-failed',error=str(exc));self.save(record)
            raise

    def submit(self,identity,operation,payload):
        if operation not in {'analyze','strategy','generate'}:raise ValueError('未知工作台操作')
        with self.lock:
            record=self.job(identity)
            if record['status'] in ACTIVE:raise ValueError('此主题已有任务正在执行')
            if sum(j['status'] in {'queued','running'} for j in self.list())>=4:raise ValueError('队列已满，请等待当前任务完成')
            if operation=='analyze':
                if not record['sourceUploaded'] or record['revision'] is not None:raise ValueError('此任务不能重新分析；已有版本可重新生成布局策略')
            elif record['revision'] is None or payload.get('baseRevision')!=record['revision']:raise ValueError('项目版本已改变，请刷新后重试')
            if operation=='strategy':payload={**payload,'strategy':validate_strategy(payload.get('strategy',{}))}
            if operation=='generate' and (not isinstance(payload.get('targets'),list) or not 1<=len(payload['targets'])<=16 or any(not isinstance(t,str) for t in payload['targets']) or len(set(payload['targets']))!=len(payload['targets']) or type(payload.get('includePackage',True)) is not bool):raise ValueError('导出设备列表或包选项无效')
            revision=record['nextRevision'];taskid=uuid.uuid4().hex;directory=self.jobs/identity/taskid;directory.mkdir()
            task={'id':taskid,'operation':operation,'revision':revision,'previousSlug':record['slug'],'config':record['config'],'payload':payload,'algorithmSpec':self.algorithm_spec}
            save_json(directory/'task.json',task)
            record.update(status='queued',error=None,nextRevision=revision+1,task={'id':taskid,'operation':operation,'status':'queued','started':now()})
            self.save(record);self.pool.submit(self._run,identity,taskid);return record

    def _run(self,identity,taskid):
        directory=self.jobs/identity/taskid;task=json.loads((directory/'task.json').read_text(encoding='utf8'));resultfile=directory/'result.json'
        try:
            with self.lock:
                record=self.job(identity);record['status']='running';record['task']['status']='running';self.save(record)
            env={**os.environ,'PYTHONPATH':str(SOURCE_ROOT)+os.pathsep+os.environ.get('PYTHONPATH','')}
            temp=directory/'tmp';temp.mkdir();env['TEMP']=str(temp);env['TMP']=str(temp);env['PYTHONIOENCODING']='utf-8'
            command=[sys.executable,'-m','multires_app.preview.workbench_worker','--workspace',str(self.root),'--job',identity,'--task',taskid]
            with (directory/'worker.log').open('wb') as log:
                process=subprocess.Popen(command,cwd=PROJECT_ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                with self.lock:self.processes.add(process)
                try:
                    try:code=process.wait(timeout=self.timeout)
                    except subprocess.TimeoutExpired:
                        process.kill();process.wait();raise ValueError('任务超时；旧版本保留，可降低场景数后创建新任务')
                finally:
                    with self.lock:self.processes.discard(process)
            if code or not resultfile.is_file():raise ValueError('后台处理失败，请查看任务日志')
            result=json.loads(resultfile.read_text(encoding='utf8'))
            if result.get('error'):raise ValueError(result['error'])
            slug=identity+'-r'+str(task['revision']);source=(directory/'output').resolve();destination=(self.projects/slug).resolve()
            # Verify both absolute directory move endpoints remain in this workspace.
            if not source.is_relative_to(self.root) or not destination.is_relative_to(self.root) or not source.is_relative_to(directory.resolve()) or not destination.is_relative_to(self.projects.resolve()) or destination.exists():raise ValueError('版本发布路径无效或已存在')
            source.replace(destination)
            with self.lock:
                record=self.job(identity);record.update(status='ready',revision=task['revision'],slug=slug,error=None)
                if task['operation']=='strategy':record['config']['strategy']=task['payload']['strategy']
                record['task']['status']='succeeded';record['history'].append({'revision':task['revision'],'slug':slug,'operation':task['operation'],'created':now(),'result':result,'config':copy.deepcopy(record['config'])})
                self.save(record)
        except Exception as exc:
            with self.lock:
                record=self.job(identity);record.update(status='failed',error=str(exc)[:800]);record['task']['status']='failed';self.save(record)

    def detail(self,identity):
        with self.lock:record=self.job(identity)
        if record['slug']:
            path=self.projects/record['slug']/'scenes'
            project=json.loads((path/'project.json').read_text(encoding='utf8'));record['analysis']=project_summary(project,record['slug'])
            if (path/'delivery.json').is_file():record['delivery']=json.loads((path/'delivery.json').read_text(encoding='utf8'))
        if record.get('task'):
            log=self.jobs/identity/record['task']['id']/'worker.log'
            if log.is_file():
                with log.open('rb') as stream:stream.seek(max(0,log.stat().st_size-6000));record['log']=stream.read().decode('utf8',errors='replace')
        return record

    def activate(self,identity,revision):
        with self.lock:
            record=self.job(identity)
            if record['status'] in ACTIVE:raise ValueError('正在处理时不能切换版本')
            entry=next((h for h in record['history'] if h['revision']==revision),None)
            if entry is None:raise ValueError('版本不存在')
            record.update(revision=revision,slug=entry['slug'],status='ready',error=None)
            if entry.get('config'):record['config']=copy.deepcopy(entry['config'])
            self.save(record)
            return record

    def import_project(self,path):
        path=Path(path).resolve();project=json.loads(path.read_text(encoding='utf8'));source=Path(project['source'])
        # Reopening the same cache is idempotent.
        existing=next((j for j in self.list() if j.get('importedFrom')==str(path)),None)
        if existing:
            relocate_resource_paths(self.projects/existing['slug']/'scenes',path.parent,self.projects/existing['slug']/'scenes')
            return existing
        if not source.is_file():raise ValueError('已有项目的原始主题包不存在：'+str(source))
        record=self.create({'filename':source.name});identity=record['id'];slug=identity+'-r0'
        shutil.copy2(source,self.jobs/identity/'source.hwt');destination=self.projects/slug/'scenes';destination.parent.mkdir()
        clone_scene(path.parent,destination);project['source']=str(self.jobs/identity/'source.hwt');project['model']['source']=project['source']
        save_json(destination/'project.json',project)
        (destination/'delivery.json').unlink(missing_ok=True)
        summary=project_summary(project,slug);save_json(destination/'workbench-analysis.json',summary)
        save_json(destination/'workbench-strategies.json',{'source':'imported cached scene analysis','devices':[{'id':d['id'],'layouts':d['layouts'],'summary':d['summary']} for d in summary['devices']]})
        record.update(status='ready',sourceUploaded=True,revision=0,nextRevision=1,slug=slug,importedFrom=str(path),
            history=[{'revision':0,'slug':slug,'operation':'import','created':now(),'result':{'imported':True}}])
        self.save(record);return record

    def dispatch(self,operation,body):
        if operation=='list':return {'jobs':self.list(),'presets':catalog()}
        if operation=='create':return self.create(body)
        if operation=='detail':return self.detail(body.get('job'))
        if operation=='activate':return self.activate(body.get('job'),body.get('revision'))
        return self.submit(body.get('job'),operation,body)

    def close(self):
        with self.lock:
            for process in self.processes:
                if process.poll() is None:process.terminate()
        self.pool.shutdown(wait=True,cancel_futures=True)
        self.instance_lock.close()

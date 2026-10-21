"""Finite queue + process timeout + immutable revision publication."""
import json
import os
import subprocess
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


class JobManager:
    def __init__(self,store,workers=1,timeout=240,worker_script=None,recover=True):
        if not 1<=workers<=4 or not 1<=timeout<=600:raise ValueError('invalid worker limits')
        self.store=store;self.timeout=timeout;self.worker_script=worker_script
        if recover:store.recover_interrupted()
        self.pool=ThreadPoolExecutor(max_workers=workers,thread_name_prefix='theme-worker')

    def submit(self,client,job,operation,payload=None):
        if operation=='apply' and not isinstance(payload,dict):raise ValueError('layout proposal must be a JSON object')
        if operation=='match' and not isinstance(payload,bytes):raise ValueError('reference image must contain bytes')
        data=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode() if isinstance(payload,dict) else payload
        task=self.store.enqueue(client,job,operation)
        root=self.store.job_root(client,job)
        try:
            if payload is not None:
                directory=root/'payloads';directory.mkdir(exist_ok=True)
                filename=task['id']+('.json' if operation=='apply' else '.image')
                (directory/filename).write_bytes(data);task['payload']=filename
            self.pool.submit(self._run,client,task)
        except Exception:
            self.store.finish_task(task,error='task could not be queued; previous revision retained');raise
        return task

    def _run(self,client,task):
        root=self.store.job_root(client,task['job']);result_path=root/(task['id']+'.result.json')
        from multires_app.paths import PROJECT_ROOT, SOURCE_ROOT
        command=([sys.executable,str(self.worker_script)] if self.worker_script else
                 [sys.executable,'-m','multires_app.service.saas_worker'])+['--root',str(root),'--operation',task['operation'],
                 '--revision',str(task['revision']),'--previous',str(task['previousRevision']),'--result',str(result_path)]
        if task.get('payload'):command+=['--payload',task['payload']]
        try:
            self.store.start_task(task)
            with (root/(task['id']+'.private.log')).open('wb') as log:
                environment={**os.environ,'PYTHONPATH':str(SOURCE_ROOT)+os.pathsep+os.environ.get('PYTHONPATH','')}
                process=subprocess.Popen(command,cwd=PROJECT_ROOT,env=environment,stdout=log,stderr=subprocess.STDOUT,
                                         creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                try:code=process.wait(timeout=self.timeout)
                except subprocess.TimeoutExpired:
                    process.kill();process.wait(timeout=10)
                    self.store.finish_task(task,error='worker timeout; previous revision retained');return
            if code!=0 or not result_path.is_file():
                self.store.finish_task(task,error='worker failed; previous revision retained');return
            result=json.loads(result_path.read_text(encoding='utf-8'))
            if result.get('workerError'):self.store.finish_task(task,error=result['workerError'])
            else:self.store.finish_task(task,result=result)
        except Exception as exc:
            # Keep the traceback next to the worker log; the task record only
            # carries a short reason because clients read it.
            try:
                with (root/(task['id']+'.private.log')).open('a',encoding='utf-8') as log:
                    log.write('\n[job-manager] '+traceback.format_exc())
            except OSError:
                pass
            self.store.finish_task(task,error=f'worker execution failed: {type(exc).__name__}: {exc}; previous revision retained')

    def close(self):self.pool.shutdown(wait=True)

import asyncio
import io
import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from multires_app.service.saas_store import ServiceStore
from multires_app.service.saas_jobs import JobManager
from multires_app.service.saas_api import SaaSApp

ROOT=Path(__file__).resolve().parents[1]


class FakeManager:
    def __init__(self,store):self.store=store
    def submit(self,client,job,operation,payload=None):return self.store.enqueue(client,job,operation)
    def close(self):pass


async def request(app,path,method='GET',key=None,body=b'',query=b'',host='localhost'):
    headers=[(b'host',host.encode())]
    if key:headers.append((b'authorization',('Bearer '+key).encode()))
    messages=[{'type':'http.request','body':body,'more_body':False}];result=[]
    async def receive():return messages.pop(0)
    async def send(message):result.append(message)
    await app({'type':'http','path':path,'method':method,'headers':headers,'query_string':query},receive,send)
    return result[0]['status'],b''.join(m.get('body',b'') for m in result[1:]),dict(result[0]['headers'])


class SaaSServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'tests');self.root=Path(self.tmp.name)
        self.store=ServiceStore(self.root/'private');self.a=self.store.create_client('A');self.b=self.store.create_client('B')
    def tearDown(self):self.tmp.cleanup()
    def job(self,client=None):return self.store.new_job((client or self.a)['id'],{'width':2560,'height':1600,'mode':'auto'})[0]

    def test_client_key_hash_ownership_idempotency_and_revocation(self):
        self.assertEqual(self.store.authenticate(self.a['apiKey']),self.a['id'])
        self.assertNotIn(self.a['apiKey'].encode(),self.store.database.read_bytes())
        config={'width':2560,'height':1600,'mode':'auto'}
        job,created=self.store.new_job(self.a['id'],config,'request-1');self.assertTrue(created)
        self.assertEqual(self.store.new_job(self.a['id'],config,'request-1'),(job,False))
        with self.assertRaises(ValueError):self.store.new_job(self.a['id'],{**config,'width':2000},'request-1')
        with self.assertRaises(KeyError):self.store.job(self.b['id'],job)
        self.store.revoke_client(self.a['id']);self.assertIsNone(self.store.authenticate(self.a['apiKey']))

    def test_revision_publication_failure_and_restart_preserve_last_result(self):
        job=self.job();first=self.store.enqueue(self.a['id'],job,'convert');self.store.finish_task(first,result={'nativeDeviceTest':False})
        edit=self.store.enqueue(self.a['id'],job,'apply')
        self.assertEqual(self.store.job(self.a['id'],job)['active_revision'],0)
        self.store.finish_task(edit,error='rejected');self.assertEqual(self.store.job(self.a['id'],job)['active_revision'],0)
        edit=self.store.enqueue(self.a['id'],job,'apply');self.store.finish_task(edit,result={'edited':True})
        self.assertEqual(self.store.job(self.a['id'],job)['active_revision'],2)
        self.store.activate_revision(self.a['id'],job,0);self.assertEqual(self.store.job(self.a['id'],job)['active_revision'],0)
        pending=self.store.enqueue(self.a['id'],job,'apply');self.store.recover_interrupted()
        self.assertEqual(self.store.task(self.a['id'],pending['id'])['status'],'failed')
        self.assertEqual(self.store.job(self.a['id'],job)['status'],'succeeded')

    def test_queue_quota_is_enforced(self):
        store=ServiceStore(self.root/'quota',max_active_per_client=1);client=store.create_client('one')
        store.new_job(client['id'],{})
        with self.assertRaises(ValueError):store.new_job(client['id'],{})

    def test_asgi_auth_owned_jobs_preview_scope_and_expiry(self):
        app=SaaSApp(self.store,FakeManager(self.store))
        self.assertEqual(asyncio.run(request(app,'/v1/jobs'))[0],401)
        self.assertEqual(asyncio.run(request(app,'/health',host='evil.example'))[0],400)
        status,data,_=asyncio.run(request(app,'/v1/jobs','POST',self.a['apiKey'],b'placeholder',b'width=2560&height=1600'))
        self.assertEqual(status,202);job=json.loads(data)['job']
        self.assertEqual(asyncio.run(request(app,'/v1/jobs/'+job,key=self.b['apiKey']))[0],404)
        task=json.loads(data)['task'];self.assertEqual(asyncio.run(request(app,'/v1/tasks/'+task,key=self.b['apiKey']))[0],404)
        root=self.store.job_root(self.a['id'],job);path=root/'revisions/0/project/preview.html';path.parent.mkdir(parents=True);path.write_text('<script>const ok=1;</script>',encoding='utf-8')
        ticket=app.ticket(self.a['id'],job,0)
        status,_,headers=asyncio.run(request(app,'/v1/previews/'+ticket+'/project/preview.html'))
        self.assertEqual(status,200);self.assertIn(b'sha256-',headers[b'content-security-policy'])
        analysis=path.parent/'interaction-ir.json';analysis.write_text('{"schemaVersion":1}',encoding='utf-8')
        self.assertEqual(asyncio.run(request(app,'/v1/previews/'+ticket+'/project/interaction-ir.json'))[0],200)
        self.assertEqual(asyncio.run(request(app,'/v1/jobs/'+job+'/files/project/interaction-ir.json',key=self.b['apiKey']))[0],404)
        self.assertEqual(asyncio.run(request(app,'/v1/previews/'+ticket+'/../config.json'))[0],404)
        expired=app.ticket(self.a['id'],job,0,expires=int(time.time()-1))
        self.assertEqual(asyncio.run(request(app,'/v1/previews/'+expired+'/project/preview.html'))[0],401)
        self.store.revoke_client(self.a['id'])
        self.assertEqual(asyncio.run(request(app,'/v1/previews/'+ticket+'/project/preview.html'))[0],401)

    def test_worker_timeout_is_bounded_and_does_not_publish_revision(self):
        job=self.job();script=self.root/'slow.py';script.write_text('import time\ntime.sleep(10)\n',encoding='utf-8')
        manager=JobManager(self.store,timeout=1,worker_script=script,recover=False)
        task=manager.submit(self.a['id'],job,'convert');manager.close()
        self.assertEqual(self.store.task(self.a['id'],task['id'])['status'],'failed')
        self.assertIn('timeout',self.store.job(self.a['id'],job)['error'])

    def test_real_conversion_subprocess_builds_artifacts_and_refinement_request(self):
        job=self.job();root=self.store.job_root(self.a['id'],job)
        (root/'config.json').write_text(json.dumps({'width':2560,'height':1600,'mode':'auto'}),encoding='utf-8')
        with zipfile.ZipFile(root/'source.hwt','w') as archive:
            for path in (ROOT/'samples/scene-theme').rglob('*'):
                if path.is_file():archive.write(path,path.relative_to(ROOT/'samples/scene-theme').as_posix())
        manager=JobManager(self.store,timeout=30,recover=False);task=manager.submit(self.a['id'],job,'convert');manager.close()
        self.assertEqual(self.store.task(self.a['id'],task['id'])['status'],'succeeded',self.store.job(self.a['id'],job)['error'])
        app=SaaSApp(self.store,FakeManager(self.store))
        status,data,_=asyncio.run(request(app,'/v1/jobs/'+job+'/layout-request',key=self.a['apiKey']))
        self.assertEqual(status,200);self.assertEqual(json.loads(data)['kind'],'layout-refinement-request')
        self.assertTrue((root/'revisions/0/adapted.hwt').is_file())


if __name__=='__main__':unittest.main()

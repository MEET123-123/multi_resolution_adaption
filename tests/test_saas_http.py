import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError

from PIL import Image
from multires_app.service.saas_store import ServiceStore

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(importlib.util.find_spec('uvicorn'),'install requirements-service.txt for real HTTP coverage')
class SaaSHTTPTests(unittest.TestCase):
    def test_customer_upload_edit_match_download_and_rollback_over_http(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as tmp:
            storage=Path(tmp)/'private';store=ServiceStore(storage);client=store.create_client('HTTP test');other=store.create_client('Other')
            environment={**os.environ,'MULTIRES_SERVICE_ROOT':str(storage),'PYTHONIOENCODING':'utf-8'}
            process=subprocess.Popen([sys.executable,'-m','uvicorn','multires_app.service.saas_api:create_app','--factory','--host','127.0.0.1','--port','0','--no-access-log'],
                                     cwd=ROOT,env=environment,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,encoding='utf-8')
            try:
                port=None
                for _ in range(12):
                    line=process.stderr.readline()
                    match=re.search(r'http://127\.0\.0\.1:(\d+)',line)
                    if match:port=match[1];break
                self.assertIsNotNone(port,'server failed to start')
                url='http://127.0.0.1:'+port
                def call(path,method='GET',body=None,key=client['apiKey']):
                    headers={'Authorization':'Bearer '+key} if key else {}
                    req=Request(url+path,data=body,method=method,headers=headers)
                    with urlopen(req,timeout=15) as response:
                        raw=response.read();content=response.headers.get('Content-Type','')
                        return json.loads(raw) if 'application/json' in content else raw
                def poll(task,expect_failure=False):
                    for _ in range(100):
                        result=call('/v1/tasks/'+task)
                        if result['status']=='failed':
                            if expect_failure:return result
                            self.fail(result['error'])
                        if result['status']=='succeeded':return result['result']
                        time.sleep(.2)
                    self.fail('worker deadline exceeded')
                self.assertIn('主题适配工作台',call('/',key=None).decode())
                data=io.BytesIO()
                with zipfile.ZipFile(data,'w') as archive:
                    for path in (ROOT/'samples/scene-theme').rglob('*'):
                        if path.is_file():archive.write(path,path.relative_to(ROOT/'samples/scene-theme').as_posix())
                    for name,color in [('red','red'),('blue','blue')]:
                        bitmap=io.BytesIO();Image.new('RGB',(32,32),color).save(bitmap,format='PNG')
                        archive.writestr('unlock/lockscreen/'+name+'.png',bitmap.getvalue())
                queued=call('/v1/jobs?width=2560&height=1600','POST',data.getvalue());job=queued['job'];poll(queued['task'])
                request=call('/v1/jobs/'+job+'/layout-request');proposal=request['responseTemplate']
                clock=next(c for c in request['components'] if c['role']=='header')
                proposal['components']=[{'id':clock['id'],'bbox':{**clock['bbox'],'x':clock['bbox']['x']+5}}]
                edit=call('/v1/jobs/'+job+'/layout-proposal','POST',json.dumps(proposal).encode());poll(edit['task'])
                self.assertEqual(call('/v1/jobs/'+job)['active_revision'],1)
                readiness=call('/v1/jobs/'+job+'/files/readiness.json')
                self.assertFalse(readiness['productionReady']);self.assertFalse(readiness['projectChecks']['nativeDeviceEvidence'])
                self.assertTrue(zipfile.is_zipfile(io.BytesIO(call('/v1/jobs/'+job+'/files/adapted.hwt'))))
                preview=call('/v1/jobs/'+job+'/preview');self.assertIn(b'const DATA=',call(preview['url'],key=None))
                self.assertTrue(call(preview['url'].replace('preview.html','assets/unlock/lockscreen/red.png'),key=None).startswith(b'\x89PNG'))
                stale={**proposal,'baseDigest':'outdated'}
                invalid=call('/v1/jobs/'+job+'/layout-proposal','POST',json.dumps(stale).encode())
                self.assertEqual(poll(invalid['task'],expect_failure=True)['status'],'failed')
                self.assertEqual(call('/v1/jobs/'+job)['active_revision'],1)
                self.assertTrue(zipfile.is_zipfile(io.BytesIO(call('/v1/jobs/'+job+'/files/adapted.hwt'))))
                image=io.BytesIO();Image.new('RGB',(2560,1600),'#25465a').save(image,format='PNG')
                match=call('/v1/jobs/'+job+'/reference-image','POST',image.getvalue());result=poll(match['task'])
                self.assertEqual(result['origin'],'local-reference-image');self.assertEqual(call('/v1/jobs/'+job)['active_revision'],1)
                with self.assertRaises(HTTPError) as error:call('/v1/jobs/'+job,key=other['apiKey'])
                self.assertEqual(error.exception.code,404)
                call('/v1/jobs/'+job+'/revisions/0/activate','POST',b'')
                self.assertEqual(call('/v1/jobs/'+job)['active_revision'],0)
            finally:
                process.terminate();process.wait(timeout=15);process.stderr.close()


if __name__=='__main__':unittest.main()

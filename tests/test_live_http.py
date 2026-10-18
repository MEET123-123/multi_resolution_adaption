import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from multires_app.pipeline.scene_pipeline import create_project

ROOT=Path(__file__).resolve().parents[1]


class LiveHTTPTests(unittest.TestCase):
    def test_catalog_opens_each_theme_as_live_preview(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as tmp:
            root=Path(tmp);out=root/'mock'/'scenes'
            create_project(ROOT/'samples/scene-theme',{'resolutionPx':{'width':1080,'height':2160}},
                           {'profiles':[{'id':'phone','device':'mock','resolutionPx':{'width':1080,'height':2160},'orientations':['portrait']}]},out,max_states=8,max_depth=4)
            (out/'assets').mkdir(exist_ok=True);(out/'assets/clip.mp4').write_bytes(b'0123456789abcdef')
            (root/'index.html').write_text('<a href="mock/scenes/preview.html">mock</a>',encoding='utf-8')
            process=subprocess.Popen([sys.executable,str(ROOT/'multires.py'),'serve',str(root),'--port','0'],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
            try:
                url=json.loads(process.stdout.readline())['url']
                with urlopen(url,timeout=5) as response:self.assertIn(b'mock',response.read())
                with urlopen(url+'mock/scenes/preview.html',timeout=5) as response:page=response.read().decode()
                with urlopen(Request(url+'mock/scenes/assets/clip.mp4',headers={'Range':'bytes=5-9'}),timeout=5) as response:
                    self.assertEqual(response.status,206);self.assertEqual(response.read(),b'56789');self.assertEqual(response.headers['Content-Range'],'bytes 5-9/16')
                config=json.loads(re.search(r'const LIVE_CONFIG=(.*?);',page).group(1))
                self.assertEqual(config['api'],'/api/mock/')
                request=Request(url+'api/mock/session',data=json.dumps({'target':'phone-portrait'}).encode(),headers={'Content-Type':'application/json','X-Scene-Token':config['token']})
                with urlopen(request,timeout=5) as response:self.assertEqual(json.load(response)['state']['id'],'state-000')
            finally:
                process.terminate();process.wait(timeout=10);process.stdout.close()

    def test_http_live_session_and_token_validation(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as tmp:
            out=Path(tmp)
            create_project(ROOT/'samples/scene-theme',{'resolutionPx':{'width':1080,'height':2160}},
                           {'profiles':[{'id':'phone','device':'mock','resolutionPx':{'width':1080,'height':2160},'orientations':['portrait']}]},out,max_states=8,max_depth=4)
            process=subprocess.Popen([sys.executable,str(ROOT/'multires.py'),'serve',str(out/'project.json'),'--port','0'],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
            try:
                info=json.loads(process.stdout.readline());url=info['url']
                with urlopen(url,timeout=5) as response:page=response.read().decode()
                config=json.loads(re.search(r'const LIVE_CONFIG=(.*?);',page).group(1))
                def post(operation,body,token=config['token']):
                    request=Request(url+'api/'+operation,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','X-Scene-Token':token})
                    with urlopen(request,timeout=10) as response:return json.load(response)
                with self.assertRaises(HTTPError) as error:post('session',{'target':'phone-portrait'},'wrong')
                self.assertEqual(error.exception.code,403)
                result=post('session',{'target':'phone-portrait'});sid=result['session']
                sampled=post('mediapreview',{'session':sid,'durationMs':400,'stepMs':100})
                self.assertEqual(sampled['mediaPolicy']['sampleFrames'],5)
                self.assertEqual(sampled['mediaPolicy']['solverCalls'],0)
                self.assertEqual(sampled['state']['id'],result['state']['id'])
                self.assertEqual(sampled['history'],result['history'])
                project=json.loads((out/'project.json').read_text(encoding='utf-8'))
                anchored=post('anchorapply',{'session':sid,'configuration':{},'reason':'HTTP anchor regression'})
                self.assertEqual(anchored['layout']['adaptationPlan']['policy'],'nine-grid-anchor-kiwi')
                request=post('refinerequest',{'session':sid})
                header=next(c for c in request['components'] if c['name']=='header')
                with self.assertRaises(HTTPError) as anchor_error:
                    post('anchorapply',{'session':sid,'configuration':{'constraints':[
                        {'type':'position','targets':[header['id']],'x':0},
                        {'type':'position','targets':[header['id']],'x':1}]}})
                self.assertEqual(anchor_error.exception.code,400)
                self.assertEqual(post('refinerequest',{'session':sid})['baseDigest'],request['baseDigest'])
                proposal=request['responseTemplate'];proposal['components']=[{'id':header['id'],'bbox':{**header['bbox'],'x':header['bbox']['x']+10}}]
                refined=post('refineapply',{'session':sid,'proposal':proposal})
                anchor=refined['layout']['transforms'][header['id']]
                action=next(e for e in project['graph']['transitions'] if e['action']=='tap' and e['changes'].get('count')==1)
                payload={'session':sid,'action':{'owner':action['owner'],'kind':'tap','sample':action['sample']}}
                for _ in range(3):result=post('gesture',payload)
                self.assertEqual(result['layout']['controlValues']['count'],3)
                self.assertEqual(result['layout']['transforms'][header['id']],anchor)
                result=post('back',{'session':sid});self.assertEqual(result['layout']['controlValues']['count'],2)
                history=post('history',{'session':sid})
                self.assertEqual(history['cursor'],result['history']['cursor'])
                self.assertEqual(len(history['records']),4)
                first=history['records'][1]['id']
                result=post('checkout',{'session':sid,'event':first})
                self.assertEqual(result['layout']['controlValues']['count'],1)
                result=post('gesture',payload)
                result=post('checkout',{'session':sid,'event':first})
                self.assertEqual(len(result['history']['branches']),2)
                payload['action']['durationMs']=-1
                with self.assertRaises(HTTPError) as error:post('gesture',payload)
                self.assertEqual(error.exception.code,400)
            finally:
                process.terminate();process.wait(timeout=10);process.stdout.close()


if __name__=='__main__':unittest.main()

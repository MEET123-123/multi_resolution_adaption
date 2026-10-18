import json
import struct
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from multires_app.engine.scene_engine import load_model,extract_assets,Runtime
from multires_app.pipeline.scene_pipeline import create_project
from multires_app.preview.interaction_server import InteractiveSession
from multires_app.media.media_support import mp4_size

ROOT=Path(__file__).resolve().parents[1]


class MediaComparisonTests(unittest.TestCase):
    def test_bitmap_clock_video_and_encoded_resource_paths(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as tmp:
            root=Path(tmp);source=root/'source';lock=source/'unlock/lockscreen';lock.mkdir(parents=True)
            (lock/'manifest.xml').write_text('<Lockscreen screenWidth="1080"><Time x="100" y="100" src="time.png" space="2"/><Video src="movie.mp4"/><Image src="a#b.png"/></Lockscreen>',encoding='utf-8')
            for char in ['0','1','4','9','dot']:Image.new('RGBA',(10,20),(255,255,255,255)).save(lock/f'time_{char}.png')
            Image.new('RGB',(12,13)).save(lock/'a#b.png')
            payload=b'\0'*40+struct.pack('>II',1080*65536,2160*65536)
            tkhd=struct.pack('>I4s',len(payload)+8,b'tkhd')+payload
            trak=struct.pack('>I4s',len(tkhd)+8,b'trak')+tkhd
            movie=struct.pack('>I4s',len(trak)+8,b'moov')+trak;(lock/'movie.mp4').write_bytes(movie)
            self.assertEqual(mp4_size(movie),(1080,2160))
            model=load_model(source);assets=extract_assets(source,model,root/'out')
            self.assertEqual(assets['unlock/lockscreen/movie.mp4']['kind'],'video')
            self.assertIn('%23',assets['unlock/lockscreen/a#b.png']['url'])
            rt=Runtime(model,1080,2160);snapshot=rt.snapshot(assets)
            clock=[e for e in snapshot if e['tag']=='Time']
            self.assertEqual(len(clock),5);self.assertTrue(all(e['asset'] in assets for e in clock))
            self.assertEqual(next(e for e in snapshot if e['tag']=='Video')['rect']['w'],1080)

    def test_source_and_target_have_independent_gesture_frames_and_restore(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as tmp:
            out=Path(tmp);source={'resolutionPx':{'width':1080,'height':2160}}
            profiles={'profiles':[{'id':'tablet','resolutionPx':{'width':2560,'height':1600},'orientations':['landscape']}]}
            create_project(ROOT/'samples/scene-theme',source,profiles,out,max_states=8,max_depth=4)
            p=json.loads((out/'project.json').read_text(encoding='utf-8'));session=InteractiveSession(p,'tablet-landscape')
            self.assertEqual(p['sourceDevice']['viewport']['height'],2160)
            self.assertEqual(session.device['viewport']['height'],675)
            owner=next(n['id'] for n in p['model']['nodes'].values() if n['attributes'].get('name')=='switch')
            response=session.gesture({'owner':owner,'kind':'tap','sample':[.5]*4,'surface':'source'})
            self.assertTrue(response['comparison']['sourceOutcome']['valid']);self.assertTrue(response['comparison']['targetOutcome']['valid'])
            down=next(f for f in response['processFrames'] if f['phase']=='down')
            self.assertEqual(down['source']['controlValues']['pressed'],1)
            self.assertEqual(down['target']['controlValues']['pressed'],1)
            self.assertEqual(response['source']['layout']['controlValues']['pressed'],0)
            self.assertEqual(response['processFrames'][-1]['phase'],'settled')
            session.back();self.assertEqual(session.source_runtime.lookup('count'),0);self.assertEqual(session.runtime.lookup('count'),0)

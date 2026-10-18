"""Test the shipped pure graph selector with Node; no browser is driven."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

JS=Path(__file__).resolve().parents[1]/'src/multires_app/preview/scene_viewer.js'


@unittest.skipUnless(shutil.which('node'),'Node required for preview graph tests')
class PreviewTransitionTests(unittest.TestCase):
    def choose(self,edges,gesture):
        text=JS.read_text(encoding='utf-8')
        function=text[text.index('function chooseSampledTransition('):text.index('async function executeHuman(')]
        code=function+'\nprocess.stdout.write(JSON.stringify(chooseSampledTransition('+json.dumps(edges)+',"opened","drawer","swipe",'+json.dumps(gesture)+',[.1,.5,.9,.5])));'
        result=subprocess.run([shutil.which('node'),'-e',code],check=True,capture_output=True,text=True)
        return json.loads(result.stdout)

    def test_unsampled_direction_cannot_render_other_scene(self):
        edge={'from':'opened','to':'closed','owner':'drawer','action':'swipe','gesture':'right','sample':[.9,.5,.1,.5]}
        for direction in ('left','up','down'):self.assertIsNone(self.choose([edge],direction))
        self.assertEqual(self.choose([edge],'right')['to'],'closed')

    def test_collapsed_witness_direction_is_kept(self):
        edge={'from':'opened','to':'opened','owner':'drawer','action':'swipe','gesture':'right',
              'witnesses':[{'gesture':'right','sample':[.9,.5,.1,.5]},{'gesture':'up','sample':[.5,.1,.5,.9]}]}
        self.assertEqual(self.choose([edge],'up')['to'],'opened')
        self.assertIsNone(self.choose([edge],'left'))

    def test_other_scene_and_button_cannot_supply_transition(self):
        edge={'from':'opened','to':'closed','owner':'drawer','action':'swipe','gesture':'right','sample':[.9,.5,.1,.5]}
        self.assertIsNone(self.choose([{**edge,'from':'other'},{**edge,'owner':'background'}],'right'))


if __name__=='__main__':unittest.main()

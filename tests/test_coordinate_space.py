import copy
import json
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from multires_app.engine.scene_engine import load_model,Runtime
from multires_app.engine.coordinate_space import replace_references,adapt_xml_inputs
from multires_app.export.layout_writeback import patch_script
from multires_app.export.interaction_preservation import audit_preservation


class CoordinateSpaceTests(unittest.TestCase):
    xml=b'''<Lockscreen screenWidth="1080"><Var name="h" expression="#screen_height"/>
    <Var name="page" expression="0"/>
    <Group visibility="eq(#page,0)"><Rectangle name="tall" x="0" y="0" w="100" h="100" visibility="gt(#h,1920)"/>
    <Rectangle name="short" x="0" y="0" w="100" h="100" visibility="le(#h,1920)"/></Group>
    <Group visibility="eq(#page,1)"><Rectangle name="second" x="10" y="#h-200" w="100" h="100"/></Group>
    <Button x="0" y="0" w="#screen_width" h="#h"><Trigger action="up">
    <VariableCommand name="page" expression="1-#page" condition="lt(#touch_y,#screen_height)"/>
    </Trigger></Button></Lockscreen>'''

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent);self.folder=Path(self.tmp.name)
        lock=self.folder/'lockscreen';lock.mkdir();(lock/'manifest.xml').write_bytes(self.xml)
        self.model=load_model(self.folder)
        self.model.update(layoutSourceViewport={'width':1080.,'height':2340.},inputs={'interaction':{'coordinateSpace':'source'}})

    def tearDown(self):self.tmp.cleanup()

    def names(self,rt):return [e['name'] for e in rt.snapshot({},visible_only=True) if e['tag']=='Rectangle']

    def test_target_aspect_does_not_change_scene_branch(self):
        for width,height in [(1080,675),(1200,800),(800,1200)]:
            rt=Runtime(self.model,width,height);self.assertEqual(self.names(rt),['tall'])
            rt.state['page']=1;rt.cache.clear();self.assertEqual(self.names(rt),['second'])
            rt.state['page']=0;rt.cache.clear();self.assertEqual(self.names(rt),['tall'])
        self.model['inputs']['interaction']['coordinateSpace']='target'
        self.assertEqual(self.names(Runtime(self.model,1080,675)),['short'])

    def test_native_writeback_preserves_conditions_and_rejects_mutations(self):
        root=ET.fromstring(self.xml);original=[(n,dict(n.attrib),list(n)) for n in root.iter()]
        adapter=adapt_xml_inputs(root,self.model)
        patched,report=patch_script(self.xml,original,root,coordinate_space=adapter)
        self.assertTrue(audit_preservation(self.xml,patched,report)['passed'])
        (self.folder/'lockscreen/manifest.xml').write_bytes(patched)
        native=load_model(self.folder)
        self.assertEqual(self.names(Runtime(native,1080,675)),['tall'])
        for corrupt in [patched.replace(b'1920',b'1900'),patched.replace(b'2340.0',b'675.0'),patched.replace(b'1-#page',b'1')]:
            with self.assertRaises(ValueError):audit_preservation(self.xml,corrupt,report)

    def test_literals_and_similar_names_are_not_rewritten(self):
        raw="ifelse(#screen_height>1920,'#screen_height.png',#screen_height_extra)"
        self.assertEqual(replace_references(raw,{'screen_height':'mr_a_height'}),
                         "ifelse(#mr_a_height>1920,'#screen_height.png',#screen_height_extra)")

    def test_source_canvas_must_be_explicit_and_valid(self):
        self.model['layoutSourceViewport']['height']=float('nan')
        with self.assertRaises(ValueError):Runtime(self.model,1080,675)

    def test_pipeline_live_preview_and_native_export_share_source_branches(self):
        from multires_app.pipeline.scene_pipeline import create_project,export_script
        from multires_app.preview.interaction_server import InteractiveSession
        from multires_app.layout.scene_membership import scene_membership_errors
        inputs=self.folder/'inputs.json';inputs.write_text(json.dumps(self.model['inputs']),encoding='utf-8')
        output=self.folder/'out'
        create_project(self.folder,{'resolutionPx':{'width':1080,'height':2340}},
                       {'profiles':[{'id':'tablet','resolutionPx':{'width':2560,'height':1600},'orientations':['landscape']}]},
                       output,max_states=3,max_depth=2,inputs_file=inputs)
        project=json.loads((output/'project.json').read_text(encoding='utf-8'));device=project['devices'][0]
        session=InteractiveSession(project,device['profile']['id'])
        self.assertEqual(self.names(session.runtime),['tall'])
        button=next(uid for uid,n in project['model']['nodes'].items() if n['tag']=='Button')
        for names in (['second'],['tall']):
            result=session.gesture({'owner':button,'kind':'tap','sample':[.5,.5,.5,.5]})
            self.assertEqual(self.names(session.runtime),names)
            self.assertFalse(scene_membership_errors(session.project,session.device,result['layout']))
        native_dir=self.folder/'native';script=native_dir/'lockscreen/manifest.xml'
        export_script(project,device['profile']['id'],script)
        native=load_model(native_dir)
        for layout in device['layouts']:
            rt=Runtime(native,1080,675,copy.deepcopy(layout['overrides']),layout['timeMs'])
            self.assertFalse(scene_membership_errors(project,device,layout,rt.snapshot({},visible_only=True)))

    def test_membership_guard_detects_foreign_scene_and_offscreen_leak(self):
        from multires_app.layout.scene_membership import scene_membership_errors
        names={n['attributes']['name']:uid for uid,n in self.model['nodes'].items() if n['attributes'].get('name')}
        order=[names['tall'],names['short']]
        source={'state':'s','rows':[[0,1100,100,100,100,1,-1,'',0]]}
        p={'model':self.model,'nodeOrder':order,'nodeComponents':{order[0]:'c'},
           'sourceDevice':{'viewport':{'width':1080,'height':2340},'layouts':[source]}}
        layout={**copy.deepcopy(source),'transforms':{}}
        device={'viewport':{'width':1080,'height':675}}
        self.assertFalse(scene_membership_errors(p,device,layout))
        layout['transforms']={'c':{'sx':1,'sy':1,'tx':-500,'ty':0}}
        errors=scene_membership_errors(p,device,layout)
        self.assertEqual(errors,[{'kind':'onscreen-node','node':order[0],'expected':0,'actual':1}])
        layout['transforms']={};layout['rows'].append([1,100,100,100,100,1,-1,'',0])
        self.assertTrue(any(e['kind']=='visible-node' and e['node']==order[1] for e in scene_membership_errors(p,device,layout)))


if __name__=='__main__':unittest.main()

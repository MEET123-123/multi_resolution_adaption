import json
import copy
import tempfile
import unittest
from pathlib import Path
from multires_app.layout.footer_components import split_footer_components
from multires_app.layout.anchor_layout import AnchorThemeLayout
from multires_app.pipeline.scene_clusters import transformed_box
from tests.test_anchor_layout import comp,ctx,SOURCE


def fixture():
    nodes={'g':{'tag':'Group','children':['left','right','bl','br'],'attributes':{},'parent':None}}
    for uid,x in [('left',79),('right',861)]:nodes[uid]={'tag':'Image','children':[],'attributes':{'x':str(x),'y':'#screen_height-66-140'},'parent':'g'}
    for uid in ['bl','br']:nodes[uid]={'tag':'Button','children':[],'attributes':{},'parent':'g'}
    members=['left','right','bl','br'];order=list(nodes)
    rows=[[order.index(uid),79 if uid in ('left','bl') else 861,2194,140,140,1,-1,'',0] for uid in members]
    c={**comp('left',(79,2194,922,140),role='content',roots=members,members=members),'name':'clock / bottom section',
       'semanticRole':'datetime','sectionSplit':'g','bindingButtons':['bl','br']}
    return {'model':{'nodes':nodes,'layoutBindings':{'bindings':[{'button':'bl','anchors':['left']},{'button':'br','anchors':['right']}]}},
            'components':[c],'nodeComponents':dict.fromkeys(members,'left'),'nodeOrder':order,
            'sourceDevice':{'viewport':{'width':1080,'height':2400},'layouts':[{'rows':rows}]}}


class CornerShortcutTests(unittest.TestCase):
    def test_content_section_is_split_and_hit_regions_follow_their_icons(self):
        p=fixture();self.assertEqual(len(split_footer_components(p)),1)
        cs=p['components'];self.assertEqual(len(cs),2)
        self.assertEqual(p['nodeComponents']['bl'],'left');self.assertEqual(p['nodeComponents']['br'],'right')
        self.assertEqual(cs[0]['cornerAttachment']['corner'],'bottom-left')
        self.assertEqual(cs[1]['cornerAttachment']['corner'],'bottom-right')
        self.assertTrue(all(c['semanticRole']=='shortcut-controls' for c in cs))
        self.assertFalse(split_footer_components(p))

    def test_opposite_corners_expand_spacing_without_stretching_resources(self):
        p=fixture();split_footer_components(p)
        cs=[{**c,'bounds':c['sourceBounds']} for c in p['components']];context=ctx(1600,1000)
        result=AnchorThemeLayout().layout_scene(cs,SOURCE,{'resolutionPx':{'width':1600,'height':1000}},context)
        boxes={c['id']:transformed_box(c['bounds'],result[c['id']]) for c in cs}
        self.assertAlmostEqual(boxes['left']['x'],79*result['left']['sx'])
        self.assertAlmostEqual(1600-boxes['right']['x']-boxes['right']['w'],79*result['right']['sx'])
        for c in cs:
            cid=c['id'];self.assertAlmostEqual(1000-boxes[cid]['y']-boxes[cid]['h'],66*result[cid]['sy'])
            self.assertAlmostEqual(result[cid]['sx'],result[cid]['sy'])
        self.assertLess(boxes['left']['x'],100);self.assertGreater(boxes['right']['x'],1400)

    def test_authored_grouping_is_not_split(self):
        p=fixture();p['components'][0]['layout']={'group':'manual'}
        self.assertFalse(split_footer_components(p))

    def test_corner_anchors_settled_frame_without_pulling_entry_pose_onscreen(self):
        p=fixture();split_footer_components(p)
        cs=[{**c,'bounds':{**c['sourceBounds'],'y':c['sourceBounds']['y']+200}} for c in p['components']]
        resources=[{'component':c['id'],'id':c['id'],'rect':c['sourceBounds']} for c in p['components']]
        c=ctx(1080,2400,model={'root':{'screenWidth':'1080'},'nodes':p['model']['nodes']},stableResources=resources)
        result=AnchorThemeLayout().layout_scene(cs,SOURCE,SOURCE,c)
        for component in cs:
            cid=component['id'];settled=transformed_box(component['sourceBounds'],result[cid]);entry=transformed_box(component['bounds'],result[cid])
            self.assertAlmostEqual(2400-settled['y']-settled['h'],66*result[cid]['sy'])
            self.assertGreater(entry['y']+entry['h'],2400)

    def test_offscreen_entry_pose_is_not_mistaken_for_a_stable_corner(self):
        p=fixture()
        for row in p['sourceDevice']['layouts'][0]['rows']:row[2]+=300
        self.assertFalse(split_footer_components(p))

    def test_exported_script_keeps_icons_and_buttons_at_the_same_corners(self):
        from PIL import Image
        from multires_app.pipeline.scene_pipeline import create_project
        from multires_app.engine.scene_engine import load_model,Runtime
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root=Path(tmp);source=root/'source';lock=source/'lockscreen';lock.mkdir(parents=True)
            xml='''<Lockscreen screenWidth="1080"><Group name="composition">
            <Text x="540" y="300" align="center" text="09:41" size="100"/>
            <Image name="camera" x="859" y="#screen_height-66" alignV="bottom" src="camera.png"/>
            <Image name="torch" x="79" y="#screen_height-66" alignV="bottom" src="torch.png"/>
            <Button name="cameraHit" x="859" y="#screen_height-206" w="140" h="140"><Triggers><Trigger action="double"><IntentCommand action="android.media.STILL_IMAGE_CAMERA"/></Trigger></Triggers></Button>
            <Button name="torchHit" x="79" y="#screen_height-206" w="140" h="140"><Triggers><Trigger action="double"><VariableCommand name="flashlightValue" expression="1"/></Trigger></Triggers></Button>
            </Group></Lockscreen>'''
            path=lock/'manifest.xml';path.write_text(xml,encoding='utf-8')
            for name in ('camera','torch'):Image.new('RGBA',(140,140),'white').save(lock/(name+'.png'))
            out=root/'output';create_project(source,SOURCE,{'profiles':[{'id':'tablet','resolutionPx':{'width':1600,'height':1000},'orientations':['landscape']}]},out,max_states=1,max_depth=0)
            p=json.loads((out/'project.json').read_text(encoding='utf-8'));d=p['devices'][0]
            ids={n['attributes'].get('name'):u for u,n in p['model']['nodes'].items()}
            self.assertNotEqual(p['nodeComponents'][ids['camera']],p['nodeComponents'][ids['torch']])
            path.write_bytes((out/'scripts/tablet-landscape.xml').read_bytes())
            adapted=load_model(source);rt=Runtime(adapted,d['viewport']['width'],d['viewport']['height'])
            actual={e['id']:e['rect'] for e in rt.snapshot(p['assets'])}
            for icon,button in [('camera','cameraHit'),('torch','torchHit')]:
                a,b=actual[ids[icon]],actual[ids[button]]
                for field in ('x','y','w','h'):self.assertAlmostEqual(a[field],b[field],places=3)
            left,right=actual[ids['torch']],actual[ids['camera']]
            self.assertLess(left['x'],d['viewport']['width']*.1)
            self.assertLess(d['viewport']['width']-right['x']-right['w'],d['viewport']['width']*.1)

    def test_authored_mode_change_releases_corner_and_return_restores_it(self):
        from unittest.mock import patch
        from multires_app.pipeline.scene_clusters import schedule_device
        p=fixture();split_footer_components(p);base=p['sourceDevice']['layouts'][0]['rows']
        def layout(i,y):
            rows=copy.deepcopy(base)
            for row in rows:row[2]=y
            return {'state':'s'+str(i),'rows':rows,'controlValues':{},'overrides':{},'transforms':{}}
        p['sourceDevice']['layouts']=[layout(0,2194),layout(1,1200),layout(2,2194)]
        p['sourceProfile']=SOURCE;p['model'].update(variables={},controls={},root={'screenWidth':'1080'})
        p.update(assets={},assetOrder=[],graph={'states':[{'id':'s'+str(i)} for i in range(3)],'transitions':[{'from':'s0','to':'s1'},{'from':'s1','to':'s2'}]})
        d={'profile':{'id':'wide','resolutionPx':{'width':1600,'height':1000}},'viewport':{'width':1080,'height':675},
           'layouts':[layout(0,469),layout(1,250),layout(2,469)]}
        a=AnchorThemeLayout();a.finalize_device=lambda p,d:None
        with patch('multires_app.pipeline.scene_pipeline.state_guard',side_effect=lambda p,s,d:s['id']):schedule_device(p,d,a)
        self.assertTrue(any(g.get('cornerAttachment') for g in d['layouts'][0]['adaptationPlan'].get('groups',[])))
        self.assertFalse(any(g.get('cornerAttachment') for g in d['layouts'][1]['adaptationPlan'].get('groups',[])))
        contracts={l['state']:l['adaptationPlan'] for l in d['layouts']};contract=contracts['s2']
        while contract.get('reusedFrom'):contract=contracts[contract['reusedFrom']]
        self.assertTrue(any(g.get('cornerAttachment') for g in contract.get('groups',[])))

    def test_clock_above_corner_icons_keeps_vertical_relationship(self):
        from multires_app.layout.spatial_flow import infer_flows
        es=[{'id':uid,'bbox':dict(zip(('x','y','w','h'),b)),'current_visual_bbox':dict(zip(('x','y','w','h'),b)),'cornerShortcut':uid!='clock'}
            for uid,b in [('clock',(291,437,459,472)),('torch',(79,2194,140,140)),('camera',(859,2194,140,140))]]
        flows=infer_flows(es,{'width':1080,'height':2400},{'w':1600,'h':1000},.5)
        self.assertTrue(all(f['axis']=='y' for f in flows if 'clock' in (f['first'],f['last'])))
        self.assertTrue(all(f['separationOnly'] for f in flows if 'clock' in (f['first'],f['last'])))
        self.assertTrue(any(f['axis']=='x' for f in flows if 'clock' not in (f['first'],f['last'])))

    def test_spreading_corners_does_not_pull_clock_out_of_its_original_position(self):
        p=fixture();split_footer_components(p)
        clock=comp('clock',(214,435,650,474),semanticRole='datetime')
        visual={'x':291,'y':437,'w':459,'h':472}
        cs=[{**c,'bounds':c['sourceBounds']} for c in p['components']]
        visuals={'clock':visual,**{c['id']:c['bounds'] for c in cs}}
        results=[]
        for components in ([clock],[clock,*cs]):
            c=ctx(1600,1000,sourceStableVisualBounds=visuals,stableVisualBounds=visuals)
            results.append(AnchorThemeLayout().layout_scene(components,SOURCE,{'resolutionPx':{'width':1600,'height':1000}},c)['clock'])
        for key in ('sx','sy','tx','ty'):self.assertAlmostEqual(results[0][key],results[1][key])


if __name__=='__main__':unittest.main()

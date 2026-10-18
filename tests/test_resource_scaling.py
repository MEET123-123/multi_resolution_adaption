import copy
import json
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from multires_app.engine.scene_engine import Runtime,load_model
from multires_app.pipeline.scene_pipeline import create_project,export_script
from multires_app.media.resource_scaling import generate_glyph_resources
from tests.test_anchor_layout import SOURCE,comp,ctx
from multires_app.layout.anchor_layout import AnchorThemeLayout
from multires_app.pipeline.scene_clusters import transformed_box


class ResourceScalingTests(unittest.TestCase):
    def test_balanced_policy_limits_occupancy_and_preserves_similar_shapes(self):
        from multires_app.layout.visual_scaling import resource_scale
        source={'width':1080,'height':2160}
        wide=resource_scale(source,{'x':0,'y':0,'w':1080,'h':675},{})
        self.assertEqual(wide['policy'],'balanced');self.assertAlmostEqual(wide['scale'],.5)
        self.assertLessEqual(wide['relativeHeightRatio'],1.6);self.assertLessEqual(wide['relativeAreaRatio'],1.25)
        similar=resource_scale(source,{'x':0,'y':0,'w':1188,'h':2376},{})
        self.assertAlmostEqual(similar['scale'],1.1);self.assertEqual(similar['route'],'width-preserve')
        self.assertAlmostEqual(similar['relativeAreaRatio'],1.)
        enlarged=resource_scale(source,{'x':0,'y':0,'w':1080,'h':675},{'resourceScaleFactor':10})
        self.assertLessEqual(enlarged['relativeAreaRatio'],1.25)
        with self.assertRaises(ValueError):resource_scale(source,{'w':1080,'h':675},{'maxRelativeAxisScale':.8})

    def test_width_scale_centers_and_relative_anchor_positions(self):
        c=comp('clock',(440,1000,200,100));context=ctx(2160,1350)
        out=AnchorThemeLayout().layout_scene([c],SOURCE,{'resolutionPx':{'width':2560,'height':1600},'adaptation':{'anchorLayout':{'resourceScalePolicy':'width'}}},context)
        q=transformed_box(c['bounds'],out['clock'])
        self.assertAlmostEqual(q['w'],400);self.assertAlmostEqual(q['h'],200)
        self.assertAlmostEqual(q['x']+q['w']/2,1080)
        self.assertAlmostEqual(q['y']+q['h']/2,1050/2400*1350)
        self.assertEqual(context['adaptationPlan']['resourceScalePolicy'],'width')

    def test_center_of_group_and_scaled_margins_are_axis_specific(self):
        c=comp('center',(440,1150,200,100));context=ctx(1600,1000)
        out=AnchorThemeLayout().layout_scene([c],SOURCE,{'resolutionPx':{'width':2560,'height':1600}},context)
        q=transformed_box(c['bounds'],out['center']);self.assertAlmostEqual(q['x']+q['w']/2,800);self.assertAlmostEqual(q['y']+q['h']/2,500)
        c=comp('edge',(20,20,100,100));context=ctx(1600,1000)
        out=AnchorThemeLayout().layout_scene([c],SOURCE,{'resolutionPx':{'width':2560,'height':1600}},context)
        q=transformed_box(c['bounds'],out['edge']);self.assertAlmostEqual(q['x'],20*1600/1080);self.assertAlmostEqual(q['y'],20*1000/2400)

    def test_nested_resources_scale_once_in_native_export(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            root=Path(tmp);source=root/'source';assets_source=source/'lockscreen';assets_source.mkdir(parents=True);out=root/'out'
            Image.new('RGBA',(60,40),'red').save(assets_source/'picture.png')
            for char in '01':Image.new('RGBA',(20,30),'white').save(assets_source/f'digit_{char}.png')
            (assets_source/'manifest.xml').write_text('''<Lockscreen screenWidth="1080"><Group x="100" y="100"><Group x="20" y="30"><Image x="10" y="10" src="picture.png"/><ImageSeries x="10" y="100" src="digit.png" string="'01'" space="4" mapList="00,11"/><Text x="10" y="150" size="20" text="test"/><Button x="10" y="200" w="80" h="40"/></Group></Group></Lockscreen>''',encoding='utf-8')
            original=(assets_source/'digit_0.png').read_bytes()
            create_project(source,{'resolutionPx':{'width':1080,'height':2160}},
                           {'profiles':[{'id':'phone','resolutionPx':{'width':1080,'height':2160},'orientations':['portrait']}]},out,max_states=2,max_depth=1)
            p=json.loads((out/'project.json').read_text(encoding='utf-8'));device=p['devices'][0]
            for l in device['layouts']:
                for cid in l['transforms']:l['transforms'][cid]={'sx':2.,'sy':2.,'tx':5.,'ty':7.}
            script=out/'scripts'/'phone-portrait.xml';result=export_script(p,'phone-portrait',script)
            native=copy.deepcopy(p['model']);resources=json.loads(script.with_suffix('.resources.json').read_text(encoding='utf-8'))
            # Native ids have the script filename as prefix; compare path suffixes.
            native['assetNames']=list(p['assets'])+list(resources['files'])
            native['file']=p['model']['file']
            # Preserve original ids for runtime comparison after loading the XML.
            from multires_app.engine.scene_engine import indexed_tree
            from xml.etree import ElementTree as ET
            native['nodes']=indexed_tree(ET.parse(script).getroot(),p['model']['file'])
            native['variables']={n['attributes']['name']:n for n in native['nodes'].values() if n['tag']=='Var'}
            assets={**p['assets'],**resources['files']}
            p['model']['assetNames']=list(p['assets'])
            old=Runtime(p['model'],1080,2160).snapshot(p['assets']);new=Runtime(native,1080,2160).snapshot(assets)
            old=[e for e in old if e['tag']!='Group'];new=[e for e in new if e['tag']!='Group']
            self.assertEqual(len(old),len(new))
            for a,b in zip(old,new):
                for field,scale,offset in [('x',2,5),('y',2,7),('w',2,0),('h',2,0)]:self.assertAlmostEqual(a['rect'][field]*scale+offset,b['rect'][field],places=4)
            self.assertEqual(len(resources['files']),2);self.assertEqual((assets_source/'digit_0.png').read_bytes(),original)
            self.assertEqual(result['resourceScaling']['remappedNodes'],1)
            # Exercise real HWT additions and hash/geometry auditing as well.
            import zipfile
            from multires_app.pipeline.scene_pipeline import export_package
            from multires_app.tools.batch_convert import validate
            archive=root/'source.hwt'
            with zipfile.ZipFile(archive,'w') as z:
                for item in sorted(source.rglob('*')):
                    if item.is_file():z.writestr(item.relative_to(source).as_posix(),item.read_bytes())
            p['source']=str(archive);project_path=out/'project.json'
            project_path.write_text(json.dumps(p),encoding='utf-8')
            package=out/'packages'/'phone-portrait.hwt';export_package(project_path,'phone-portrait',package)
            validation=validate(out,[package])
            self.assertEqual(validation['failures'],0,validation['examples'])
            self.assertEqual(validation['generatedResourcesChecked'],2)
            self.assertEqual(validation['checkedGlyphSnapshots'],2)

    def test_invalid_scale_policy_is_rejected(self):
        target={'resolutionPx':{'width':1600,'height':1000},'adaptation':{'anchorLayout':{'resourceScalePolicy':'stretch-image'}}}
        with self.assertRaises(ValueError):AnchorThemeLayout().layout_scene([comp('x',(10,10,100,100))],SOURCE,target,ctx())

    def test_offscreen_panels_stay_offscreen_after_height_change(self):
        comps=[comp('above',(54,-665,972,616)),comp('below',(0,2500,1080,400)),comp('right',(1200,100,200,200))]
        context=ctx(1080,675);target={'resolutionPx':{'width':2560,'height':1600}}
        out=AnchorThemeLayout().layout_scene(comps,SOURCE,target,context)
        boxes={c['id']:transformed_box(c['bounds'],out[c['id']]) for c in comps}
        self.assertLess(boxes['above']['y']+boxes['above']['h'],0)
        self.assertGreaterEqual(boxes['below']['y'],675)
        self.assertGreaterEqual(boxes['right']['x'],1080)


if __name__=='__main__':unittest.main()

import copy
import json
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from multires_app.engine.scene_engine import Runtime, load_model, extract_assets, VISUAL, Evaluator
from multires_app.media.media_layout import reference, temporal_ids, intrinsic_expression, layout_rows
from multires_app.pipeline.scene_clusters import describe_layout, classify_layouts, schedule_device
from multires_app.pipeline.source_comparison import packed_snapshot
from multires_app.pipeline.scene_pipeline import create_project, export_script, state_guard
from multires_app.preview.interaction_server import InteractiveSession

ROOT=Path(__file__).resolve().parents[1]
XML='''<Lockscreen screenWidth="1080">
<Var name="frame" expression="int(#time/100)%3"/>
<Var name="style" expression="0"/>
<Image name="movie" x="100+#style*200" y="100" src="seq.png" srcid="#frame" align="center">
 <PositionAnimation><Position time="0" x="0" y="0"/><Position time="1000" x="100" y="50"/></PositionAnimation>
 <AlphaAnimation><Alpha time="0" a="0"/><Alpha time="1000" a="255"/></AlphaAnimation>
</Image><Image name="choice" x="100" y="500" src="seq.png" srcid="#style"/>
<ImageSeries name="digits" src="seq.png" string="'01'"/>
</Lockscreen>'''


class MediaLayoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'tests');self.root=Path(self.tmp.name)
        self.source=self.root/'source';lock=self.source/'unlock/lockscreen';lock.mkdir(parents=True)
        (lock/'manifest.xml').write_text(XML,encoding='utf-8')
        for i,size in enumerate(((100,80),(140,90),(70,120))):Image.new('RGBA',size,'red').save(lock/f'seq_{i}.png')
        self.model=load_model(self.source);self.assets=extract_assets(self.source,self.model,self.root/'out')
        self.order=[uid for uid,n in self.model['nodes'].items() if n['tag'] in VISUAL]
        self.movie=next(uid for uid,n in self.model['nodes'].items() if n['attributes'].get('name')=='movie')
        comps=[{'id':uid,'name':uid,'role':'content','members':[uid],'roots':[uid]} for uid in self.order if self.model['nodes'][uid]['tag']!='Lockscreen']
        self.p={'model':self.model,'assets':self.assets,'assetOrder':list(self.assets),'nodeOrder':self.order,
                'components':comps,'nodeComponents':{c['id']:c['id'] for c in comps},'graph':{'semantics':{},'transitions':[]}}

    def tearDown(self):self.tmp.cleanup()

    def packed(self,t,state=None):return packed_snapshot(self.p,Runtime(self.model,1080,2160,state,time_ms=t),'s'+str(t))

    def test_time_sequence_detected_but_user_variant_and_glyphs_not(self):
        self.assertEqual(temporal_ids(self.model),{self.movie})

    def test_static_ui_easing_uses_settled_pose_while_sequence_uses_first_frame(self):
        lock=self.source/'unlock/lockscreen/manifest.xml'
        lock.write_text('''<Lockscreen screenWidth="1080">
          <Var name="easing" expression="min(#time/1000,1)"/>
          <Group y="-800+800*#easing"><Image name="static-card" x="100" y="900" src="seq_0.png"/></Group>
          <Image name="sequence" x="500" y="100" src="seq.png" srcid="int(#time/100)%3"/>
        </Lockscreen>''',encoding='utf8')
        model=load_model(self.source);order=[u for u,n in model['nodes'].items() if n['tag'] in VISUAL]
        model['assetNames']=list(self.assets);project={'model':model,'nodeOrder':order,'assetOrder':list(self.assets),'assets':self.assets,'graph':{'semantics':{}}}
        rt=Runtime(model,1080,2160,time_ms=2400);packed=packed_snapshot(project,rt,'settled')
        rows={model['nodes'][order[r[0]]]['attributes'].get('name'):r for r in layout_rows(project,packed)}
        self.assertEqual(rows['static-card'][2],900)
        self.assertEqual(rows['sequence'][3:5],[100,80])

    def test_autonomous_position_reference_excludes_finite_and_human_motion(self):
        from multires_app.media.media_layout import autonomous_position_ids
        lock=self.source/'unlock/lockscreen/manifest.xml'
        lock.write_text('''<Lockscreen screenWidth="1080">
          <Var name="phase"><VariableAnimation><AniFrame time="0" value="0"/><AniFrame time="1000" value="6"/></VariableAnimation></Var>
          <Var name="drawer"><VariableAnimation><AniFrame time="0" value="0"/><AniFrame time="1000" value="1080"/></VariableAnimation></Var>
          <Image name="orbit" x="100+cos(#phase)*50" src="seq_0.png"/>
          <Image name="human-size" x="100+cos(#phase)*50" w="100+#drawer" src="seq_0.png"/>
          <Image name="intro" src="seq_0.png"><PositionAnimation loop="false"><Position time="0" x="-300"/><Position time="1000" x="0"/></PositionAnimation></Image>
          <Image name="panel" x="#drawer" src="seq_0.png"/>
          <Button><Triggers><Trigger action="up"><Command target="drawer.animation" value="play"/></Trigger></Triggers></Button>
        </Lockscreen>''',encoding='utf8')
        model=load_model(self.source)
        names={model['nodes'][uid]['attributes'].get('name') for uid in autonomous_position_ids(model)}
        self.assertEqual(names,{'orbit'})

    def test_continuous_phase_cannot_change_export_layout_guard(self):
        from xml.etree import ElementTree as ET
        from multires_app.engine.scene_engine import indexed_tree
        root=ET.fromstring('<Lockscreen><Var name="clock" expression="#time"/><Image src="seq_0.png" alpha="128+sin(#clock)"/></Lockscreen>')
        nodes=indexed_tree(root,'unlock/lockscreen/manifest.xml')
        model={'nodes':nodes,'controls':['style'],'variables':{'clock':next(n for n in nodes.values() if n['tag']=='Var')}}
        layouts=[{'state':'a','controlValues':{'style':0,'clock':2400}}, {'state':'b','controlValues':{'style':1,'clock':4800}}]
        p={'model':model,'graph':{'semantics':{'behaviorControls':['style'],'guardVariables':['clock']}}}
        device={'profile':{'id':'tablet'},'layouts':layouts}
        for l in layouts:state_guard(p,{'id':l['state']},device)
        expressions=list(p['_stateGuardExpressions'].values())
        self.assertTrue(all('#clock' not in e and '#style' in e for e in expressions))

    def test_first_transparent_frame_has_layout_and_render_stays_animated(self):
        a,b=self.packed(100),self.packed(200)
        self.assertNotEqual(a['rows'],b['rows'])
        self.assertEqual(a['mediaLayout'],b['mediaLayout'])
        r=a['mediaLayout']['rows'][0];self.assertEqual(r[1:5],[50,100,100,80])
        self.assertEqual(describe_layout(self.p,a),describe_layout(self.p,b))
        zero=self.packed(0)
        self.assertFalse(any(self.order[r[0]]==self.movie for r in zero['rows']))
        self.assertTrue(any(self.order[r[0]]==self.movie for r in layout_rows(self.p,zero)))

    def test_human_state_changes_are_not_frozen(self):
        a,b=self.packed(100,{'style':0}),self.packed(100,{'style':1})
        self.assertNotEqual(a['mediaLayout']['rows'][0][1],b['mediaLayout']['rows'][0][1])
        self.assertNotEqual(describe_layout(self.p,a),describe_layout(self.p,b))

    def test_different_frame_dimensions_do_not_create_layout_templates(self):
        layouts=[self.packed(t) for t in (0,100,200,500)]
        device={'viewport':{'width':1080,'height':2160},'layouts':layouts}
        analysis=classify_layouts(self.p,device)
        self.assertEqual(analysis['summary']['layoutTemplates'],1)
        self.assertEqual(analysis['requests']['s200']['kind'],'reuse')

    def test_natural_dimensions_follow_each_frame_with_constant_scale(self):
        attr=self.model['nodes'][self.movie]['attributes']
        for axis,expected in [('width',[100,140,70]),('height',[80,90,120])]:
            expr=intrinsic_expression(self.model,self.assets,attr,axis)
            self.assertEqual([Evaluator(lambda name:i).evaluate(expr) for i in range(3)],expected)
        uniform={k:{**v,'width':100} for k,v in self.assets.items()}
        expr=intrinsic_expression(self.model,uniform,attr,'width')
        self.assertEqual(Evaluator(lambda name:99).evaluate(expr),0)
        unsupported={**attr,'srcid':'#12xs'}
        self.assertEqual(intrinsic_expression(self.model,uniform,unsupported,'width'),'100')
        self.assertIsNone(intrinsic_expression(self.model,self.assets,unsupported,'width'))

    def test_playback_reference_is_relative_and_pause_cannot_relayout(self):
        lock=self.source/'unlock/lockscreen/manifest.xml'
        lock.write_text(XML.replace('<Var name="frame" expression="int(#time/100)%3"/>','<Var name="frame"><VariableAnimation><AniFrame time="0" value="0"/><AniFrame time="200" value="2"/></VariableAnimation></Var>'),encoding='utf-8')
        model=load_model(self.source)
        def ref(state):return reference(Runtime(model,1080,2160,state,time_ms=5200),self.assets,self.order,list(self.assets))
        a=ref({'__animations':{'frame':{'started':5000}}})
        self.assertEqual(a['rows'][0][3:5],[100,80])
        b=ref({'frame':1})
        self.assertEqual(b['rows'][0][3:5],[100,80])

    def test_string_frame_paths_preserve_unsampled_intrinsic_size(self):
        attrs={'srcExp':"'seq_' + int(#frame) + '.png'"}
        expression=intrinsic_expression(self.model,self.assets,attrs,'width')
        self.assertEqual([Evaluator(lambda name:i).evaluate(expression) for i in range(3)],[100,140,70])
        uniform={k:{**v,'width':100} for k,v in self.assets.items()}
        expression=intrinsic_expression(self.model,uniform,attrs,'width')
        self.assertEqual(Evaluator(lambda name:99).evaluate(expression),0)

    def test_video_intrinsic_canvas_is_reference_at_any_time(self):
        lock=self.source/'unlock/lockscreen/manifest.xml'
        lock.write_text('<Lockscreen screenWidth="1080"><Video src="a.mp4" x="10" y="20"/></Lockscreen>',encoding='utf-8')
        model=load_model(self.source);order=list(model['nodes']);assets={'unlock/lockscreen/a.mp4':{'width':640,'height':360,'kind':'video'}}
        model['assetNames']=list(assets)
        refs=[reference(Runtime(model,1080,2160,time_ms=t),assets,order,list(assets)) for t in (0,1000,5000)]
        self.assertEqual(refs[0],refs[1]);self.assertEqual(refs[1],refs[2]);self.assertEqual(refs[0]['rows'][0][1:5],[10,20,640,360])
        from multires_app.media.media_layout import refresh_media_metadata
        uid=next(uid for uid,n in model['nodes'].items() if n['tag']=='Video');model['nodes'][uid]['attributes']['play']='false'
        row=[order.index(uid),10,20,640,360,1,0,'',0]
        p={'model':model,'nodeOrder':order,'assetOrder':list(assets),'devices':[{'viewport':{'width':1080,'height':2160},'layouts':[{'rows':[row],'overrides':{}}]}]}
        refresh_media_metadata(p);self.assertFalse(row[10]['playing']);self.assertEqual(row[9],'unlock/lockscreen/a.mp4')

    def test_sources_animation_uses_first_canvas_and_preserves_frame_offsets(self):
        xml='''<Lockscreen screenWidth="1080"><Image name="movie" x="100" y="100">
        <SourcesAnimation loop="false"><Source src="seq_0.png" x="0" time="0"/>
        <Source src="seq_1.png" x="10" time="100"/><Source src="seq_2.png" x="20" time="200"/></SourcesAnimation>
        </Image></Lockscreen>'''
        (self.source/'unlock/lockscreen/manifest.xml').write_text(xml,encoding='utf-8')
        model=load_model(self.source);order=list(model['nodes']);uid=next(n['id'] for n in model['nodes'].values() if n['tag']=='Image')
        refs=[reference(Runtime(model,1080,2160,time_ms=t),self.assets,order,list(self.assets)) for t in (0,100,200,700)]
        self.assertTrue(all(r==refs[0] for r in refs));self.assertEqual(refs[0]['rows'][0][1:5],[100,100,100,80])
        image=next(e for e in Runtime(model,1080,2160,time_ms=200).snapshot(self.assets) if e['tag']=='Image')
        self.assertEqual(image['rect']['x'],120);self.assertEqual(image['rect']['w'],70)
        expr=intrinsic_expression(model,self.assets,model['nodes'][uid]['attributes'],'width',model['nodes'][uid])
        self.assertEqual([Evaluator(lambda name:t).evaluate(expr) for t in (0,100,200,700)],[100,140,70,70])
        out=self.root/'sources-project'
        create_project(self.source,{'resolutionPx':{'width':1080,'height':2160}},
                       {'profiles':[{'id':'tablet','resolutionPx':{'width':2400,'height':1600},'orientations':['landscape']}]},out,max_states=2,max_depth=1)
        p=json.loads((out/'project.json').read_text(encoding='utf-8'));d=p['devices'][0];l=d['layouts'][0]
        path=self.root/'source-adapted/unlock/lockscreen/manifest.xml';path.parent.mkdir(parents=True)
        export_script(p,d['profile']['id'],path);adapted=load_model(self.root/'source-adapted');adapted['assetNames']=list(self.assets)
        t=l['transforms'][p['nodeComponents'][uid]]
        for phase in (0,100,200,700):
            a=next(e['rect'] for e in Runtime(model,1080,720,time_ms=phase).snapshot(self.assets) if e['tag']=='Image')
            b=next(e['rect'] for e in Runtime(adapted,1080,720,time_ms=phase).snapshot(self.assets) if e['tag']=='Image')
            for key,scale,offset in [('x','sx','tx'),('y','sy','ty'),('w','sx',None),('h','sy',None)]:
                self.assertAlmostEqual(b[key],a[key]*t[scale]+(t[offset] if offset else 0),places=3)

    def test_delayed_visibility_has_stable_first_frame_reference(self):
        xml=XML.replace('srcid="#frame" align="center"','srcid="#frame" align="center" visibility="gt(#time,500)*eq(#style,0)"')
        (self.source/'unlock/lockscreen/manifest.xml').write_text(xml,encoding='utf-8');model=load_model(self.source)
        def ref(t,state):return reference(Runtime(model,1080,2160,state,time_ms=t),self.assets,self.order,list(self.assets))
        refs=[ref(t,{'style':0}) for t in (0,100,200,700,2400)]
        self.assertTrue(all(r==refs[0] for r in refs));self.assertEqual(len(refs[0]['rows']),1)
        self.assertEqual(ref(700,{'style':1})['rows'],[])

    def test_zero_size_intro_reserves_intrinsic_canvas_without_freezing_render(self):
        xml=XML.replace('srcid="#frame" align="center"','srcid="#frame" align="center" w="100*min(#time/1000,1)" h="80*min(#time/1000,1)"')
        (self.source/'unlock/lockscreen/manifest.xml').write_text(xml,encoding='utf-8');model=load_model(self.source)
        rt=Runtime(model,1080,2160,time_ms=0)
        ref=reference(rt,self.assets,self.order,list(self.assets));self.assertEqual(ref['rows'][0][1:5],[50,100,100,80])
        self.assertEqual(len(ref['warnings']),2)
        movie=next(e for e in rt.snapshot(self.assets) if e['name']=='movie');self.assertEqual(movie['rect']['w'],0)

    def test_export_and_media_preview_preserve_affine_for_unsampled_frames(self):
        out=self.root/'project'
        create_project(self.source,{'resolutionPx':{'width':1080,'height':2160}},
                       {'profiles':[{'id':'tablet','resolutionPx':{'width':2400,'height':1600},'orientations':['landscape']}]},out,max_states=2,max_depth=1)
        p=json.loads((out/'project.json').read_text(encoding='utf-8'));d=p['devices'][0];l=d['layouts'][0]
        session=InteractiveSession(p,d['profile']['id']);before=copy.deepcopy(session.capture_state())
        count=len(session.project['graph']['states']);result=session.media_preview(400,100)
        self.assertEqual(result['mediaPolicy']['solverCalls'],0);self.assertEqual(len(session.project['graph']['states']),count)
        self.assertEqual(session.capture_state(),before)
        for frame in result['processFrames']:self.assertEqual(frame['target']['transforms'],l['transforms'])
        with self.assertRaises(ValueError):session.media_preview(10000,40)
        path=self.root/'adapted/unlock/lockscreen/manifest.xml';path.parent.mkdir(parents=True)
        export_script(p,d['profile']['id'],path);adapted=load_model(self.root/'adapted');adapted['assetNames']=list(p['assets'])
        cid=p['nodeComponents'][self.movie];t=l['transforms'][cid]
        for phase in (100,200,350):
            source=Runtime(self.model,d['viewport']['width'],d['viewport']['height'],time_ms=phase)
            target=Runtime(adapted,d['viewport']['width'],d['viewport']['height'],time_ms=phase)
            a=next(e['rect'] for e in source.snapshot(p['assets']) if e['id']==self.movie)
            b=next(e['rect'] for e in target.snapshot(p['assets']) if e['name']=='movie')
            for key,scale,offset in [('x','sx','tx'),('y','sy','ty'),('w','sx',None),('h','sy',None)]:
                self.assertAlmostEqual(b[key],a[key]*t[scale]+(t[offset] if offset else 0),places=3)


if __name__=='__main__':unittest.main()

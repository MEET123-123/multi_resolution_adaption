import unittest
from multires_app.layout.clock_semantics import promote_clock_components
from multires_app.layout.anchor_layout import AnchorThemeLayout
from multires_app.pipeline.scene_clusters import transformed_box
from tests.test_anchor_layout import comp,ctx,SOURCE


class ClockSemanticsTests(unittest.TestCase):
    def test_animated_bitmap_digits_participate_in_vertical_flow(self):
        nodes={name:{'tag':'Image','attributes':{'srcid':expression},'children':[]}
               for name,expression in [('hour','#hour/10'),('minute','#minute%10'),('art','#time/100')]}
        components=[comp('hour',(108,252,111,157),role='decoration',members=['hour']),
                    comp('minute',(108,437,111,157),role='decoration',members=['minute']),
                    comp('art',(800,200,50,50),role='decoration',members=['art'])]
        project={'model':{'nodes':nodes,'variables':{}},'components':components}
        self.assertEqual(promote_clock_components(project),['hour','minute'])
        self.assertEqual(components[2]['role'],'decoration')
        result=AnchorThemeLayout().layout_scene(components,SOURCE,{'resolutionPx':{'width':1080,'height':675}},ctx(1080,675))
        a=transformed_box(components[0]['bounds'],result['hour']);b=transformed_box(components[1]['bounds'],result['minute'])
        self.assertGreater(b['y']-a['y']-a['h'],0)

    def test_transitive_clock_input_and_manual_layout(self):
        nodes={'digit':{'tag':'Image','attributes':{'srcid':'#digits'},'children':[]}}
        components=[{'id':'digit','members':['digit'],'role':'decoration'}]
        p={'model':{'nodes':nodes,'variables':{'digits':{'attributes':{'expression':'#hour%10'}}}},'components':components}
        promote_clock_components(p);self.assertEqual(components[0]['semanticRole'],'datetime')
        components[0].update(role='decoration',layout={'anchor':'top_left'})
        self.assertEqual(promote_clock_components(p),[])
        self.assertEqual(components[0]['role'],'decoration')

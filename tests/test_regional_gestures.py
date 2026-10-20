import unittest

from multires_app.interaction.interaction_semantics import hit_test, tap_only_owner
from multires_app.interaction.regional_gestures import split_regional_gestures


def model():
    nodes={n:{'attributes':{}} for n in ('page','scroll','icon')}
    variables={'tapGuard':{'attributes':{'expression':'lt(abs(#touch_begin_x-#touch_x),20)*lt(abs(#touch_begin_y-#touch_y),20)'}}}
    def trigger(owner,condition):return {'owner':owner,'action':'up','condition':None,
        'commands':[{'attributes':{'expression':'1'},'conditions':[condition]}]}
    return {'nodes':nodes,'variables':variables,'triggers':[
        trigger('page','ge(#touch_begin_x-#touch_x,200)'),
        trigger('scroll','ge(#touch_begin_y-#touch_y,5)'),trigger('icon','#tapGuard')]}


class RegionalGesturesTests(unittest.TestCase):
    def test_global_regions_keep_source_top_gap_after_reflow(self):
        p={'model':{'nodes':{'global':{'tag':'Button'},'drawer':{'tag':'Button'}},'layoutBindings':{'bindings':[]}},
            'nodeOrder':['global','drawer'],'nodeComponents':{'global':'global','drawer':'drawer'},
            'components':[{'id':u,'role':'gesture-layer'} for u in ['global','drawer']],
            'sourceDevice':{'viewport':{'width':1000,'height':2000},'layouts':[{'state':'s','rows':[[0,0,0,1000,2100,1],[1,0,100,1000,2200,1]]}]},
            'devices':[{'viewport':{'width':1000,'height':600},'layouts':[{'state':'s','rows':[[0,0,0,1000,2100,1],[1,0,100,1000,2200,1]],'transforms':{}}]}]}
        split_regional_gestures(p);l=p['devices'][0]['layouts'][0];t=l['transforms']['drawer']
        self.assertAlmostEqual(100*t['sy']+t['ty'],30)
        self.assertAlmostEqual(2000*t['sy']+t['ty'],600)
        self.assertAlmostEqual(2200*t['sy'],660)
        self.assertAlmostEqual(l['transforms']['global']['sy']*2100,630)
    def test_partial_page_swipe_negotiates_with_scroll_and_tap_but_tap_is_unchanged(self):
        m=model();vp={'width':1000,'height':1000}
        def element(uid,x,y,w,h):return {'id':uid,'tag':'Button','visible':True,'rect':{'x':x,'y':y,'w':w,'h':h}}
        elements=[element('page',500,200,500,500),element('icon',550,300,100,100),element('scroll',500,200,500,500)]
        self.assertTrue(tap_only_owner(m,'icon'))
        self.assertEqual(hit_test(elements,600,350,m,vp,'left'),'page')
        self.assertEqual(hit_test(elements,600,350,m,vp,'up'),'scroll')
        self.assertEqual(hit_test(elements,600,350,m,vp),'scroll')
        m['nodes']['scroll']['attributes']['interceptTouch']='true'
        self.assertEqual(hit_test(elements,600,350,m,vp,'left'),'scroll')
        m['nodes']['scroll']['attributes'].clear();m['inputs']={'interaction':{'hitPolicy':'strict-z-order'}}
        self.assertEqual(hit_test(elements,600,350,m,vp,'left'),'scroll')

    def test_broad_pager_is_split_and_keeps_normalized_source_region(self):
        m=model();m['layoutBindings']={'bindings':[{'button':'page','effectTargets':['a','b','c','d','e']}]}
        source={'state':'s','rows':[[0,500,200,500,500,1,-1,'',0]],'transforms':{}}
        target={'state':'s','rows':[[0,500,-100,500,500,1,-1,'',0]],'transforms':{'card':{'sx':.2,'sy':.2,'tx':1,'ty':2}}}
        p={'model':m,'nodeOrder':['page'],'nodeComponents':{'page':'card'},
           'components':[{'id':'card','role':'content','members':['card','page'],'roots':['card'],'bindingButtons':['page']}],
           'sourceDevice':{'viewport':{'width':1000,'height':1000},'layouts':[source]},
           'devices':[{'viewport':{'width':1000,'height':600},'layouts':[target]}]}
        self.assertEqual(len(split_regional_gestures(p)),1)
        self.assertEqual(p['nodeComponents']['page'],'page');self.assertNotIn('page',p['components'][0]['members'])
        t=target['transforms']['page']
        self.assertAlmostEqual(500*t['sx']+t['tx'],500)
        self.assertAlmostEqual(-100*t['sy']+t['ty'],120)
        self.assertAlmostEqual(500*t['sy'],300)
        self.assertEqual(split_regional_gestures(p),[])

    def test_move_handler_is_never_treated_as_a_tap_only_control(self):
        m=model();self.assertFalse(tap_only_owner(m,'page'))
        m['triggers'].append({'owner':'icon','action':'move','commands':[]})
        self.assertFalse(tap_only_owner(m,'icon'))


if __name__=='__main__':unittest.main()

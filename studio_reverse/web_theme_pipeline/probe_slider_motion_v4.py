"""Probe Slider travel and user-controlled animation tracks in v4 pilot."""
import json,sys,tempfile
from pathlib import Path
sys.path[:0]=[str(Path(__file__).resolve().parents[2]/'src'),str(Path(__file__).resolve().parent)]
from multires_app.preview.live_preview import LiveSession
import interaction_families_v4 as v
import json as j
root=Path('artifacts/learning/interaction-families-v4-pilot')
family_paths={}
for line in (root/'reports'/'pairs.jsonl').open(encoding='utf8'):
 p=j.loads(line);family_paths[p['family']]=root/'themes'/(p['theme']+'.hwt')
def run():
 results={}
 for family in ('swipe_carousel','motion_control'):
  if family not in family_paths:continue
  with tempfile.TemporaryDirectory() as tmp:
   ses=LiveSession(family_paths[family],Path(tmp))
   if family=='swipe_carousel':
    x,y=475,1220
    try:
     hit=ses.press({'x':x,'y':y})
     results['sliderHit']={'slider':hit.get('slider'),'button':hit.get('button')}
     first=ses.drag_phase({'phase':'start','x':x,'y':y})
     next=ses.drag_phase({'phase':'move','x':940,'y':y})
     last=ses.drag_phase({'phase':'end','x':940,'y':y})
     results['sliderDrag']={'active':first.get('active'),'progress':next.get('progress'),
       'reached':last.get('reached'),'executed':last.get('executed'),'finalSlide':ses.runtime.lookup('slide')}
    except Exception as exc:results['sliderError']=str(exc)
   else:
    before=ses.plan()
    rule=v.FAMILIES[family][0];rect=rule[4]
    act=ses.gesture({'kind':'tap','x':rect[0]+rect[2]/2,'y':rect[1]+rect[3]/2,
                     'toX':rect[0]+rect[2]/2,'toY':rect[1]+rect[3]/2})
    after=ses.plan()
    items=[e for e in after['elements'] if e.get('name')=='v4-motion-sprite']
    results['animation']={'motionState':ses.runtime.lookup('motion'),'event':act.get('hit'),
      'spriteVisible':bool(items),'tracks':[t.get('kind') for item in items for t in item.get('tracks',[])]}
 print(json.dumps(results,ensure_ascii=False,indent=2))
if __name__=='__main__':run()

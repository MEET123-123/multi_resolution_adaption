"""HarmonyOS 4.x gesture commands and an offline runner for the emitted XML.

Only down/move/up are required. A release is classified once, before any state
mutation, so a hold, a horizontal swipe and an unlock cannot also become a tap.
"""
import json
import xml.etree.ElementTree as ET
from multires_app.engine.scene_engine import Runtime
from multires_app.interaction.interaction_semantics import hit_test
from multires_app.interaction.interaction_history import InteractionHistory, runtime_state, restore_runtime, model_fingerprint, needs_runtime_sync

PERSISTENT={'look','look_a','look_b','palette_mode','motion','focus'}
GESTURES={'none':0,'tap':1,'hold':2,'left':3,'right':4,'unlock':5}


def install_state(root,theme):
    settings={'motion':1,'focus':0,'palette_mode':2}
    settings.update({'look':1} if theme=='bear' else {'look_a':0,'look_b':0})
    definitions={
        'notification':'1',**{k:str(v) for k,v in settings.items()},
        'palette':'ifelse(eq(#palette_mode,2),ge(#hour24,19)+lt(#hour24,7),#palette_mode)',
        'low_power':'le(#battery_level,15)*ne(#battery_state,1)*ne(#battery_state,3)',
        'effective_motion':'gt(#motion,0)*eq(#low_power,0)*eq(#focus,0)',
        'animation_rate':'ifelse(eq(#motion,2),0.5,1)',
        'tap_slop':'max(12,#screen_width*0.0125)',
        'swipe_slop':'#screen_width*0.05',
        'unlock_slop':'#screen_width*0.10',
        'hold_ms':'650','press_at':'-10000','press_active':'0','press_owner':'-1',
        'gesture_moved':'0','gesture_kind':'0','react_at':'-10000',
        'react_a_at':'-10000','react_b_at':'-10000',
    }
    for name,expr in definitions.items():
        a={'name':name,'expression':expr}
        if name in settings:a.update(persist='true',globalPersist='true')
        ET.SubElement(root,'Var',a)
    for suffix in ('','_a','_b'):
        elapsed=f'(#time-#react{suffix}_at)'
        expr=f'ifelse(ge({elapsed},0)*lt({elapsed},600),sin({elapsed}*3.141592653589793/600)*#effective_motion,0)'
        ET.SubElement(root,'Var',name='reaction'+suffix,expression=expr)
    commands=ET.SubElement(root,'ExternalCommands')
    for event in ('resume','pause'):
        trigger=ET.SubElement(commands,'Trigger',action=event)
        for name in ('press_active','gesture_moved','gesture_kind'):
            ET.SubElement(trigger,'VariableCommand',name=name,expression='0')
        for name in ('react_at','react_a_at','react_b_at'):
            ET.SubElement(trigger,'VariableCommand',name=name,expression='-10000')
    return settings


def emit_actions(trigger,actions,condition):
    for name,expression in actions:
        attributes={'name':name,'expression':str(expression),'condition':condition}
        if name in PERSISTENT:attributes['persist']='true'
        ET.SubElement(trigger,'VariableCommand',attributes)


def bind_gestures(button,owner_id,*,tap=(),hold=(),left=(),right=()):
    ts=ET.SubElement(button,'Triggers')
    down=ET.SubElement(ts,'Trigger',action='down')
    for name,expr in [('press_at','#time'),('press_active','1'),('press_owner',str(owner_id)),('gesture_moved','0'),('gesture_kind','0')]:
        ET.SubElement(down,'VariableCommand',name=name,expression=expr)
    move=ET.SubElement(ts,'Trigger',action='move')
    ET.SubElement(move,'VariableCommand',name='gesture_moved',expression='1',condition=f'eq(#press_owner,{owner_id})*#press_active*(gt(abs(#touch_x-#touch_begin_x),#tap_slop)+gt(abs(#touch_y-#touch_begin_y),#tap_slop))')
    up=ET.SubElement(ts,'Trigger',action='up')
    dx='(#touch_x-#touch_begin_x)';dy='(#touch_begin_y-#touch_y)'
    still=f'le(abs({dx}),#tap_slop)*le(abs({dy}),#tap_slop)*eq(#gesture_moved,0)'
    unlock=f'ge({dy},#unlock_slop)*ge({dy},abs({dx})*1.3)'
    horizontal=f'ge(abs({dx}),#swipe_slop)*ge(abs({dx}),abs({dy})*1.5)'
    classified=f'ifelse({unlock},5,({horizontal})*lt({dx},0),3,({horizontal})*gt({dx},0),4,({still})*ge(#time-#press_at,#hold_ms),2,({still})*lt(#time-#press_at,#hold_ms),1,0)'
    expr=f'ifelse(#press_active*eq(#press_owner,{owner_id})*ge(#time,#press_at),{classified},0)'
    ET.SubElement(up,'VariableCommand',name='gesture_kind',expression=expr)
    for number,actions in [(1,tap),(2,hold),(3,left),(4,right)]:emit_actions(up,actions,f'eq(#gesture_kind,{number})')
    ET.SubElement(up,'ExternCommand',command='unlock',condition='eq(#gesture_kind,5)')
    ET.SubElement(up,'VariableCommand',name='press_active',expression='0')


def defaults_actions(theme):
    values=[('motion','1'),('focus','0'),('palette_mode','2')]
    return values+([('look','1')] if theme=='bear' else [('look_a','0'),('look_b','0')])


class InteractionSession:
    """Execute the actual package XML; records unlock instead of calling an OS.

    Native pointer arbitration and persistence are still device-test items.
    This runner explicitly models captured ownership and declared persistence.
    """
    def __init__(self,model,width,height,assets=None,saved=None,system=None,record_history=False):
        self.model=model;self.width=width;self.height=height;self.assets=assets or {}
        self.system={'hour24':8,'hour12':8,'battery_level':86,'battery_state':0,**(system or {})}
        self.runtime=Runtime(model,width,height,saved,time_ms=0)
        self.runtime.system.update(self.system);self.runtime.system['time']=0
        self.captured=None;self.begin=None;self.now=0
        self.journal=None
        self.external('resume')
        if record_history:
            self.journal=InteractionHistory(self.capture_state(),metadata={'modelHash':model_fingerprint(model),
                                           'width':width,'height':height,'scope':'offline native-XML runner'})

    def capture_state(self):
        return {'runtime':runtime_state(self.runtime),'pointer':{'owner':self.captured,'begin':list(self.begin) if self.begin else None,'timeMs':self.now}}

    def _restore_state(self,state):
        restore_runtime(self.runtime,state['runtime'])
        self.captured=state['pointer']['owner'];self.begin=tuple(state['pointer']['begin']) if state['pointer']['begin'] else None
        self.now=state['pointer']['timeMs']

    def checkout(self,event_id):
        if self.journal is None:raise ValueError('Create session with record_history=True')
        self._restore_state(self.journal.checkout(event_id))
        return self.capture_state()

    def _record(self,event,operation):
        if self.journal is None:return operation()
        before=self.capture_state();self.runtime.command_log=[]
        try:
            result=operation();after=self.capture_state()
            commands=self.runtime.command_log
            effects=[{'delivery':'recorded-only',**e} for e in self.runtime.events[len(before['runtime']['effects']):]]
            # Explicitly record direct input/variable edits made by callers.
            json.dumps([before,after,event,commands,effects],allow_nan=False)
            if needs_runtime_sync(self.journal.current,before):self.journal.commit({'type':'runtime-sync'},before)
            self.journal.commit(event,after,commands=commands,effects=effects)
            return result
        except Exception:
            self._restore_state(before)
            raise
        finally:self.runtime.command_log=None

    def external(self,event):
        return self._record({'type':'lifecycle','action':event},lambda:self._external(event))

    def _external(self,event):
        for trigger in self.model['triggers']:
            if trigger['action']==event:self.runtime.execute(trigger)

    def event(self,action,point,time_ms):
        self.now=time_ms;rt=self.runtime;rt.time_ms=time_ms
        rt.system.update(time=time_ms,touch_begin_x=self.begin[0],touch_begin_y=self.begin[1],touch_x=point[0],touch_y=point[1]);rt.cache.clear()
        for trigger in self.model['triggers']:
            if trigger['owner']==self.captured and trigger['action']==action:rt.execute(trigger)

    def down(self,x,y):
        return self._record({'type':'pointer','action':'down','point':[x,y],'timeMs':self.now},lambda:self._down(x,y))

    def _down(self,x,y):
        elements=self.runtime.snapshot(self.assets,visible_only=True)
        self.captured=hit_test(elements,x,y,model=self.model,viewport={'width':self.width,'height':self.height})
        self.begin=(x,y)
        if self.captured:self.event('down',(x,y),self.now)
        return self.captured

    def move(self,x,y,elapsed=100):
        return self._record({'type':'pointer','action':'move','point':[x,y],'timeMs':self.now+elapsed},lambda:self._move(x,y,elapsed))

    def _move(self,x,y,elapsed):
        if self.captured:self.event('move',(x,y),self.now+elapsed)

    def up(self,x,y,elapsed=100):
        return self._record({'type':'pointer','action':'up','point':[x,y],'timeMs':self.now+elapsed},lambda:self._up(x,y,elapsed))

    def _up(self,x,y,elapsed):
        if self.captured:self.event('up',(x,y),self.now+elapsed)
        self.captured=None

    def button(self,name):
        return next(e for e in self.runtime.snapshot(self.assets,visible_only=True) if e['tag']=='Button' and e['name']==name and e['visible'])

    def gesture(self,name,dx=0,dy=0,duration=100,return_to_start=False):
        button=self.button(name);b=button['rect']
        elements=self.runtime.snapshot(self.assets,visible_only=True)
        points=[(b['x']+b['w']*fx,b['y']+b['h']*fy) for fx,fy in [(.5,.5),(.1,.1),(.9,.1),(.1,.9),(.9,.9)]]
        start=next((p for p in points if hit_test(elements,*p,model=self.model,viewport={'width':self.width,'height':self.height})==button['id']),None)
        if start is None:raise ValueError('control is occluded: '+name)
        self.down(*start)
        if dx or dy:
            self.move(start[0]+dx,start[1]+dy,elapsed=duration/2)
            end=start if return_to_start else (start[0]+dx,start[1]+dy)
            self.up(*end,elapsed=duration/2)
        else:self.up(*start,elapsed=duration)

    def value(self,name):return self.runtime.lookup(name)

    def saved(self):
        return {name:self.runtime.lookup(name) for name,node in self.model['variables'].items()
                if node['attributes'].get('persist')=='true' or node['attributes'].get('globalPersist')=='true'}

    def unlocks(self):
        return sum(e['tag']=='ExternCommand' and e['attributes'].get('command')=='unlock' for e in self.runtime.events)

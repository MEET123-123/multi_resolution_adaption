"""Versioned interaction rules, dependency graph and deterministic reference runner.

The IR describes rules, not an enumeration of possible variable values. Native
effects and unsupported nodes stay explicit; no platform APIs are invoked.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import heapq
import json
import re
from collections import defaultdict

from multires_app.engine.scene_engine import REF, Evaluator, ExpressionError, parse_expression
from multires_app.interaction.interaction_history import InteractionHistory

EVENTS={'down':'pointer.down','move':'pointer.move','up':'pointer.up','tap':'gesture.tap','click':'gesture.tap',
        'double':'gesture.double','long':'gesture.long','init':'lifecycle.init','resume':'lifecycle.resume',
        'pause':'lifecycle.pause','variable-change':'variable.change'}
OPS={ast.Add:'add',ast.Sub:'subtract',ast.Mult:'multiply',ast.Div:'divide',ast.Mod:'modulo',
     ast.USub:'negative',ast.UAdd:'positive',ast.Not:'not'}


def expression(value):
    source=str(value if value is not None else 0)
    def encode(n):
        if isinstance(n,ast.Constant) and isinstance(n.value,(str,int,float,bool)):return {'op':'literal','value':n.value}
        if isinstance(n,ast.Name) and n.id in {'true','false'}:return {'op':'literal','value':int(n.id=='true')}
        if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and not n.keywords:
            if n.func.id=='ref' and len(n.args)==1 and isinstance(n.args[0],ast.Constant):return {'op':'reference','name':n.args[0].value}
            return {'op':'call','name':n.func.id,'args':[encode(a) for a in n.args]}
        if isinstance(n,ast.BinOp) and type(n.op) in OPS:return {'op':OPS[type(n.op)],'args':[encode(n.left),encode(n.right)]}
        if isinstance(n,ast.UnaryOp) and type(n.op) in OPS:return {'op':OPS[type(n.op)],'args':[encode(n.operand)]}
        raise ValueError('unsupported expression syntax')
    result={'source':source,'language':'huawei-expression','reads':sorted(set(REF.findall(source)))}
    try:
        text=REF.sub(lambda m:'ref('+repr(m[1])+')',source);text=re.sub(r'\bnot\s*\(','logical_not(',text)
        result['ast']=encode(parse_expression(text).body)
    except (SyntaxError,ValueError,ExpressionError):result['ast']=None
    return result


def dependencies(model):
    """Transitive references with bounded fixed point, including string refs."""
    direct={name:set(REF.findall(v['attributes'].get('expression',''))) for name,v in model['variables'].items()}
    # VariableAnimation destinations are reactive dependencies too. Without
    # them a human-selected page is mistaken for a free playback phase.
    for name,var in model['variables'].items():
        for child in var['children']:
            animation=model['nodes'][child]
            if animation['tag']!='VariableAnimation':continue
            for frame in animation['children']:
                direct[name].update(REF.findall(str(model['nodes'][frame]['attributes'])))
    expanded={name:set(refs) for name,refs in direct.items()}
    while True:
        updated={name:refs|set().union(*(expanded.get(r,set()) for r in refs)) for name,refs in expanded.items()}
        if updated==expanded:return direct,expanded
        expanded=updated


def node_reads(model):
    _,transitive=dependencies(model);result={}
    for uid,node in model['nodes'].items():
        fields={k:set(REF.findall(v)) for k,v in node['attributes'].items()}
        parent=node['parent']
        while parent:
            fields.setdefault('ancestorVisibility',set()).update(REF.findall(model['nodes'][parent]['attributes'].get('visibility','')))
            parent=model['nodes'][parent]['parent']
        result[uid]={k:sorted(refs|set().union(*(transitive.get(r,set()) for r in refs))) for k,refs in fields.items() if refs}
    return result


def compile_interactions(model,source_engine='unspecified',graph=None):
    nodes=model['nodes'];variables={};rules=[];edges=[];reads=node_reads(model)
    def native_tree(uid):
        n=nodes[uid]
        return {'tag':n['tag'],'attributes':copy.deepcopy(n['attributes']),'children':[native_tree(c) for c in n['children']]}
    direct,transitive=dependencies(model)
    for name,node in model['variables'].items():
        a=node['attributes'];animated=any(nodes[c]['tag'].endswith('Animation') for c in node['children'])
        variables[name]={'type':a.get('type','number'),'initial':expression(a.get('expression','0')),
                         'mutable':a.get('const','false').lower()!='true',
                         'persistence':'global' if a.get('globalPersist','false').lower()=='true' else 'theme' if a.get('persist','false').lower()=='true' else 'session',
                         'reactive':name not in model['controls'],'animated':animated,'sourceNode':node['id'],'native':native_tree(node['id'])}
    for trigger in model['triggers']:
        owner=nodes.get(trigger['owner'],{});actions=[];guards=[]
        if trigger.get('condition'):guards.append(expression(trigger['condition']))
        parent=trigger['owner']
        if owner.get('tag')=='Button':
            while parent:
                a=nodes[parent]['attributes']
                if 'visibility' in a:guards.append(expression(a['visibility']))
                parent=nodes[parent]['parent']
        for order,c in enumerate(trigger['commands']):
            a=c['attributes'];kind='assign' if c['tag']=='VariableCommand' else 'animation-control' if c['tag']=='Command' and '.animation' in a.get('target','') else 'native-effect'
            action={'id':c['id'],'op':kind,'order':order,'guards':[expression(x) for x in c['conditions']],
                    'delay':expression(a.get('delay','0')),'native':{'tag':c['tag'],'attributes':copy.deepcopy(a)}}
            if kind=='assign':
                action.update(variable=a.get('name'),value=expression(a.get('expression','0')))
                edges.append({'from':trigger['id'],'to':a.get('name'),'relation':'writes'})
            ancestor=nodes[c['id']]['parent']
            containers=[]
            while ancestor and ancestor!=trigger['id']:
                if nodes[ancestor]['tag'] in {'CycleCommand','GroupCommands','LoopCommand'}:containers.append(ancestor)
                ancestor=nodes[ancestor]['parent']
            if containers or c['tag'] in {'CycleCommand','GroupCommands','LoopCommand'}:
                action['unsupportedControlFlow']=containers or [c['id']]
            actions.append(action)
        event=EVENTS.get(trigger['action'],'native.'+trigger['action'])
        rule={'id':trigger['id'],'owner':trigger['owner'],'event':event,'nativeEvent':trigger['action'],
              'guards':guards,'actions':actions,'native':native_tree(trigger['id']),
              'reactionVariable':owner.get('attributes',{}).get('name') if owner.get('tag')=='Var' else None}
        rule['reads']=sorted(set().union(*(set(e['reads']) for e in guards),*(set(REF.findall(str(c['attributes']))) for c in trigger['commands'])))
        for name in rule['reads']:edges.append({'from':name,'to':rule['id'],'relation':'reads'})
        rules.append(rule)
    for name,refs in direct.items():
        for ref in refs:edges.append({'from':ref,'to':name,'relation':'derives'})
    for uid,fields in reads.items():
        for field,refs in fields.items():
            for ref in refs:edges.append({'from':ref,'to':uid,'relation':'drives','property':field})
    result={'schemaVersion':1,'kind':'theme-interaction-ir','sourceEngine':source_engine,'adapter':'huawei-xml',
            'variables':variables,'rules':rules,'dependencies':{k:sorted(v) for k,v in transitive.items()},'nodeReads':reads,'edges':edges,
            'semantics':{'commandOrder':'source order; delayed actions ordered by deadline then insertion',
                         'reactions':'run to completion with explicit budget','externalEffects':'record-only',
                         'unknownExpressions':'preserved; reference runner raises','animations':'preserved; native sampling still uses scene_engine',
                         'stateSpace':'symbolic rules plus bounded observation witnesses'}}
    if graph:
        result['observations']={'states':[{'id':s['id'],'values':s['controlValues'],'visibleNodes':s['visibleElements'],'trace':s['trace']} for s in graph['states']],
                                'transitions':graph['transitions'],'coverage':graph['coverage']}
    return result


class InteractionMachine:
    """Engine-neutral AST runner for assign/guard/delay/reaction/history semantics.

    This runner consumes IR JSON without reading XML. Native effects are output
    events for a platform adapter; unsupported animations remain diagnostics.
    """
    def __init__(self,document,system=None):
        if document.get('schemaVersion')!=1 or document.get('kind')!='theme-interaction-ir':raise ValueError('unknown interaction IR')
        self.ir=copy.deepcopy(document);self.values={};self.system=dict(system or {});self.time=0.;self.sequence=0
        self.queue=[];self.effects=[];self.stack=set();self.command_log=[];self.diagnostics=[]
        self.evaluator=Evaluator(self.lookup)
        for name,spec in self.ir['variables'].items():
            if not spec['reactive']:self.values[name]=self.evaluate(spec['initial'])
        fingerprint=hashlib.sha256(json.dumps(document,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        self.history=InteractionHistory(self.snapshot(),metadata={'irHash':fingerprint})

    def evaluate(self,expr):
        def decode(n):
            if not n:raise ExpressionError('opaque expression requires native adapter')
            op=n['op']
            if op=='literal':return ast.Constant(value=n['value'])
            if op=='reference':return ast.Call(func=ast.Name(id='ref'),args=[ast.Constant(value=n['name'])],keywords=[])
            if op=='call':return ast.Call(func=ast.Name(id=n['name']),args=[decode(a) for a in n['args']],keywords=[])
            reverse={v:k for k,v in OPS.items()}
            if op not in reverse:raise ExpressionError('unknown AST operation '+op)
            args=[decode(a) for a in n['args']]
            return ast.UnaryOp(op=reverse[op](),operand=args[0]) if op in {'positive','negative','not'} else ast.BinOp(left=args[0],op=reverse[op](),right=args[1])
        value=self.evaluator._node(decode(expr['ast']))
        if isinstance(value,float):
            import math
            if not math.isfinite(value):raise ExpressionError('non-finite result')
        return value

    def lookup(self,name):
        if name=='time':return self.time
        if name in self.values:return self.values[name]
        if name in self.system:return self.system[name]
        spec=self.ir['variables'].get(name)
        if spec is None:raise ExpressionError('missing external variable '+name)
        if name in self.stack:raise ExpressionError('cyclic variable '+name)
        if spec.get('animated'):raise ExpressionError('animated variable requires native sampling: '+name)
        self.stack.add(name)
        try:return self.evaluate(spec['initial'])
        finally:self.stack.remove(name)

    def snapshot(self):
        return copy.deepcopy({'values':self.values,'system':self.system,'timeMs':self.time,'scheduled':self.queue,
                              'sequence':self.sequence,'effects':self.effects,'diagnostics':self.diagnostics})

    def _restore(self,snapshot):
        self.values=copy.deepcopy(snapshot['values']);self.system=copy.deepcopy(snapshot['system']);self.time=snapshot['timeMs']
        self.queue=copy.deepcopy(snapshot['scheduled']);heapq.heapify(self.queue);self.sequence=snapshot['sequence']
        self.effects=copy.deepcopy(snapshot['effects']);self.diagnostics=copy.deepcopy(snapshot['diagnostics'])

    def checkout(self,event):self._restore(self.history.checkout(event));return self.snapshot()
    def undo(self):return self.checkout(self.history.parent or self.history.cursor)

    def _guards(self,guards):return all(bool(self.evaluate(g)) for g in guards)

    def _run_actions(self,rule):
        for action in rule['actions']:
            if action.get('unsupportedControlFlow'):raise ExpressionError('native control-flow container requires adapter')
            delay=float(self.evaluate(action['delay']))
            if delay<0:raise ValueError('negative command delay')
            if delay:
                self.sequence+=1;heapq.heappush(self.queue,(self.time+delay,self.sequence,copy.deepcopy(action)))
            else:self._execute(action)

    def _execute(self,action):
        if not self._guards(action['guards']):return
        if action['op']=='assign':
            name=action['variable'];spec=self.ir['variables'].get(name)
            if spec and not spec['mutable']:raise ValueError('cannot assign const variable '+name)
            previous=self.lookup(name) if spec or name in self.values else None
            value=self.evaluate(action['value']);self.values[name]=value
            self.command_log.append({'command':action['id'],'timeMs':self.time,'variable':name,'before':previous,'after':value})
        else:
            self.effects.append({'command':action['id'],'timeMs':self.time,'operation':action['op'],'native':copy.deepcopy(action['native']),'executed':False})

    def _reaction_values(self):
        names={r['reactionVariable'] for r in self.ir['rules'] if r['event']=='variable.change' and r['reactionVariable']}
        return {n:self.lookup(n) for n in names}

    def _settle(self,before):
        for _ in range(64):
            after=self._reaction_values();changed={n for n in after if after[n]!=before.get(n)}
            if not changed:return
            before=after
            for rule in self.ir['rules']:
                if rule['event']=='variable.change' and rule['reactionVariable'] in changed and self._guards(rule['guards']):self._run_actions(rule)
        raise ValueError('reaction budget exceeded')

    def dispatch(self,event,owner=None,payload=None):
        if owner is None and event.startswith(('pointer.','gesture.')):raise ValueError('interactive event requires owner')
        before=self.snapshot();self.command_log=[]
        try:
            reactions=self._reaction_values();self.system.update(payload or {})
            for rule in self.ir['rules']:
                if rule['event']==event and (owner is None or rule['owner']==owner) and self._guards(rule['guards']):self._run_actions(rule)
            self._settle(reactions)
            self.history.commit({'type':event,'owner':owner,'payload':payload or {},'commands':copy.deepcopy(self.command_log)},self.snapshot())
        except Exception:self._restore(before);raise
        return self.snapshot()

    def save(self):
        return {'schemaVersion':1,'kind':'theme-interaction-session','history':self.history.to_dict()}

    @classmethod
    def restore(cls,document,session):
        if session.get('schemaVersion')!=1 or session.get('kind')!='theme-interaction-session':raise ValueError('unknown interaction session')
        history=InteractionHistory.from_dict(session['history'])
        result=cls(document,system=history.current['system'])
        if result.history.metadata!=history.metadata:raise ValueError('session belongs to different interaction IR')
        result.history=history;result._restore(history.current)
        return result

    def advance(self,elapsed_ms):
        if elapsed_ms<0:raise ValueError('time cannot move backwards')
        before=self.snapshot();deadline=self.time+elapsed_ms;self.command_log=[];steps=0
        try:
            while self.queue and self.queue[0][0]<=deadline:
                steps+=1
                if steps>4096:raise ValueError('timer budget exceeded')
                reactions=self._reaction_values();self.time,_,action=heapq.heappop(self.queue);self._execute(action);self._settle(reactions)
            reactions=self._reaction_values();self.time=deadline;self._settle(reactions)
            self.history.commit({'type':'timer.advance','elapsedMs':elapsed_ms,'commands':copy.deepcopy(self.command_log)},self.snapshot())
        except Exception:self._restore(before);raise
        return self.snapshot()

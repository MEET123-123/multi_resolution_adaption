"""Human gesture transactions and conservative behavior-aware state abstraction.

This is script analysis, not an LLM or proof of arbitrary XML reachability.
Every admitted state has an executable gesture path; unsupported branches are
reported. Text-only counters do not manufacture independent interaction states.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections import defaultdict, deque

from multires_app.engine.scene_engine import REF, Runtime, VISUAL, touch_samples, coverage_report


def swipe_axes(model, owner):
    """Infer touch axes through handler guards and reactive Var definitions.

    This is routing evidence, not proof of a gesture branch. Empty/ambiguous
    evidence never makes a control transparent; explicit interception wins.
    """
    refs=set()
    for trigger in model.get('triggers',[]):
        if trigger['owner']!=owner:continue
        refs.update(REF.findall(trigger.get('condition') or ''))
        for command in trigger['commands']:
            refs.update(REF.findall(str(command['attributes'])))
            refs.update(REF.findall(str(command['conditions'])))
    pending=list(refs)
    while pending:
        node=model.get('variables',{}).get(pending.pop(),{})
        extra=set(REF.findall(node.get('attributes',{}).get('expression','')))-refs
        refs.update(extra);pending.extend(extra)
    return [axis for axis in ('x','y') if 'touch_begin_'+axis in refs and 'touch_'+axis in refs]


def analyze_interactions(model):
    from multires_app.interaction.interaction_ir import dependencies
    definitions,_=dependencies(model)
    writers=defaultdict(list)
    visual_reads=set();condition_reads=set();asset_reads=set();geometry_reads=set();user_writes=set();user_animations=set()
    for node in model["nodes"].values():
        a=node["attributes"]
        if node["tag"] in VISUAL:
            visual_reads.update(REF.findall(a.get("visibility","")))
            visual_reads.update(REF.findall(a.get("alpha","")))
            asset_reads.update(REF.findall(a.get("srcExp","")+a.get("srcid","")))
            for key in ("x","y","w","h","width","height","size","space","scale","scaleX","scaleY",
                        "rotation","angle","align","alignV","color"):
                geometry_reads.update(REF.findall(a.get(key,"")))
    for trigger in model["triggers"]:
        human=model["nodes"].get(trigger["owner"],{}).get("tag")=="Button"
        if human:condition_reads.update(REF.findall(trigger.get("condition") or ""))
        for command in trigger["commands"]:
            if human:
                for condition in command["conditions"]:condition_reads.update(REF.findall(condition))
                if command["tag"]=="VariableCommand":user_writes.add(command["attributes"].get("name",""))
                if command["tag"]=="Command" and ".animation" in command["attributes"].get("target",""):user_animations.add(command["attributes"]["target"].split(".animation")[0])
            if command["tag"]=="VariableCommand":writers[command["attributes"].get("name","")].append(command)
    # Only predicates and categorical image switches affect state identity.
    # Follow assignments backwards, including Var definitions. Future guards
    # retain exact control values, so counters that unlock a branch are kept.
    core=set(visual_reads)|condition_reads
    def closure(seed):
        relevant=set(seed)
        while True:
            expanded=set(relevant)
            for name in relevant:
                expanded.update(definitions.get(name,set()))
                for command in writers.get(name,[]):expanded.update(REF.findall(command["attributes"].get("expression","")))
            if expanded==relevant:return relevant
            relevant=expanded
    core_relevant=closure(core);relevant=closure(core|geometry_reads|asset_reads)
    controls=set(model["controls"])
    transient={name for name,commands in writers.items() if commands and all("#time" in c["attributes"].get("expression","") for c in commands) and name not in core_relevant}
    behavior=sorted((controls & relevant & (user_writes|core_relevant))-transient)
    by_owner=defaultdict(list)
    for trigger in model["triggers"]:
        if model["nodes"].get(trigger["owner"],{}).get("tag")=="Button":by_owner[trigger["owner"]].append(trigger)
    actions=[]
    for owner,triggers in by_owner.items():
        refs=set();writes=set();external=[]
        for t in triggers:
            for c in t["commands"]:
                refs.update(REF.findall(str(c["attributes"])))
                if c["tag"]=="VariableCommand":writes.add(c["attributes"].get("name"))
                elif c["tag"] not in {"Command"}:external.append(c["tag"])
        # Displacement tests can be indirect through a Var (e.g. #dianji).
        expanded=set(refs)
        for _ in range(len(definitions)+1):
            more=set().union(*(definitions.get(n,set()) for n in expanded)) if expanded else set()
            if more<=expanded:break
            expanded.update(more)
        actions.append({"owner":owner,"name":model["nodes"][owner]["attributes"].get("name",owner.rsplit("/",1)[-1]),
                        "triggerIds":[t["id"] for t in triggers],"eventActions":[t["action"] for t in triggers],
                        "supportsSwipe":any(t["action"]=="move" for t in triggers) or bool({"touch_begin_x","touch_begin_y"}&expanded),
                        "swipeAxes":swipe_axes(model,owner),
                        "supportsLongPress":any(t["action"]=="long" for t in triggers),"supportsDoubleTap":any(t["action"]=="double" for t in triggers),
                        "writes":sorted(w for w in writes if w),"reads":sorted(refs),"externalActions":sorted(set(external)),
                        "intent":"system-action" if external else "scene-switch" if writes&relevant else "data-update",
                        "evidence":"Button handlers + predicate/assignment dependency slice"})
    # Gesture-local predicates (e.g. tap-vs-drag) cannot identify a stable
    # scene: the next pointerdown changes them before any scene command runs.
    _,transitive=dependencies(model)
    touch_names={'touch_x','touch_y','touch_begin_x','touch_begin_y'}
    guard_variables={n for n in (set(model['variables'])&core_relevant)-controls if not transitive.get(n,set())&touch_names}
    from multires_app.interaction.animation_semantics import scene_animation_names
    scene_animations=scene_animation_names(model)
    # A gesture can change only an animation, with no separate page flag.
    # Such states must survive exploration, clustering and native guards.
    behavior=sorted(set(behavior)|scene_animations)
    guard_variables.update(scene_animations)
    return {"behaviorControls":behavior,"guardVariables":sorted(guard_variables),"dataOnlyControls":sorted(controls-set(behavior)),"userWrittenVariables":sorted(user_writes),"userControlledAnimations":sorted(user_animations),
            "sceneAnimationVariables":sorted(scene_animations),
            "actions":actions,"variableDependencies":{n:sorted(v) for n,v in definitions.items()},
            "note":"状态保留未来条件依赖；只影响文字/数值内容的控制量不单独生成场景。未知引擎能力不作可达性证明。"}


def initialize(model,width,height):
    rt=Runtime(model,width,height,model.get("inputs",{}).get("variables",{}),time_ms=0)
    for trigger in model["triggers"]:
        if trigger["action"] in {"init","resume"}:rt.execute(trigger)
    rt.time_ms=2400;rt.system["time"]=2400;rt.cache.clear()
    return rt


def hit_test(elements,x,y,model=None,viewport=None,gesture=None):
    # Pointer interception follows button paint order; images are not controls.
    matches=[]
    for e in reversed(elements):
        r=e["rect"]
        if e["tag"]=="Button" and e["visible"] and r["w"]>0 and r["h"]>0 and r["x"]<=x<=r["x"]+r["w"] and r["y"]<=y<=r["y"]+r["h"]:
            matches.append(e)
    if not matches:return None
    policy=(model or {}).get("inputs",{}).get("interaction",{}).get("hitPolicy","specific-controls-before-global-gestures")
    if model and viewport and policy!='strict-z-order' and gesture in {'left','right','up','down'}:
        axis='x' if gesture in {'left','right'} else 'y'
        area=viewport['width']*viewport['height']
        # Coexisting full-screen horizontal paging and vertical unlock layers
        # must negotiate by direction. Local controls still capture normally.
        matches=[e for e in matches if not (
            e['rect']['w']*e['rect']['h']>=area*.85
            and model['nodes'][e['id']]['attributes'].get('interceptTouch','false').lower()!='true'
            and swipe_axes(model,e['id'])==[('y' if axis=='x' else 'x')])]
        if not matches:return None
    top=matches[0]
    if model and viewport and policy!="strict-z-order":
        area=viewport["width"]*viewport["height"]
        attributes=model["nodes"][top["id"]]["attributes"]
        if top["rect"]["w"]*top["rect"]["h"]>=area*.85 and attributes.get("interceptTouch","false").lower()!="true":
            specific=[e for e in matches if e["rect"]["w"]*e["rect"]["h"]<area*.35]
            if specific:return specific[0]["id"]
    return top["id"]


def perform_action(runtime, action, assets, verify_hit=True, elements=None, on_event=None):
    if elements is None:elements=runtime.snapshot(assets,visible_only=True,only_ids=None if verify_hit else {action['owner']})
    owner=action["owner"]
    element=next((e for e in elements if e["id"]==owner and e["visible"]),None)
    if not element:return {"valid":False,"reason":"button-not-visible","executed":[]}
    r=element["rect"];fx,fy,bx,by=action["sample"]
    start=(r["x"]+bx*r["w"],r["y"]+by*r["h"]);end=(r["x"]+fx*r["w"],r["y"]+fy*r["h"])
    if verify_hit and (not 0<=start[0]<=runtime.system["screen_width"] or not 0<=start[1]<=runtime.system["screen_height"]):
        return {"valid":False,"reason":"outside-screen","executed":[]}
    dx=end[0]-start[0];dy=end[1]-start[1]
    direction=('left' if dx<0 else 'right') if abs(dx)>abs(dy) else ('up' if dy<0 else 'down')
    if verify_hit and hit_test(elements,*start,model=runtime.model,viewport={"width":runtime.system["screen_width"],"height":runtime.system["screen_height"]},gesture=direction if action['kind']=='swipe' else None)!=owner:return {"valid":False,"reason":"occluded-by-later-button","executed":[]}
    triggers=[t for t in runtime.model["triggers"] if t["owner"]==owner]
    supported={t["action"] for t in triggers}
    events=[("down",0,start)]
    if action["kind"]=="swipe":
        for i in (1,2,3,4):events.append(("move",i*70,(start[0]+(end[0]-start[0])*i/4,start[1]+(end[1]-start[1])*i/4)))
    if action["kind"]=="long-press":
        duration=max(float(runtime.model["nodes"][owner]["attributes"].get("longTouchTime",600)),float(action.get("durationMs",0)))
        events.append(("long",duration+20,start));events.append(("up",duration+100,end))
    elif action["kind"]=="double-tap":
        events.extend([("up",80,end),("down",180,start),("up",260,end),("double",270,end)])
    else:events.append(("up",340 if action["kind"]=="swipe" else 100,end))
    if action["kind"]=="tap":
        events.append(("tap",110,end))
        events.append(("click",110,end))
    # All handlers in a gesture share the exact begin point and captured owner.
    # The preview does not invent an up-only transaction from an idle state.
    executed=[];timeline=[];origin=runtime.time_ms
    for event,elapsed,position in events:
        runtime.time_ms=origin+elapsed;runtime.system.update(time=runtime.time_ms,touch_begin_x=start[0],touch_begin_y=start[1],touch_x=position[0],touch_y=position[1]);runtime.cache.clear()
        for trigger in triggers:
            if trigger["action"]!=event:continue
            if trigger.get("condition") and not runtime.value(trigger["condition"],0,trigger["id"]):continue
            before={name:runtime.lookup(name) for name in runtime.model["variables"]}
            runtime.execute(trigger);runtime.settle(before)
            executed.append(trigger["id"])
            timeline.append({"event":event,"timeMs":elapsed,"trigger":trigger["id"],"begin":list(start),"point":list(position)})
        if on_event:on_event(event,elapsed,position,runtime)
    if not executed:return {"valid":False,"reason":"no-recognized-human-handler","executed":[]}
    runtime.time_ms=origin+max(2400,events[-1][1]+2400 if action["kind"]=="long-press" else 2400);runtime.system["time"]=runtime.time_ms;runtime.cache.clear()
    return {"valid":True,"executed":executed,"timeline":timeline,"capturedOwner":owner}


def candidate_actions(runtime, semantics, assets, elements=None):
    if elements is None:elements=runtime.snapshot(assets,visible_only=True)
    visible={e["id"]:e for e in elements if e["visible"] and e["tag"]=="Button"}
    by_id={t["id"]:t for t in runtime.model["triggers"]}
    candidates=[];seen=set()
    for spec in semantics["actions"]:
        if spec["owner"] not in visible:continue
        r=visible[spec["owner"]]["rect"]
        samples=[("center",(.5,.5,.5,.5))]
        for uid in spec["triggerIds"]:samples.extend(touch_samples(runtime,by_id[uid],r))
        for gesture,sample in samples:
            if gesture in {"left","right","up","down"}:continue
            fx,fy,_,_=sample
            key=(spec["owner"],"tap",round(fx,4),round(fy,4))
            if key not in seen:
                seen.add(key);candidates.append({"owner":spec["owner"],"kind":"tap","gesture":gesture,"sample":[fx,fy,fx,fy],"intent":spec["intent"]})
        if spec["supportsSwipe"]:
            for direction,sample in (("left",(.1,.5,.9,.5)),("right",(.9,.5,.1,.5)),("up",(.5,.1,.5,.9)),("down",(.5,.9,.5,.1))):
                candidates.append({"owner":spec["owner"],"kind":"swipe","gesture":direction,"sample":list(sample),"intent":spec["intent"]})
        if spec["supportsLongPress"]:candidates.append({"owner":spec["owner"],"kind":"long-press","gesture":"hold","sample":[.5,.5,.5,.5],"intent":spec["intent"]})
        if spec["supportsDoubleTap"]:candidates.append({"owner":spec["owner"],"kind":"double-tap","gesture":"double","sample":[.5,.5,.5,.5],"intent":spec["intent"]})
    return candidates


def state_signature(runtime, elements, semantics):
    # Keep layout-changing geometry, visibility and future behavioral controls.
    # Ignore clocks, alpha animation phases, text contents, playback timestamps.
    visible=set()
    for e in elements:
        if not e.get("logicalVisible",e["visible"]) or e["tag"]=="Group":continue
        visible.add(e["id"])
    def normalized(value):
        return int(value) if isinstance(value,float) and value.is_integer() else value
    values={n:normalized(runtime.lookup(n)) for n in semantics["behaviorControls"]}
    return json.dumps([sorted(visible),values],sort_keys=True,ensure_ascii=False),values


def explore_human(model, source_profile, assets, max_states=96, max_depth=6):
    r=source_profile["resolutionPx"];semantics=analyze_interactions(model)
    max_gestures=int(model.get('inputs',{}).get('interaction',{}).get('maxExplorationGestures',4096))
    if max_gestures<1:raise ValueError('maxExplorationGestures must be positive')
    attempted=0;budget_exceeded=False;processed=0
    initial=initialize(model,r["width"],r["height"])
    states=[];transitions=[];signatures={};queue=deque();diagnostics={};rejected=defaultdict(int);replay_cache={}
    def admit(rt,depth,trace,label):
        elements=rt.logical_snapshot();signature,behavior=state_signature(rt,elements,semantics);diagnostics.update(rt.issues)
        if signature in signatures:
            state=states[signatures[signature]];state["mergedWitnessCount"]+=1
            return state["id"],False
        if len(states)>=max_states:return None,False
        index=len(states);sid=f"state-{index:03d}";signatures[signature]=index
        state={"id":sid,"label":label,"overrides":copy.deepcopy(rt.state),"depth":depth,"trace":trace,"activeOwner":None,
               "visibleElements":[e["id"] for e in elements if e["visible"]],"controlValues":{n:rt.lookup(n) for n in model["controls"]},
               "behaviorValues":behavior,"mergedWitnessCount":0,"signatureHash":hashlib.sha1(signature.encode()).hexdigest()[:16]}
        states.append(state);queue.append(state);return sid,True
    admit(initial,0,[],"初始 / 完整操作前")
    # Reserve important authored journeys before broad exploration fills the
    # state budget. These are real hit-tested gestures, never variable patches.
    required_results=[]
    for journey in model.get('inputs',{}).get('interaction',{}).get('requiredTraces',[]):
        current=initialize(model,r['width'],r['height']);sid=states[0]['id'];trace=[]
        for step,raw in enumerate(journey['actions'],1):
            action={**raw,'gesture':raw.get('gesture',raw['kind']),'intent':raw.get('intent','required-journey')}
            before=copy.deepcopy(current.state)
            outcome=perform_action(current,action,assets)
            if not outcome['valid']:raise ValueError(f"required journey {journey['name']} step {step}: {outcome['reason']}")
            trace=trace+[{'humanAction':action}]
            target,_=admit(current,step,trace,journey['name']+' / '+str(step))
            if target is None:raise ValueError('max_states is too small for required journeys')
            transitions.append({'from':sid,'to':target,'trigger':outcome['executed'][-1],
                'triggers':list(dict.fromkeys(outcome['executed'])),'owner':action['owner'],
                'action':action['kind'],'gesture':action['gesture'],'sample':action['sample'],
                'intent':action['intent'],'timeline':outcome['timeline'],'events':copy.deepcopy(current.events),
                'changes':{k:v for k,v in current.state.items() if k!='__animations' and before.get(k)!=v},
                'bounded':False,'witnesses':[{'gesture':action['gesture'],'sample':action['sample']}],
                'noVisualChange':sid==target,'requiredJourney':journey['name']})
            sid=target
        required_results.append({'name':journey['name'],'steps':len(trace),'finalState':sid,'passed':True})
    while queue:
        state=queue.popleft()
        current=replay_human_state(model,r["width"],r["height"],state,assets,cache=replay_cache)
        current_elements=current.snapshot(assets,visible_only=True)
        edges={}
        for action in candidate_actions(current,semantics,assets,current_elements):
            if attempted>=max_gestures:budget_exceeded=True;break
            attempted+=1
            result=Runtime(model,r["width"],r["height"],copy.deepcopy(current.state),time_ms=current.time_ms)
            result.system=copy.deepcopy(current.system)
            outcome=perform_action(result,action,assets,elements=current_elements)
            if not outcome["valid"]:rejected[outcome["reason"]]+=1;continue
            signature,_=state_signature(result,result.logical_snapshot(),semantics)
            existing=signatures.get(signature)
            target=states[existing]["id"] if existing is not None else None
            trace=state["trace"]+[{"humanAction":action}]
            if target is None and state["depth"]<max_depth:target,_=admit(result,state["depth"]+1,trace,action["kind"]+" / "+action["gesture"]+" / "+action["owner"].rsplit("/",1)[-1])
            elif target is not None:states[existing]["mergedWitnessCount"]+=1
            changes={k:v for k,v in result.state.items() if state["overrides"].get(k)!=v and k!="__animations"}
            # Several hit cells can execute the same semantic branch. Keep
            # all coordinate witnesses on one edge, including external effects.
            edge_key=json.dumps([action["owner"],action["kind"],target,signature,result.events],sort_keys=True,ensure_ascii=False)
            witness={"gesture":action["gesture"],"sample":action["sample"]}
            if edge_key in edges:edges[edge_key]["witnesses"].append(witness);continue
            edge={"from":state["id"],"to":target,"trigger":outcome["executed"][-1],"triggers":list(dict.fromkeys(outcome["executed"])),
                  "owner":action["owner"],"action":action["kind"],"gesture":action["gesture"],"sample":action["sample"],
                  "intent":action["intent"],"timeline":outcome["timeline"],"events":result.events,"changes":changes,
                  "bounded":target is None,"witnesses":[witness],"noVisualChange":target==state["id"]}
            edges[edge_key]=edge;diagnostics.update(result.issues)
        transitions.extend(edges.values())
        processed+=1
        if processed%16==0:print(json.dumps({'exploredStates':processed,'admittedStates':len(states),'gestureSamples':attempted}),flush=True)
        if budget_exceeded:break
    flat=[{**e,"trigger":trigger} for e in transitions for trigger in e["triggers"]]
    coverage=coverage_report(model,flat,max_states,max_depth,budget_exceeded or any(e["bounded"] for e in transitions))
    coverage.update(maxGestureSamples=max_gestures,attemptedGestureSamples=attempted,gestureBudgetExceeded=budget_exceeded,
                    requiredJourneys=required_results,
                    unexpandedStates=([state['id']] if budget_exceeded else [])+[s['id'] for s in queue])
    coverage.update(boundedTransitions=sum(e["bounded"] for e in transitions),humanActions=len(transitions),
                    coordinateWitnesses=sum(len(e["witnesses"]) for e in transitions),rejectedGestures=dict(rejected),
                    mergedWitnesses=sum(s["mergedWitnessCount"] for s in states))
    return {"interactionMode":"human-gesture-transactions","states":states,"transitions":transitions,"semantics":semantics,
            "diagnostics":diagnostics,"coverage":coverage}


def replay_human_state(model,width,height,state,assets,verify_hit=True,cache=None):
    trace=state['trace'];start=0;rt=None
    if cache is not None:
        for length in range(len(trace),-1,-1):
            key=json.dumps(trace[:length],sort_keys=True)
            if key in cache:
                saved=cache[key];rt=Runtime(model,width,height,copy.deepcopy(saved.state),time_ms=saved.time_ms)
                rt.system=copy.deepcopy(saved.system);rt.issues=dict(saved.issues);start=length;break
    if rt is None:rt=initialize(model,width,height)
    if cache is not None and start==0 and '[]' not in cache:
        saved=Runtime(model,width,height,copy.deepcopy(rt.state),time_ms=rt.time_ms)
        saved.system=copy.deepcopy(rt.system);saved.issues=dict(rt.issues);cache['[]']=saved
    for index,step in enumerate(trace[start:],start):
        outcome=perform_action(rt,step["humanAction"],assets,verify_hit=verify_hit)
        if not outcome["valid"]:
            rt.issue("replay:"+step["humanAction"]["owner"],"目标设备交互路径不可执行："+outcome["reason"])
            break
        if cache is not None:
            saved=Runtime(model,width,height,copy.deepcopy(rt.state),time_ms=rt.time_ms)
            saved.system=copy.deepcopy(rt.system);saved.issues=dict(rt.issues)
            cache[json.dumps(trace[:index+1],sort_keys=True)]=saved
    return rt

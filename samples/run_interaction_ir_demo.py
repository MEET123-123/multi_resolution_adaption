"""Run symbolic rules and export a resumable session, without any native effects."""
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from multires_app.engine.scene_engine import load_model
from multires_app.interaction.interaction_ir import compile_interactions,InteractionMachine


def run(output):
    model=load_model(ROOT/'samples/generic-interactions');ir=compile_interactions(model)
    ids={n['attributes']['name']:uid for uid,n in model['nodes'].items() if n['tag']=='Button'}
    machine=InteractionMachine(ir);steps=[]
    for name in ['next','next','next','settings','choose','back','home']:
        machine.dispatch('pointer.up',ids[name]);steps.append({'event':name,'values':dict(machine.values)})
    output.mkdir(parents=True,exist_ok=True)
    for name,data in [('interaction-ir.json',ir),('session.json',machine.save()),('trace.json',steps)]:
        (output/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    recovered=InteractionMachine.restore(ir,json.loads((output/'session.json').read_text(encoding='utf-8')))
    assert recovered.snapshot()==machine.snapshot()
    return {'output':str(output.resolve()),'pages':[s['values']['page'] for s in steps],
            'choiceAfterReturn':machine.lookup('choice'),'restored':True,'nativeEffectsExecuted':False}


if __name__=='__main__':
    print(json.dumps(run(Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'theme/interaction-ir-demo'),ensure_ascii=False,indent=2))

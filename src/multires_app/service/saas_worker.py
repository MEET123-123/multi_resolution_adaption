"""Isolated conversion subprocess. Invoked by saas_jobs, never from user shell text."""
import argparse
import json
import os
from pathlib import Path


def readiness_summary(output):
    from multires_app.tools.release_readiness import assess
    project=json.loads((output/'project/project.json').read_text(encoding='utf-8'));report=assess(project)
    (output/'readiness.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return {'reviewCount':report['projectChecks']['layoutReviewCount'],
            'explorationBounded':report['projectChecks']['explorationBounded'],'automaticDeliveryReady':False}


def run(root,operation,revision,previous,payload):
    if os.name=='posix':
        import resource
        resource.setrlimit(resource.RLIMIT_AS,(2*1024**3,2*1024**3))
        resource.setrlimit(resource.RLIMIT_CPU,(180,180))
    if operation=='convert':
        from multires_app.tools.theme_adapter import adapt_theme
        from multires_app.tools.batch_convert import source_profile
        config=json.loads((root/'config.json').read_text(encoding='utf-8'))
        source=root/'source.hwt';out=root/'revisions'/'0'
        from multires_app.engine.archive_safety import validate_archive
        validate_archive(source)
        profile=source_profile(source)
        target={'resolutionPx':{'width':config['width'],'height':config['height']},'adaptation':{'mode':config['mode'],'flow':'row-major'}}
        result=adapt_theme(source,profile,target,out,max_states=16,max_depth=2)
        package=Path(result['package']);package.replace(out/'adapted.hwt')
        return {'scope':'lockscreen adaptation candidate','sourceUnchanged':result['sourceUnchanged'],
                'nativeDeviceTest':False,'target':config,**readiness_summary(out)}
    current=root/'revisions'/str(previous);output=root/'revisions'/str(revision)
    path=current/'project/project.json';project=json.loads(path.read_text(encoding='utf-8'))
    target=project['devices'][0]['profile']['id']
    if operation=='match':
        from multires_app.preview.reference_matching import match_reference
        return match_reference(project,target,'state-000',(root/'payloads'/payload).read_bytes(),root/'revisions/0/project')
    if operation=='apply':
        from multires_app.preview.layout_refinement import write_back
        from multires_app.pipeline.scene_pipeline import export_package
        proposal=json.loads((root/'payloads'/payload).read_text(encoding='utf-8'))
        generated=output/'project';generated.mkdir(parents=True,exist_ok=True)
        draft=generated/'project.json';draft.write_text(json.dumps(project,ensure_ascii=False),encoding='utf-8')
        report=write_back(draft,proposal);export_package(draft,target,output/'adapted.hwt')
        return {'scope':'reviewed layout candidate','nativeDeviceTest':False,'changedComponents':report['changedComponents'],**readiness_summary(output)}
    raise ValueError('unknown worker operation')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True);parser.add_argument('--operation',choices=['convert','match','apply'],required=True)
    parser.add_argument('--revision',type=int,required=True);parser.add_argument('--previous',type=int,required=True);parser.add_argument('--payload');parser.add_argument('--result',type=Path,required=True)
    args=parser.parse_args()
    try:result=run(args.root,args.operation,args.revision,args.previous,args.payload)
    except ValueError as exc:result={'workerError':str(exc)[:240]}
    args.result.write_text(json.dumps(result,ensure_ascii=False,allow_nan=False),encoding='utf-8')

"""Bounded subprocess steps for the local theme workbench."""
import argparse
import json
from pathlib import Path
import traceback

from multires_app.preview.workbench import clone_scene, project_summary, save_json, target_profiles, relocate_resource_paths


def run(workspace,identity,taskid):
    jobroot=workspace/'jobs'/identity;directory=jobroot/taskid;task=json.loads((directory/'task.json').read_text(encoding='utf8'))
    output=directory/'output'/'scenes';operation=task['operation'];payload=task['payload'];warnings=[]
    from multires_app.pipeline.scene_pipeline import create_project, export_script, export_package
    if operation=='analyze':
        from multires_app.engine.archive_safety import validate_archive
        from multires_app.tools.batch_convert import source_profile
        source=jobroot/'source.hwt';print('检查主题包与源画布…',flush=True);validate_archive(source)
        profile=source_profile(source)
        if task['config']['sourceDimensions']:profile['resolutionPx']=task['config']['sourceDimensions'];profile['note']='工作台手工指定源画布'
        profiles=target_profiles(task['config'],profile)
        print('探索人工交互状态、聚类并求解初始布局…',flush=True)
        try:create_project(source,profile,profiles,output,task.get('algorithmSpec','anchor'),task['config']['maxStates'],task['config']['maxDepth'])
        except ValueError as exc:
            if not (output/'project.json').is_file():raise
            # The analysis is useful even if the final native-script compiler
            # rejects a state condition. Export remains an explicit later step.
            warnings.append('分析已保存，初始脚本导出需复核：'+str(exc))
            print(warnings[-1],flush=True)
    else:
        previous=workspace/'projects'/task['previousSlug']/'scenes';clone_scene(previous,output)
        if operation=='strategy':
            from multires_app.pipeline.scene_pipeline import load_scene_algorithm
            from multires_app.pipeline.scene_clusters import schedule_device
            from multires_app.pipeline.scene_analysis import attach_analysis
            from multires_app.pipeline.layout_storage import compact_layout_storage, externalize_anchor_evidence
            from multires_app.preview.scene_previewer import build_scene_preview
            project=json.loads((output/'project.json').read_text(encoding='utf8'));project['anchorIRs']={};project['model']['assetNames']=list(project['assets'])
            algorithm=load_scene_algorithm(task.get('algorithmSpec','anchor'))
            for device in project['devices']:
                strategy=payload['strategy'];old=device['profile'].setdefault('adaptation',{}).get('anchorLayout',{})
                device['profile']['adaptation']['anchorLayout']={**old,**{k:v for k,v in strategy.items() if k!='keepConfirmed'}}
                if not strategy['keepConfirmed']:device.pop('manualComponentAnchors',None)
                print('按新策略求解：'+device['profile']['id'],flush=True);schedule_device(project,device,algorithm)
            project['algorithm']=getattr(algorithm,'name',task.get('algorithmSpec','anchor'));project['algorithmSpec']=task.get('algorithmSpec','anchor')
            project['model'].pop('assetNames',None)
            compact_layout_storage(project);attach_analysis(project,output);externalize_anchor_evidence(project)
            save_json(output/'project.json',{k:v for k,v in project.items() if not k.startswith('_')});build_scene_preview(project,output/'preview.html')
            save_json(output/'layout-clusters.json',{d['profile']['id']:d.get('layoutClusters',{}) for d in project['devices']})
            for device in project['devices']:
                target=device['profile']['id'];print('编译新布局脚本：'+target,flush=True)
                (output/'scripts').mkdir(exist_ok=True);export_script(project,target,output/'scripts'/(target+'.xml'))
            (output/'delivery.json').unlink(missing_ok=True)
        elif operation=='generate':
            from multires_app.preview.layout_refinement import write_back
            proposal=payload.get('proposal')
            if proposal:
                print('校验参考图布局并写回组件变换…',flush=True);write_back(output/'project.json',proposal)
            project=json.loads((output/'project.json').read_text(encoding='utf8'));known={d['profile']['id'] for d in project['devices']}
            if not set(payload['targets'])<=known:raise ValueError('导出设备不存在')
            result=[]
            for target in payload['targets']:
                print('生成脚本与资源：'+target,flush=True);script=output/'scripts'/(target+'.xml');script.parent.mkdir(exist_ok=True)
                report=export_script(project,target,script)
                item={'target':target,'script':'scripts/'+target+'.xml','patch':'scripts/'+target+'.patch.json',
                    'resources':'scripts/'+target+'.resources.json','changedAttributes':report.get('changedAttributes')}
                if payload.get('includePackage',True):
                    print('打包 HWT：'+target,flush=True);export_package(output/'project.json',target,output/'packages'/(target+'.hwt'))
                    item['package']='packages/'+target+'.hwt'
                result.append(item)
            save_json(output/'delivery.json',{'outputs':result,'nativeDeviceTest':False,'proposalApplied':bool(proposal)})
        else:raise ValueError('未知处理步骤')
    project=json.loads((output/'project.json').read_text(encoding='utf8'));slug=identity+'-r'+str(task['revision'])
    relocate_resource_paths(output,output,workspace/'projects'/slug/'scenes')
    summary=project_summary(project,slug);save_json(output/'workbench-analysis.json',summary)
    save_json(output/'workbench-strategies.json',{'source':'scene clustering and anchor constraints',
        'devices':[{k:d[k] for k in ('id','layouts','summary')} for d in summary['devices']]})
    print('处理完成，准备发布新版本',flush=True)
    return {'warnings':warnings,'states':len(project['graph']['states']),'devices':len(project['devices']),'components':len(project['components'])}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--workspace',type=Path,required=True);parser.add_argument('--job',required=True);parser.add_argument('--task',required=True)
    args=parser.parse_args();directory=args.workspace/'jobs'/args.job/args.task
    try:result=run(args.workspace,args.job,args.task)
    except Exception as exc:traceback.print_exc();result={'error':str(exc)[:800]}
    save_json(directory/'result.json',result)


if __name__=='__main__':main()

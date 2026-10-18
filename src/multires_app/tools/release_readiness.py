"""Evidence-based readiness report; never equates unit tests with device acceptance."""
import argparse
import json
from pathlib import Path


def assess(project):
    reviews=sum(len(l.get('adaptationReview',[])) for d in project['devices'] for l in d['layouts'])
    coverage=project['graph']['coverage'];bounded=bool(coverage.get('truncated') or coverage.get('boundedTransitions') or coverage.get('gestureBudgetExceeded'))
    return {'productTarget':'customer-facing online theme adaptation','stage':'private-preview','productionReady':False,
            'automaticDeliveryReady':False,
            'implemented':['nine-grid anchors and nested containers','Kiwi required/soft constraints and bounded collision-direction search',
                           'source-preserving XML patch audit','source-guided adaptive layouts','ordered uniform two-column packing','datetime whitespace scoring',
                           'reviewed offline layout JSON','local reference-image bbox proposals','archive intake budgets',
                           'hashed client API keys','owned jobs/tasks/artifacts','bounded queue and process timeout',
                           'immutable result revisions and rollback','scoped expiring preview tickets'],
            'projectChecks':{'archiveIntakePassed':bool(project.get('archiveValidation',{} ) and project['archiveValidation'].get('passed')),
                             'layoutReviewCount':reviews,'diagnosticCount':len(project.get('diagnostics',{})),
                             'explorationBounded':bounded,'nativeDeviceEvidence':False,
                             'bindingReviewCount':project.get('sceneAnalysis',{}).get('bindings',{}).get('statuses',{}).get('needs-review',0),
                             'bindingViolationCount':len(project.get('sceneAnalysis',{}).get('bindings',{}).get('violations',[])),
                             'interactionIRAvailable':bool(project.get('sceneAnalysis'))},
            'releaseBlockers':['目标设备与 HarmonyOS 版本的真实安装/锁屏/解锁/旋转/持久化验收尚无证据',
                              '复杂原包的组件边界、布局警告和未探索交互仍需人工复核',
                              '公网 TLS、网络隔离、容器/文件系统硬配额、限流与恶意文件扫描尚未部署验证',
                              '当前 SQLite 单实例队列尚未经过并发、崩溃恢复与容量压测；暂不支持多个 API worker',
                              '数据保留/删除、备份恢复、审计监控、依赖漏洞扫描与资源商用授权尚未形成验收闭环'],
            'deliveryPolicy':'当前只能作为需人工验收的适配候选；不得把离线测试通过解释为原生兼容或商用认证。'}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('project',type=Path);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();report=assess(json.loads(args.project.read_text(encoding='utf-8')))
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'stage':report['stage'],'productionReady':report['productionReady'],'projectChecks':report['projectChecks']},ensure_ascii=False))


if __name__=='__main__':
    main()

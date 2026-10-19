"""Pluggable batch evaluation, candidate iteration and reviewed regressions."""
import argparse
import json
from datetime import datetime
from pathlib import Path
from multires_app.evaluation.core import load_plugin, write_json, validate_record
from multires_app.evaluation.workflow import run, parallel_run, iter_records, review, freeze, regression
from multires_app.evaluation.gate import compare
from multires_app.evaluation.refiners import RescheduleRefiner


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('scan', 'iterate'):
        p = sub.add_parser(name)
        p.add_argument('--root', type=Path, default=Path('theme/adaptation'))
        p.add_argument('--project', type=Path)
        p.add_argument('--only', nargs='+')
        p.add_argument('--target', action='append')
        p.add_argument('--out', type=Path)
        p.add_argument('--config', type=Path)
        p.add_argument('--plugin', action='append', default=[])
        p.add_argument('--evidence-budget', type=int, default=20)
        p.add_argument('--replay-traces', type=int, default=0)
        p.add_argument('--responsive', action='store_true')
        p.add_argument('--workers', type=int, choices=[1, 2, 3, 4], default=1)
        p.add_argument('--resume', action='store_true', help='verify completed project checkpoints and resume a batch run')
        if name == 'iterate':
            p.add_argument('--rounds', type=int, choices=range(1, 4), default=1)
            p.add_argument('--refiner', help='trusted module:factory; defaults to existing layout algorithm')
            p.add_argument('--algorithm', help='existing layout_scene plugin specification')
    p = sub.add_parser('compare')
    p.add_argument('baseline', type=Path)
    p.add_argument('candidate', type=Path)
    p.add_argument('--out', type=Path, required=True)
    p = sub.add_parser('review')
    p.add_argument('run', type=Path)
    p.add_argument('--case', required=True)
    p.add_argument('--label', choices=['confirm', 'dismiss', 'unknown'], required=True)
    p.add_argument('--reviewer', required=True)
    p.add_argument('--notes', default='')
    p.add_argument('--cause', default='')
    p.add_argument('--preference', choices=['A', 'B', 'both', 'neither', ''], default='')
    p = sub.add_parser('freeze')
    p.add_argument('run', type=Path)
    p.add_argument('--case', required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--expected', choices=['pass', 'fail', 'review', 'unknown'], default='pass')
    p.add_argument('--split', choices=['train', 'validation', 'test'], default='validation')
    p = sub.add_parser('regressions')
    p.add_argument('run', type=Path)
    p.add_argument('--fixtures', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p = sub.add_parser('validate')
    p.add_argument('file', type=Path)
    args = parser.parse_args()
    if args.command == 'validate':
        errors, count = [], 0
        for index, line in enumerate(args.file.read_text(encoding='utf8').splitlines(), 1):
            if not line.strip():
                continue
            count += 1
            try:
                validate_record(json.loads(line))
            except (TypeError, ValueError) as exc:
                errors.append({'line': index, 'error': str(exc)})
        print(json.dumps({'records': count, 'errors': errors}, ensure_ascii=False))
        return 1 if errors or not count else 0
    if args.command in {'scan', 'iterate'}:
        options = json.loads(args.config.read_text(encoding='utf8')) if args.config else {}
        options.update(evidenceBudget=args.evidence_budget, replayTraces=args.replay_traces,
                       responsive=args.responsive)
        if args.target:
            options['targets'] = args.target
        if getattr(args, 'algorithm', None):
            options['algorithm'] = args.algorithm
        if args.evidence_budget < 0 or args.replay_traces < 0:
            parser.error('budgets must be nonnegative')
        paths = [args.project] if args.project else sorted(args.root.glob('*/scenes/project.json'))
        if args.only:
            paths = [p for p in paths if p.parent.parent.name in args.only]
        out = args.out or Path('artifacts/evaluation/runs') / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        refiner = (load_plugin(args.refiner) if args.refiner else RescheduleRefiner()) if args.command == 'iterate' else None
        extra = [load_plugin(s) for s in args.plugin]
        if args.command == 'scan':
            result, code = parallel_run(paths, out, options, extra, args.workers, args.resume)
        else:
            if args.resume:
                parser.error('resume applies to scan batches; iterate uses immutable new runs')
            if args.workers != 1:
                parser.error('iterate currently uses one worker; shard themes with --only')
            result, code = run(paths, out, options, extra, refiner, args.rounds)
        print(json.dumps({'report': str((out / 'report.html').resolve()), 'summary': result, 'exitCode': code}, ensure_ascii=False))
        return code
    if args.command == 'review':
        print(json.dumps(review(args.run, args.case, args.label, args.reviewer, args.notes, args.cause, args.preference), ensure_ascii=False))
        return 0
    if args.command == 'freeze':
        freeze(args.run, args.case, args.out, args.expected, args.split)
        return 0
    if args.command == 'regressions':
        result = regression(args.run, sorted(args.fixtures.glob('*.json')))
        write_json(args.out, result)
        return 0 if result['passed'] else 1
    before = json.loads((args.baseline / 'manifest.json').read_text(encoding='utf8'))
    after = json.loads((args.candidate / 'manifest.json').read_text(encoding='utf8'))
    # Gate requires identical inputs, devices and evaluator configuration.
    compatible = before['evaluators'] == after['evaluators'] and before['options'] == after['options'] and sorted(
        (e['inputIdentity'], json.dumps(e['devices'], sort_keys=True)) for e in before['projects']) == sorted(
        (e['inputIdentity'], json.dumps(e['devices'], sort_keys=True)) for e in after['projects'])
    def compact(run):
        for r in iter_records(run):
            yield {k: r[k] for k in ('id', 'rule', 'status', 'residual', 'plugin')} | {'evidence': {
                'evaluation': {'score': r['evidence'].get('evaluation', {}).get('score')},
                'failures': [{k: f[k] for k in ('component', 'first', 'last', 'axis', 'edge') if k in f}
                             for f in r['evidence'].get('failures', []) if isinstance(f, dict)]}}
    result = compare(list(compact(args.baseline)), list(compact(args.candidate))) if compatible else {
        'regressionPassed': False, 'error': 'inputs/devices/evaluator/options mismatch; runs are not comparable'}
    write_json(args.out, result)
    return 0 if result['regressionPassed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

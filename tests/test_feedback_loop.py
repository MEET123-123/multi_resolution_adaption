import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from multires_app.evaluation.core import Context, Observation, evaluate, validate_record, validate_options
from multires_app.evaluation.checks import GeometryEvaluator
from multires_app.evaluation.gate import compare, exit_code
from multires_app.evaluation.responsive import sample_graph
from multires_app.evaluation.workflow import run, read_records, review, freeze, regression
from multires_app.layout.center_intent import center_contracts
from multires_app.layout.anchor_ir import build_ir
from multires_app.layout.anchor_layout import AnchorThemeLayout
from multires_app.pipeline.scene_clusters import transformed_box
from tests.test_anchor_layout import comp, ctx, SOURCE


def project():
    identity = {'sx': 1., 'sy': 1., 'tx': 0., 'ty': 0.}
    source = {'state': 's', 'rows': [[0, 400, 500, 280, 100, 1, -1, '', 0]], 'transforms': {'clock': identity}}
    target = {'state': 's', 'rows': [[0, 400, 500, 280, 100, 1, -1, '', 0]],
              'transforms': {'clock': {**identity, 'tx': 100.}}, 'adaptationPlan': {}}
    return {'source': 'source.hwt', 'sourceProfile': SOURCE,
        'model': {'variables': {}, 'nodes': {'n': {'id': 'n', 'tag': 'Image', 'attributes': {}, 'children': [], 'parent': None}}},
        'components': [{'id': 'clock', 'name': 'clock', 'role': 'header', 'members': ['n']}],
        'nodeOrder': ['n'], 'nodeComponents': {'n': 'clock'}, 'assets': {}, 'assetOrder': [],
        'graph': {'states': [{'id': 's', 'trace': []}], 'transitions': [], 'coverage': {}},
        'sourceDevice': {'viewport': {'width': 1080, 'height': 2400}, 'layouts': [source]},
        'devices': [{'profile': {'id': 'tablet', 'resolutionPx': {'width': 1600, 'height': 1000}},
                     'viewport': {'width': 1600, 'height': 1000}, 'layouts': [target]}]}


class FeedbackTests(unittest.TestCase):
    def rows(self, p):
        return evaluate(Context(p, Path('themes/demo/scenes/project.json')), [GeometryEvaluator()])

    def test_center_is_inferred_without_solver_flags(self):
        p = project()
        rows = self.rows(p)
        bad = [r for r in rows if r['rule'] == 'GEO.GROUP_CENTER']
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0]['status'], 'fail')
        self.assertEqual(bad[0]['evidence']['expected'], 800)
        self.assertEqual(bad[0]['evidence']['actual'], 640)
        self.assertEqual(bad[0]['axis'], 'x')

    def test_center_can_be_fixed_and_compared_with_stable_case_id(self):
        p = project()
        before = self.rows(p)
        p['devices'][0]['layouts'][0]['transforms']['clock']['tx'] = 260
        after = self.rows(p)
        result = compare(before, after)
        self.assertTrue(result['regressionPassed'], result)
        self.assertTrue(result['improvements'])

    def test_center_y_and_source_offset_and_manual_rules(self):
        p = project()
        src = p['sourceDevice']['layouts'][0]['rows'][0]
        src[2] = 1150
        self.assertEqual({r['axis'] for r in self.rows(p) if r['rule'] == 'GEO.GROUP_CENTER'}, {'x', 'y'})
        src[1] = 300
        self.assertEqual({r['axis'] for r in self.rows(p) if r['rule'] == 'GEO.GROUP_CENTER'}, {'y'})
        p['components'][0]['layout'] = {'anchor': 'left'}
        self.assertFalse(center_contracts(p, p['devices'][0], p['devices'][0]['layouts'][0]))

    def test_near_center_groups_are_required_after_resize(self):
        c = comp('clock', (402, 500, 280, 100))
        context = ctx(1600, 1000)
        out = AnchorThemeLayout().layout_scene([c], SOURCE, {'resolutionPx': {'width': 1600, 'height': 1000}}, context)
        b = transformed_box(c['bounds'], out['clock'])
        self.assertAlmostEqual(b['x']+b['w']/2, 800, places=4)
        self.assertTrue(any('x' in g['centerRequiredAxes'] for g in context['adaptationPlan']['groups']))

    def test_transient_center_does_not_invent_permanent_anchor(self):
        c = comp('clock', (400, 500, 280, 100))
        one = {'clock': c['bounds']}
        two = {'clock': {**c['bounds'], 'x': 600}}
        ir = build_ir([c], {'width': 1080, 'height': 2400}, ctx(flowSamples=[(one, one), (two, two)]), {})
        self.assertTrue(all('x' not in g.get('centerRequiredAxes', []) for g in ir['groups']))

    def test_footer_distribution_preserves_optical_center_despite_transparent_padding(self):
        cs=[comp(str(i),(90+i*360,2200,180,100),role='footer') for i in range(3)]
        visuals={c['id']:c['bounds'] for c in cs}
        visuals['1']={**visuals['1'],'x':460,'w':172}
        context=ctx(1600,1000,sourceStableVisualBounds=visuals,stableVisualBounds=visuals)
        result=AnchorThemeLayout().layout_scene(cs,SOURCE,{'resolutionPx':{'width':1600,'height':1000}},context)
        for i,c in enumerate(cs):
            b=transformed_box(visuals[c['id']],result[c['id']])
            self.assertAlmostEqual(b['x']+b['w']/2,1600*(i+.5)/3,places=4)

    def test_plugin_exception_is_unknown_and_other_plugin_runs(self):
        class Broken:
            name, version = 'broken', '1'
            def evaluate(self, c):
                yield Observation('PARTIAL', 'pass')
                raise ValueError('broken')
        rows = evaluate(Context(project(), Path('demo/scenes/project.json')), [Broken(), GeometryEvaluator()])
        self.assertFalse(any(r['rule'] == 'PARTIAL' for r in rows))
        self.assertTrue(any(r['status'] == 'unknown' for r in rows))
        self.assertTrue(any(r['rule'] == 'GEO.GROUP_CENTER' for r in rows))

    def test_schema_rejects_nan_invalid_status_and_confidence(self):
        for observation in [Observation('a', 'maybe'), Observation('a', 'pass', confidence=2),
                            Observation('a', 'pass', residual=float('nan'))]:
            with self.assertRaises(ValueError):
                observation.validate()

    def test_unsafe_artifact_paths_and_unbounded_options_are_rejected(self):
        row = self.rows(project())[0]
        for path in ['../../outside.png', 'C:/outside.png', '/outside.png']:
            with self.assertRaises(ValueError):
                validate_record({**row, 'artifacts': {'source': path}})
        for options in [{'replayTraces': 100000}, {'centerTolerance': float('nan')}, {'responsiveAxes': ['depth']}]:
            with self.assertRaises(ValueError):
                validate_options(options)

    def test_rendering_huge_geometry_is_bounded_and_marked_unknown(self):
        from multires_app.evaluation.report import evidence
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            p = project()
            p['devices'][0]['layouts'][0]['rows'][0][3] = 10**9
            c = Context(p, Path(tmp)/'scenes/project.json')
            row = next(r for r in self.rows(p) if r['rule'] == 'GEO.GROUP_CENTER')
            evidence(c, row, Path(tmp)/'case')
            report = json.loads((Path(tmp)/'case/evidence.json').read_text(encoding='utf8'))
            self.assertEqual(report['rendering']['target']['status'], 'unknown')
            self.assertTrue(report['rendering']['target']['omitted'])

    def test_unknown_is_not_pass_and_severity_does_not_depend_on_sample_count(self):
        rows = self.rows(project())
        self.assertEqual(exit_code(rows), 1)
        self.assertEqual(exit_code([{'status': 'unknown'}]), 2)
        self.assertEqual(exit_code([]), 2)

    def test_reproducible_records(self):
        self.assertEqual(self.rows(project()), self.rows(project()))

    def test_new_failure_and_missing_check_reject_candidate(self):
        before = self.rows(project())
        after = copy.deepcopy(before)
        after[0]['status'] = 'fail'
        self.assertFalse(compare(before, after)['regressionPassed'])
        self.assertFalse(compare(before, after[1:])['regressionPassed'])

    def test_responsive_uniform_sampling_finds_isolated_failure(self):
        result = sample_graph(lambda w: {'relations': {'inside': not 145 <= w <= 155}}, 100, 200, steps=5, budget=17)
        self.assertTrue(result['anomalies'])
        self.assertTrue(any(s['value'] == 150 and not s['relations']['inside'] for s in result['samples']))
        self.assertFalse(result['exhaustive'])

    def test_responsive_probe_error_and_budget_are_explicit(self):
        def probe(w):
            if w == 150:
                raise ValueError('solver conflict')
            return {'relations': {'a': True}}
        result = sample_graph(probe, 100, 200, steps=3, budget=3)
        self.assertFalse(result['samplingComplete'])
        self.assertTrue(any(s['status'] == 'unknown' for s in result['samples']))
        with self.assertRaises(ValueError):
            sample_graph(probe, 100, 200, budget=5, breakpoints=[120, 140, 160])

    def test_immutable_run_review_and_frozen_regression(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root = Path(tmp)
            path = root / 'themes' / 'demo' / 'scenes' / 'project.json'
            path.parent.mkdir(parents=True)
            p = project()
            source = root / 'source.hwt'
            source.write_bytes(b'original source bytes')
            p['source'] = str(source)
            path.write_text(json.dumps(p), encoding='utf8')
            original = path.read_bytes(), source.read_bytes()
            out = root / 'run'
            with patch('multires_app.evaluation.workflow.plugins', return_value=[GeometryEvaluator()]):
                summary, code = run([path], out, {'evidenceBudget': 0, 'passSamples': 0})
                with self.assertRaises(ValueError):
                    run([path], out)
            self.assertEqual(original, (path.read_bytes(), source.read_bytes()))
            self.assertTrue(summary['sourceUnchanged'])
            bad = next(r for r in read_records(out) if r['rule'] == 'GEO.GROUP_CENTER')
            review(out, bad['id'], 'confirm', 'test-reviewer')
            fixture = root / 'fixtures' / 'one.json'
            freeze(out, bad['id'], fixture, expected='fail')
            self.assertTrue(regression(out, [fixture])['passed'])
            with self.assertRaises(ValueError):
                freeze(out, bad['id'], fixture)

    def test_real_center_repair_exports_audited_candidate_and_preserves_source(self):
        import zipfile
        from PIL import Image
        from multires_app.pipeline.scene_pipeline import create_project
        from multires_app.evaluation.refiners import RescheduleRefiner
        from multires_app.evaluation.input_checks import file_sha
        from io import BytesIO
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root = Path(tmp)
            source = root / 'original.hwt'
            bitmap = BytesIO()
            Image.new('RGBA', (280, 100), '#ffffff').save(bitmap, format='PNG')
            xml = '<Lockscreen version="1" screenWidth="1080" screenHeight="2160"><Group x="400" y="200"><Image src="clock.png" w="280" h="100" /></Group></Lockscreen>'
            with zipfile.ZipFile(source, 'w') as archive:
                archive.writestr('unlock/lockscreen/manifest.xml', xml)
                archive.writestr('unlock/lockscreen/clock.png', bitmap.getvalue())
            before = file_sha(source)
            directory = root / 'adaptation' / 'mock' / 'scenes'
            create_project(source, {'resolutionPx': {'width': 1080, 'height': 2160}},
                {'profiles': [{'id': 'tablet', 'resolutionPx': {'width': 1600, 'height': 1000}, 'orientations': ['landscape']}]},
                directory, max_states=2, max_depth=1)
            path = directory / 'project.json'
            p = json.loads(path.read_text(encoding='utf8'))
            for l in p['devices'][0]['layouts']:
                for t in l['transforms'].values():
                    t['tx'] += 40
            path.write_text(json.dumps(p), encoding='utf8')
            cached = path.read_bytes()
            out = root / 'run'
            summary, code = run([path], out, {'evidenceBudget': 1, 'passSamples': 0}, refiner=RescheduleRefiner())
            iterations = json.loads((out / 'iterations.json').read_text(encoding='utf8'))
            self.assertTrue(iterations[0]['accepted'], iterations)
            self.assertTrue(iterations[0]['exports'][0]['writebackValidation']['passed'])
            self.assertTrue((out / 'candidates/mock/1/preview.html').is_file())
            self.assertEqual(before, file_sha(source))
            self.assertEqual(cached, path.read_bytes())
            self.assertTrue(summary['sourceUnchanged'])


if __name__ == '__main__':
    unittest.main()

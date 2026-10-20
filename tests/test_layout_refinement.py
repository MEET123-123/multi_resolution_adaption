import copy
import io
import json
import tempfile
import unittest
import shutil
import subprocess
from unittest.mock import patch
from pathlib import Path

import numpy as np
from PIL import Image
from multires_app.preview.layout_refinement import export_request,validate_and_apply,write_back,geometry_digest
from multires_app.preview.reference_matching import match_template,match_reference
from multires_app.pipeline.scene_pipeline import create_project, compiled_transform
from multires_app.pipeline.scene_clusters import same_geometry, describe_layout

ROOT=Path(__file__).resolve().parents[1]


class LayoutRefinementTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'tests');self.root=Path(self.tmp.name)
        source=self.root/'source';lock=source/'unlock/lockscreen';lock.mkdir(parents=True)
        script='<Lockscreen screenWidth="400"><Image src="art.png" x="30" y="80" w="100" h="120"/><Text text="Label" x="230" y="300" w="80" h="30" size="20"/></Lockscreen>'
        (lock/'manifest.xml').write_text(script,encoding='utf-8')
        rng=np.random.default_rng(13);Image.fromarray(rng.integers(0,256,(120,100,3),dtype=np.uint8)).save(lock/'art.png')
        create_project(source,{'resolutionPx':{'width':400,'height':400}},
                       {'profiles':[{'id':'square','resolutionPx':{'width':400,'height':400},'orientations':['portrait']}]},self.root/'project',max_states=2,max_depth=1)
        self.path=self.root/'project/project.json';self.project=json.loads(self.path.read_text(encoding='utf-8'))
        self.request=export_request(self.project,'square-portrait','state-000')
    def tearDown(self):self.tmp.cleanup()
    def proposal(self):
        result=copy.deepcopy(self.request['responseTemplate']);item=next(c for c in self.request['components'] if c['role']!='background')
        result['components']=[{'id':item['id'],'bbox':{**item['bbox'],'x':item['bbox']['x']+20}}]
        return result

    def test_offline_json_updates_only_geometry_and_keeps_backup(self):
        proposal=self.proposal();updated,report=validate_and_apply(self.project,proposal)
        self.assertEqual(report['changedComponents'],1);self.assertEqual(updated['model'],self.project['model'])
        before=self.path.read_bytes();report=write_back(self.path,proposal)
        self.assertEqual(Path(report['backup']).read_bytes(),before)
        self.assertTrue((self.path.parent/'scripts/square-portrait.xml').is_file())
        self.assertNotEqual(self.path.read_bytes(),before)

    def test_stale_unknown_invalid_aspect_and_overlap_are_rejected(self):
        for mutate in [lambda p:p.update(baseDigest='stale'),lambda p:p['components'][0].update(id='unknown'),
                       lambda p:p['components'][0]['bbox'].update(w=12),lambda p:p['components'][0]['bbox'].update(x=-99),
                       lambda p:p['components'][0]['bbox'].update(x=float('nan'))]:
            proposal=self.proposal();mutate(proposal)
            with self.assertRaises(ValueError):validate_and_apply(self.project,proposal)
        before=self.path.read_bytes()
        proposal=self.proposal();proposal['components'][0]['bbox']['w']=1
        with self.assertRaises(ValueError):write_back(self.path,proposal)
        self.assertEqual(self.path.read_bytes(),before)

    def test_fft_template_matching_finds_known_location_and_rejects_duplicates(self):
        rng=np.random.default_rng(29);pixels=rng.integers(0,256,(30,24,3),dtype=np.uint8)
        template=Image.fromarray(pixels).convert('RGBA');reference=Image.new('RGB',(160,100),'#273544');reference.paste(template,(81,27))
        match=match_template(reference,template,template.size,scales=(1.,))
        self.assertEqual((match['x'],match['y']),(81,27));self.assertGreater(match['confidence'],.99)
        self.assertGreater(match['peakMargin'],.5)
        reference.paste(template,(20,60));duplicate=match_template(reference,template,template.size,scales=(1.,))
        self.assertLess(duplicate['peakMargin'],.001)

    def test_reference_image_proposes_bbox_without_modifying_project(self):
        reference=Image.new('RGB',(400,400),'#253645')
        with Image.open(self.root/'source/unlock/lockscreen/art.png') as bitmap:reference.paste(bitmap,(150,80))
        stream=io.BytesIO();reference.save(stream,format='PNG')
        before=copy.deepcopy(self.project)
        result=match_reference(self.project,'square-portrait','state-000',stream.getvalue(),self.path.parent)
        self.assertEqual(self.project,before);self.assertEqual(len(result['components']),1)
        self.assertAlmostEqual(result['components'][0]['bbox']['x'],150)
        self.assertAlmostEqual(result['components'][0]['bbox']['y'],80)
        validate_and_apply(self.project,result)
        wrong=io.BytesIO();Image.new('RGB',(800,200)).save(wrong,format='PNG')
        with self.assertRaises(ValueError):match_reference(self.project,'square-portrait','state-000',wrong.getvalue(),self.path.parent)

    def test_matching_anchors_survive_json_and_propagate_atomically(self):
        project=copy.deepcopy(self.project);device=project['devices'][0]
        other=copy.deepcopy(device['layouts'][0]);other['state']='state-copy';device['layouts'].append(other)
        updated,report=validate_and_apply(project,self.proposal())
        cid=self.proposal()['components'][0]['id'];d=updated['devices'][0]
        self.assertEqual(report['affectedStates'],2)
        self.assertEqual(d['layouts'][0]['transforms'][cid],d['layouts'][1]['transforms'][cid])
        persisted=json.loads(json.dumps(updated))
        self.assertTrue(same_geometry(describe_layout(persisted,persisted['devices'][0]['layouts'][0])[cid],persisted['devices'][0]['manualComponentAnchors'][cid]))
        # A component in another state occupies the proposed destination. Reject all changes.
        other_desc=describe_layout(project,other);obstacle=next(c for c in other_desc if c!=cid)
        box=self.proposal()['components'][0]['bbox'];bounds=other_desc[obstacle]['bounds']
        other['transforms'][obstacle]={'sx':1,'sy':1,'tx':box['x']+box['w']-12-bounds['x'],'ty':box['y']-bounds['y']}
        before=copy.deepcopy(project)
        with self.assertRaisesRegex(ValueError,'state-copy'):validate_and_apply(project,self.proposal())
        self.assertEqual(project['devices'],before['devices'])

    def test_reference_reflow_propagates_whole_compatible_layout_and_exports(self):
        project=copy.deepcopy(self.project);device=project['devices'][0]
        compatible=copy.deepcopy(device['layouts'][0]);compatible['state']='compatible';device['layouts'].append(compatible)
        incompatible=copy.deepcopy(device['layouts'][0]);incompatible['state']='incompatible';device['layouts'].append(incompatible)
        request=export_request(project,'square-portrait','state-000');items=[c for c in request['components'] if not c['locked']]
        altered=items[-1]['id']
        index=project['nodeOrder'].index(next(uid for uid,cid in project['nodeComponents'].items() if cid==altered))
        for row in incompatible['rows']:
            if row[0]==index:row[2]+=2
        proposal={**request['responseTemplate'],'origin':'expected-image-matcher','propagationPolicy':'compatible-layout',
                  'components':[{'id':c['id'],'bbox':{**c['bbox'],'x':c['bbox']['x']+10}} for c in items]}
        updated,report=validate_and_apply(project,proposal)
        self.assertIn('incompatible',report['skippedStates'])
        self.assertEqual(report['affectedStates'],2)
        self.assertEqual(updated['devices'][0]['layouts'][2]['transforms'],incompatible['transforms'])
        from multires_app.layout.anchor_validation import validate_export
        validate_export(updated,updated['devices'][0])
        # Export validates containment even when the original edge rules are weak.
        cid=items[0]['id'];updated['devices'][0]['layouts'][0]['transforms'][cid]['tx']=-10000
        with self.assertRaises(ValueError):validate_export(updated,updated['devices'][0])

    def test_multi_file_failure_restores_original_artifacts(self):
        paths=[self.path,self.path.parent/'scripts/square-portrait.xml',self.path.parent/'preview.html']
        before={p:p.read_bytes() for p in paths};replace=Path.replace
        def fail_preview(source,destination):
            if source.name.startswith('refinement-') and source.suffix=='.html':raise OSError('simulated commit failure')
            return replace(source,destination)
        with patch.object(Path,'replace',fail_preview):
            with self.assertRaisesRegex(OSError,'simulated'):write_back(self.path,self.proposal())
        self.assertEqual({p:p.read_bytes() for p in paths},before)
        self.assertFalse(list(self.path.parent.glob('refinement-*')))

    def test_ambiguous_native_state_guards_reject_different_transforms(self):
        project=copy.deepcopy(self.project);device=project['devices'][0]
        other=copy.deepcopy(device['layouts'][0]);other['state']='duplicate-state';device['layouts'].append(other)
        project['graph']['states'].append({**project['graph']['states'][0],'id':other['state']})
        cid=next(iter(other['transforms']));other['transforms'][cid]['tx']+=10
        with self.assertRaisesRegex(ValueError,'同一原生状态条件'):compiled_transform(project,device,cid,'tx')

    def test_browser_digest_matches_python_including_rounding_boundaries(self):
        node=shutil.which('node')
        if not node:
            bundled=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
            if bundled.is_file():node=str(bundled)
        if not node:self.skipTest('Node is required for browser/Python digest compatibility')
        data={'viewport':{'width':1080.0000005,'height':675.125},'components':[
            {'id':'component-b','bbox':{'x':1/128,'y':-.0000005,'w':99.0000005,'h':27.25}},
            {'id':'component-a','bbox':{'x':-.0000004,'y':.0000005,'w':18.3333335,'h':7.1234565}}]}
        bootstrap="globalThis.$=id=>({});globalThis.crypto=require('crypto').webcrypto;const fixture=JSON.parse(process.argv[1]);const stateId='state-000';function device(){return {profile:{id:'tablet-landscape',resolutionPx:{width:2560,height:1600}},viewport:fixture.viewport};}\n"
        code=bootstrap+(ROOT/'src/multires_app/preview/layout_refinement_ui.js').read_text(encoding='utf-8')+"\ncomponentBoxes=()=>fixture.components;localRefinementRequest().then(v=>process.stdout.write(v.baseDigest)).catch(e=>{console.error(e);process.exitCode=1;});"
        result=subprocess.run([node,'-e',code,json.dumps(data)],capture_output=True,text=True,check=True)
        self.assertEqual(result.stdout,geometry_digest('tablet-landscape','state-000',data['viewport'],data['components']))


if __name__=='__main__':unittest.main()

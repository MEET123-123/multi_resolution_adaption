import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


class BatchTests(unittest.TestCase):
    def test_parallel_workers_keep_packages_and_reports_separate(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as tmp:
            theme=Path(tmp)
            for name in ('one.hwt','two.hwt'):
                with zipfile.ZipFile(theme/name,'w') as archive:
                    for path in (ROOT/'samples/scene-theme').rglob('*'):
                        if path.is_file():archive.write(path,path.relative_to(ROOT/'samples/scene-theme').as_posix())
                    if name=='one.hwt':archive.write(ROOT/'samples/scene-theme/unlock/lockscreen/manifest.xml','unlock/ucdlockscreen/manifest.xml')
            subprocess.run([sys.executable,str(ROOT/'multires.py'),'tools','batch-convert','--theme',str(theme),'--workers','2'],cwd=ROOT,check=True,capture_output=True,timeout=60)
            reports=json.loads((theme/'adaptation/batch-summary.json').read_text(encoding='utf-8'))
            self.assertEqual(len(reports),2);self.assertEqual(len({r['slug'] for r in reports}),2)
            self.assertTrue(all(r['status']=='validated' and r['sourceUnchanged'] for r in reports))
            self.assertTrue(all(r['devices']==22 for r in reports))
            variant_record=next(r for r in reports if r['name']=='one.hwt')
            self.assertEqual(len(variant_record['variants']),1)
            self.assertEqual(variant_record['validation']['unadaptedLockscreenVariants'],[])
            package=theme/'adaptation'/variant_record['slug']/'scenes/packages/tablet-mock-16-10-landscape.hwt'
            with zipfile.ZipFile(theme/'one.hwt') as original,zipfile.ZipFile(package) as adapted:
                for manifest in ('unlock/lockscreen/manifest.xml','unlock/ucdlockscreen/manifest.xml'):
                    self.assertNotEqual(original.read(manifest),adapted.read(manifest))


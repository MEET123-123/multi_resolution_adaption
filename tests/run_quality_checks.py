"""Run the real test suite and save machine-readable evidence for release review."""
import json
import platform
import sys
import time
import unittest
from importlib.metadata import version
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
start=time.monotonic();suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_*.py')
result=unittest.TextTestRunner(verbosity=1).run(suite)
report={'passed':result.wasSuccessful(),'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
        'skipped':len(result.skipped),'seconds':round(time.monotonic()-start,3),'python':platform.python_version(),
        'dependencies':{'Pillow':version('Pillow'),'numpy':version('numpy'),'kiwisolver':version('kiwisolver')},'nativeDeviceTest':False,
        'scope':'automated offline tests, ASGI protocol checks, subprocess conversion, isolation and rollback cases'}
output=ROOT/'artifacts/reports/quality-results.json';output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
if not result.wasSuccessful():raise SystemExit(1)

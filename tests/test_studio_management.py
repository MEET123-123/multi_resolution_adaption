"""Regression tests for the non-destructive experiment index and CLI refactor."""
from __future__ import annotations
import contextlib, importlib.util, io, tempfile, unittest
from pathlib import Path
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
def load(path,name):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

manage=load("studio_reverse/manage.py","studio_manager_test")
index=load("scripts/index_artifacts.py","artifact_index_test")
audit=load("scripts/audit_studio_reverse.py","studio_audit_test")

class ExperimentalStructureTests(unittest.TestCase):
    def test_all_dispatch_targets_exist(self):
        for command in manage.SCRIPTS:
            with self.subTest(command=command):
                resolved=manage.resolve(command)
                self.assertTrue(resolved.is_file())
                self.assertTrue(resolved.is_relative_to(ROOT))
    def test_list_is_safe(self):
        out=io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(manage.main(["list"]),0)
        self.assertIn("families",out.getvalue())
        self.assertIn("interactive-legacy",out.getvalue())
    def test_unknown_command(self):
        err=io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(manage.main(["not-a-tool"]),2)
        self.assertIn("Unknown command",err.getvalue())
    def test_launcher_uses_same_interpreter_and_root(self):
        fake=mock.Mock(returncode=0)
        with mock.patch.object(manage.subprocess,"run",return_value=fake) as patched_run:
            with contextlib.redirect_stdout(io.StringIO()):
                result=manage.main(["families","--help"])
            args,kwargs=patched_run.call_args
        self.assertEqual(result,0)
        self.assertEqual(args[0][0],manage.sys.executable)
        self.assertEqual(args[0][-1],"--help")
        self.assertEqual(kwargs["cwd"],ROOT)
        self.assertIn(str(ROOT/"src"),kwargs["env"]["PYTHONPATH"])
    def test_quick_index_marks_nested_subtree_partial(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            (folder/"one").mkdir()
            (folder/"one"/"two").mkdir()
            (folder/"one"/"two"/"important.dat").write_bytes(b"data")
            r=index.summarize(folder,1000)
            self.assertTrue(r["partial"])
    def test_manifest_retains_supervision_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            (folder/"manifest.json").write_text(
                '{"pairs":120,"nativeValidated":false,"supervision":"synthetic-rule"}',
                encoding="utf8")
            m=index.manifests(folder)
            self.assertEqual(m[0]["fields"]["pairs"],120)
            self.assertFalse(m[0]["fields"]["nativeValidated"])
    def test_python_dependency_inspection(self):
        data="import interactive as base\nfrom foo import bar\ndef run(): pass\nif __name__ == '__main__': run()\n"
        r=audit.dependencies(None,data)
        self.assertIn("interactive",r["imports"])
        self.assertIn("run",r["topFunctions"])
        self.assertTrue(r["hasMainGuard"])

if __name__=="__main__":
    unittest.main()

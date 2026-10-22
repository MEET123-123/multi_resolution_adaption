"""Artificial fixtures only: validate the contract without labeling real themes."""
import importlib.util
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("annotation_tasks", ROOT / "annotation_tasks.py")
workflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workflow)


@contextmanager
def temporary_workspace():
    with tempfile.TemporaryDirectory(dir=ROOT / "tests", prefix="tmp-") as name:
        if not Path(name).resolve().is_relative_to(ROOT.resolve()):
            raise RuntimeError("临时目录超出本工作区")
        yield name


class AnnotationWorkflowTests(unittest.TestCase):
    def blank(self):
        task = workflow.read(ROOT / "templates/task.blank.json")
        task["task_id"] = "test-fixture"
        return task

    def group(self, gid, member):
        group = workflow.read(ROOT / "templates/group.blank.json")
        group.update(id=gid, roles=["other"], member_ids=[member], source_bbox_1000=[100, 100, 100, 100],
                     bbox_basis="script", internal_layout="rigid", transform_policy="uniform_affine",
                     offscreen_policy="inside", evidence="人工构造的格式检查夹具，不是真实主题标签")
        return group

    def release_fixture(self, folder):
        task = self.blank()
        for kind in ("package", "project", "frame"):
            path = folder / kind
            path.write_bytes(b"artificial-contract-fixture")
            task["source"][kind + "_path"] = str(path)
            task["source"][kind + "_sha256"] = workflow.sha(path)
        task["source"].update(scene_id="fixture-state", canvas={"width": 1000, "height": 1000}, coordinate_unit="engine_logical")
        facts_path = folder / "facts.json"
        workflow.save(facts_path, {"schema": "theme-layout-source-facts/1", "scene_id": "fixture-state",
                                  "package_sha256": task["source"]["package_sha256"],
                                  "project_sha256": task["source"]["project_sha256"],
                                  "nodes": {"n0": {}, "n1": {}}, "visual_node_ids": ["n0", "n1"]})
        task["source"]["facts_path"] = str(facts_path)
        task["target"].update(canvas={"width": 1000, "height": 1000}, coordinate_unit="engine_logical",
                              safe_region={"x": 0, "y": 0, "w": 1000, "h": 1000},
                              design_brief="人工夹具的事前条件", brief_origin="designer_brief")
        task["dataset"].update(theme_family="fixture", split="train", family_review="confirmed", rights_status="confirmed")
        structure = task["annotations"]["source_structure"]
        structure.update(status="reviewed", scene_type="fixture", groups=[self.group("g0", "n0"), self.group("g1", "n1")])
        destination = task["annotations"]["target_layout"]
        destination.update(status="reviewed", mode="preserve", topology="preserved", group_boxes_1000={"g0": [100, 100, 100, 100], "g1": [300, 100, 100, 100]},
                           mapping=[{"source_group_id": gid, "target_group_id": gid} for gid in ("g0", "g1")],
                           decision_note="人工构造的正例，用于证明校验器不会拒绝所有完整记录", reference_frame_path=task["source"]["frame_path"], reference_origin="human_relayout")
        quality = task["annotations"]["quality"]
        for key in quality:
            if key != "notes":
                quality[key] = "passed"
        quality["notes"]["relations"] = "夹具不检验关系语义"
        task["review"].update(stage="released", annotator="fixture-A", reviewer="fixture-B", adjudication_note="人工构造的格式检查记录")
        return task

    def test_empty_draft_is_not_releaseable(self):
        task = self.blank()
        self.assertTrue(workflow.validate(task)["valid"])
        self.assertFalse(workflow.validate(task, release=True)["valid"])

    def test_duplicate_ownership_and_cycle_rejected(self):
        task = self.blank()
        task["annotations"]["source_structure"]["groups"] = [self.group("a", "n"), self.group("b", "n")]
        self.assertFalse(workflow.validate(task)["valid"])
        task["annotations"]["source_structure"]["groups"][1]["member_ids"] = ["m"]
        task["annotations"]["source_structure"]["groups"][0]["parent_id"] = "b"
        task["annotations"]["source_structure"]["groups"][1]["parent_id"] = "a"
        self.assertFalse(workflow.validate(task)["valid"])

    def test_complete_record_passes_then_hash_change_fails(self):
        with temporary_workspace() as tmp:
            task = self.release_fixture(Path(tmp))
            result = workflow.validate(task, release=True)
            self.assertTrue(result["valid"], result)
            Path(task["source"]["frame_path"]).write_bytes(b"changed")
            self.assertIn("source.frame 哈希不匹配", workflow.validate(task, release=True)["errors"])

    def test_uncovered_visual_and_unreviewed_regions_rejected(self):
        with temporary_workspace() as tmp:
            task = self.release_fixture(Path(tmp))
            task["annotations"]["source_structure"]["groups"][1]["member_ids"] = []
            self.assertFalse(workflow.validate(task, release=True)["valid"])
            task = self.release_fixture(Path(tmp))
            task["annotations"]["source_structure"]["content_regions"] = [{"id": "r", "kind": "subject", "bbox_1000": [0, 0, 100, 100], "origin": "algorithm_candidate", "evidence": "pending"}]
            self.assertFalse(workflow.validate(task, release=True)["valid"])

    def test_batch_detects_family_leakage(self):
        with temporary_workspace() as tmp:
            for index, split in enumerate(("train", "test")):
                task = self.blank()
                task["task_id"] = f"fixture-{index}"
                task["dataset"].update(theme_family="same-template", split=split)
                workflow.save(Path(tmp) / f"task-{index}.json", task)
            result = workflow.validate_batch(tmp)
            self.assertFalse(result["valid"])
            self.assertTrue(any(e["error"] == "同家族跨集合" for e in result["errors"]))

    def test_init_never_adds_labels_and_never_overwrites(self):
        with temporary_workspace() as tmp:
            folder = Path(tmp)
            (folder / "templates").mkdir()
            (folder / "templates/task.blank.json").write_bytes((ROOT / "templates/task.blank.json").read_bytes())
            with patch.object(workflow, "ROOT", folder):
                result = workflow.new_task("empty-task")
                task = workflow.read(result["task"])
                self.assertEqual(result["humanLabelsCreated"], 0)
                self.assertEqual(task["annotations"]["source_structure"]["groups"], [])
                self.assertEqual(task["annotations"]["target_layout"]["group_boxes_1000"], {})
                with self.assertRaises(ValueError):
                    workflow.new_task("empty-task")


if __name__ == "__main__":
    unittest.main()

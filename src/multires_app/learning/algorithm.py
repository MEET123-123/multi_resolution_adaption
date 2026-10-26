"""Model proposals remain soft suggestions to the existing constraint solver."""

import copy
import json
import os
from pathlib import Path
from multires_app.layout.anchor_layout import AnchorThemeLayout
from multires_app.layout.adaptive_layout import safe_region
from multires_app.evaluation.core import digest
from .contract import make_request, validate_answer


class RecordingAnchor(AnchorThemeLayout):
    def propose_group_boxes(self, ir, desired, source, target, context):
        request, mapping = make_request(
            ir["groups"], ir["source_viewport"], safe_region(source, target, context), context
        )
        context["layoutProposal"] = {
            "key": digest(request),
            "request": request,
            "mapping": mapping,
            "status": "recorded",
        }
        return desired


class LearnedAnchor(RecordingAnchor):
    """Plug in with --algorithm multires_app.learning.algorithm:LearnedAnchor.

    Inherit the anchor engine identity so scheduler feasibility promotions,
    media families and resource scale contracts follow the existing path.
    Predictions are precomputed by layout-lab predict: no HTTP in the solver.
    """

    def __init__(self, predictions=None):
        self.predictions = predictions
        if predictions is None:
            path = os.environ.get("MULTIRES_LAYOUT_PREDICTIONS")
            if not path:
                raise ValueError("set MULTIRES_LAYOUT_PREDICTIONS to a prediction JSON file")
            self.predictions = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        self.events = []

    def propose_group_boxes(self, ir, desired, source, target, context):
        super().propose_group_boxes(ir, desired, source, target, context)
        meta = context["layoutProposal"]
        row = self.predictions.get(meta["key"])
        if not row:
            meta["status"] = "fallback-missing"
            self.events.append(copy.deepcopy(meta))
            return desired
        try:
            answer = validate_answer(row.get("answer"), meta["request"])
        except (ValueError, TypeError, KeyError) as exc:
            meta.update(status="fallback-invalid", reason=str(exc))
            self.events.append(copy.deepcopy(meta))
            return desired
        result = copy.deepcopy(desired)
        groups = {g["id"]: g for g in ir["groups"]}
        region = meta["request"]["safeRegion"]
        for alias, gid in meta["mapping"].items():
            if next(g for g in meta["request"]["groups"] if g["id"] == alias)["fixed"]:
                continue
            x, y, w, h = answer["groups"][alias]
            ref = groups[gid]["bbox"]
            # Project shape proposals to a uniform resource scale; the solver
            # then enforces size limits, centers, corners, gaps and frozen leaves.
            scale = min(w * region["w"] / 1000 / ref["w"], h * region["h"] / 1000 / ref["h"])
            width, height = ref["w"] * scale, ref["h"] * scale
            result[gid] = {
                "x": region["x"] + (x + w / 2) * region["w"] / 1000 - width / 2,
                "y": region["y"] + (y + h / 2) * region["h"] / 1000 - height / 2,
                "w": width,
                "h": height,
            }
        meta.update(status="proposed", provider=row.get("provider", "external"), answer=answer)
        self.events.append(copy.deepcopy(meta))
        return result

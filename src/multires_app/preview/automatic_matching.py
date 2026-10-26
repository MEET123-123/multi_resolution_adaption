"""CPU visual grounding without theme names, positions, annotations or training.

SIFT/RANSAC and dense scale/aspect search generate independent hypotheses.
Appearance verification and a global one-to-one assignment select observations;
the existing reference-first solver and native compiler remain separate stages.
"""

from __future__ import annotations

import json
import math
import time

import numpy as np
from PIL import Image, ImageDraw


def assignment(scores, unmatched_score=0.75):
    """Rectangular Hungarian assignment with one private rejection slot per row."""
    n = len(scores)
    if not n:
        return []
    m = len(scores[0])
    cost = [[1 - v for v in row] + [1 - unmatched_score] * n for row in scores]
    columns = m + n
    u = [0.0] * (n + 1)
    v = [0.0] * (columns + 1)
    p = [0] * (columns + 1)
    way = [0] * (columns + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minimum = [math.inf] * (columns + 1)
        used = [False] * (columns + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = math.inf
            j1 = 0
            for j in range(1, columns + 1):
                if used[j]:
                    continue
                cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                if cur < minimum[j]:
                    minimum[j] = cur
                    way[j] = j0
                if minimum[j] < delta:
                    delta = minimum[j]
                    j1 = j
            for j in range(columns + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minimum[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    result = [None] * n
    for j in range(1, columns + 1):
        if p[j] and j <= m:
            result[p[j] - 1] = j - 1
    return result


def iou(a, b):
    x = max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    y = max(0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    inter = x * y
    return inter / max(1.0, a[2] * a[3] + b[2] * b[3] - inter)


def _ncc(a, b, mask):
    weight = max(1.0, float(mask.sum()))
    a = a.astype(np.float32)
    b = b.astype(np.float32)
    a = a - float((a * mask).sum() / weight)
    b = b - float((b * mask).sum() / weight)
    return float(
        (a * b * mask).sum() / max(1e-6, np.sqrt((a * a * mask).sum() * (b * b * mask).sum()))
    )


def appearance(template, background, cv):
    """Include the negative space around opaque glyphs, not just flat ink.

    Comparing only opaque pixels loses the silhouette of monochrome text.
    A dilated support and a local matte preserve that evidence without treating
    every transparent image rectangle as an opaque visual component.
    """
    alpha = template[:, :, 3].astype(np.float32) / 255
    rgb = template[:, :, :3].astype(np.float32) * alpha[:, :, None] + np.asarray(background) * (
        1 - alpha[:, :, None]
    )
    radius = max(2, round(min(alpha.shape) * 0.035))
    support = cv.dilate(
        (alpha > 0.5).astype(np.uint8),
        cv.getStructuringElement(cv.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1)),
    )
    return cv.cvtColor(np.uint8(np.clip(rgb, 0, 255)), cv.COLOR_RGB2GRAY), support.astype(
        np.float32
    )


def verify(reference, template, box, cv):
    x, y, w, h = map(round, box)
    if min(w, h) < 8 or x < 0 or y < 0 or x + w > reference.shape[1] or y + h > reference.shape[0]:
        return None
    src = cv.resize(template, (w, h), interpolation=cv.INTER_AREA)
    dst = reference[y : y + h, x : x + w]
    mask = (src[:, :, 3].astype(np.float32) / 255) ** 2
    if mask.sum() < 32:
        return None
    background = (
        np.median(dst[src[:, :, 3] < 64], axis=0)
        if np.any(src[:, :, 3] < 64)
        else np.median(dst.reshape(-1, 3), axis=0)
    )
    gray_a, support = appearance(src, background, cv)
    gray_b = cv.cvtColor(dst, cv.COLOR_RGB2GRAY)
    gray = _ncc(gray_a, gray_b, support)

    def edges(im):
        im = cv.GaussianBlur(im, (3, 3), 0).astype(np.float32)
        return cv.magnitude(cv.Sobel(im, cv.CV_32F, 1, 0), cv.Sobel(im, cv.CV_32F, 0, 1))

    edge = _ncc(edges(gray_a), edges(gray_b), support)
    delta = abs(src[:, :, :3].astype(float) - dst.astype(float))
    color_error = float((delta * mask[:, :, None]).sum() / max(1.0, mask.sum() * 3) / 255)
    color = max(0.0, 1 - color_error / 0.35)
    # A gray NCC match must not turn an orange disc into a black clock face.
    if color_error > 0.26:
        return None
    score = 0.65 * max(0.0, gray) + 0.15 * max(0.0, edge) + 0.2 * color
    # Tolerant edge distance handles slightly redrawn strokes in generated
    # references. Both directions are required to reject partial-logo matches.
    ea = cv.Canny(gray_a, 60, 150)
    eb = cv.Canny(gray_b, 60, 150)
    ea[support == 0] = 0
    eb[support == 0] = 0
    chamfer = 0.0
    if cv.countNonZero(ea) >= 16 and cv.countNonZero(eb) >= 16:
        radius = max(1.0, min(w, h) * 0.025)
        da = cv.distanceTransform(255 - ea, cv.DIST_L2, 3)
        db = cv.distanceTransform(255 - eb, cv.DIST_L2, 3)
        chamfer = float(
            min(np.exp(-da[eb > 0] / radius).mean(), np.exp(-db[ea > 0] / radius).mean())
        )
        if gray > 0.55 and edge > 0.5 and color_error < 0.14 and chamfer > 0.72:
            score = max(score, 0.65 + 0.25 * chamfer)
    return {
        "bbox": [x, y, w, h],
        "score": float(score),
        "grayNcc": gray,
        "edgeNcc": edge,
        "colorError": color_error,
        "bidirectionalEdgeFit": chamfer,
    }


def feature_candidates(reference, template, cv, reference_features):
    sift = cv.SIFT_create(nfeatures=3500, contrastThreshold=0.02)
    gray = cv.cvtColor(template[:, :, :3], cv.COLOR_RGB2GRAY)
    kp, des = sift.detectAndCompute(gray, (template[:, :, 3] > 128).astype(np.uint8) * 255)
    ref_kp, ref_des = reference_features
    if des is None or ref_des is None or len(des) < 6:
        return []
    pairs = []
    # Keep two plausible instances: a target may contain duplicated widgets.
    for nearest in cv.BFMatcher(cv.NORM_L2).knnMatch(des, ref_des, k=min(3, len(ref_des))):
        if len(nearest) < 2:
            continue
        cutoff = 0.8 * nearest[-1].distance
        for match in nearest[:-1]:
            if match.distance < cutoff:
                pairs.append(match)
    results = []
    for _ in range(3):
        if len(pairs) < 6:
            break
        a = np.float32([kp[m.queryIdx].pt for m in pairs])
        b = np.float32([ref_kp[m.trainIdx].pt for m in pairs])
        matrix, inliers = cv.estimateAffinePartial2D(
            a, b, method=cv.RANSAC, ransacReprojThreshold=4, maxIters=2500, confidence=0.995
        )
        if matrix is None or inliers is None:
            break
        inside = inliers.ravel().astype(bool)
        count = int(inside.sum())
        if count < 5:
            break
        coverage = float(
            cv.contourArea(cv.convexHull(a[inside]))
            / max(1.0, template.shape[0] * template.shape[1])
        )
        angle = abs(math.degrees(math.atan2(matrix[1, 0], matrix[0, 0])))
        corners = np.float32(
            [
                [0, 0],
                [template.shape[1], 0],
                [0, template.shape[0]],
                [template.shape[1], template.shape[0]],
            ]
        )
        mapped = cv.transform(corners[None, :, :], matrix)[0]
        left, top = mapped.min(axis=0)
        right, bottom = mapped.max(axis=0)
        if angle < 8 and coverage > 0.035:
            record = verify(
                reference,
                template,
                [float(left), float(top), float(right - left), float(bottom - top)],
                cv,
            )
            if record:
                record.update(method="sift-ransac", inliers=count, featureCoverage=coverage)
                # Geometry is supporting evidence, never a substitute for appearance.
                record["score"] = min(
                    1.0, record["score"] + 0.06 * min(1.0, count / 12) * min(1.0, coverage / 0.2)
                )
                if (
                    count >= 12
                    and coverage >= 0.2
                    and record["grayNcc"] > 0.4
                    and record["colorError"] < 0.2
                ):
                    # AI-generated references redraw outlines. Broad, consistent
                    # keypoint geometry can support a moderately changed bitmap;
                    # a tiny matching logo or a color-incompatible object cannot.
                    quality = min(1.0, count / 30) * min(1.0, coverage / 0.4)
                    record["score"] = max(record["score"], 0.65 + 0.25 * quality)
                results.append(record)
        pairs = [p for p, flag in zip(pairs, inside) if not flag]
    return results


def dense_candidates(reference, template, cv, deadline=None):
    gray = cv.cvtColor(reference, cv.COLOR_RGB2GRAY)
    source, support = appearance(template, np.median(reference.reshape(-1, 3), axis=0), cv)
    mask0 = np.uint8(support * 255)
    candidates = []
    height, width = gray.shape
    th, tw = source.shape
    # Search target scales independently of original screen anchors. Slight
    # aspect changes accommodate AI redraws while the later solver stays uniform.
    for w in sorted(set(round(v) for v in np.geomspace(max(16, width * 0.025), width * 0.8, 42))):
        if deadline and time.monotonic() >= deadline:
            break
        for aspect in (0.55, 0.72, 0.86, 1.0, 1.18, 1.4):
            h = round(w * th / tw * aspect)
            if min(w, h) < 8 or h >= height or w >= width:
                continue
            bitmap = cv.resize(source, (w, h), interpolation=cv.INTER_AREA)
            mask = cv.resize(mask0, (w, h), interpolation=cv.INTER_NEAREST)
            if cv.countNonZero(mask) < 32 or bitmap.std() < 2:
                continue
            scores = cv.matchTemplate(gray, bitmap, cv.TM_CCOEFF_NORMED, mask=mask)
            scores = np.nan_to_num(scores, nan=-1.0, posinf=-1.0, neginf=-1.0)
            for _ in range(2):
                _, best, _, point = cv.minMaxLoc(scores)
                x, y = point
                if best < 0.45:
                    break
                candidates.append((float(best), [x, y, w, h]))
                scores[max(0, y - h // 2) : y + h // 2 + 1, max(0, x - w // 2) : x + w // 2 + 1] = (
                    -1
                )
    candidates.sort(reverse=True, key=lambda p: p[0])
    selected = []
    for _, box in candidates:
        if any(iou(box, r["bbox"]) > 0.55 for r in selected):
            continue
        record = verify(reference, template, box, cv)
        if record:
            record["method"] = "dense-pyramid"
            selected.append(record)
        if len(selected) >= 10:
            break
    # Refine size around each coarse peak; this avoids the old 20–40% scale steps.
    refined = []
    for row in selected[:5]:
        if deadline and time.monotonic() >= deadline:
            break
        x, y, w, h = row["bbox"]
        best = row
        for sx in (0.95, 1.0, 1.05):
            for sy in (0.95, 1.0, 1.05):
                ww, hh = round(w * sx), round(h * sy)
                left = max(0, x - max(4, w // 12))
                top = max(0, y - max(4, h // 12))
                right = min(width, x + w + max(4, w // 12))
                bottom = min(height, y + h + max(4, h // 12))
                if ww >= right - left or hh >= bottom - top:
                    continue
                bitmap = cv.resize(source, (ww, hh))
                mask = cv.resize(mask0, (ww, hh), interpolation=cv.INTER_NEAREST)
                scores = cv.matchTemplate(
                    gray[top:bottom, left:right], bitmap, cv.TM_CCOEFF_NORMED, mask=mask
                )
                scores = np.nan_to_num(scores, nan=-1.0, posinf=-1.0, neginf=-1.0)
                _, _, _, point = cv.minMaxLoc(scores)
                trial = verify(reference, template, [left + point[0], top + point[1], ww, hh], cv)
                if trial and trial["score"] > best["score"]:
                    trial["method"] = "dense-refined"
                    best = trial
        refined.append(best)
    return [*selected, *refined]


def automatic_matcher(context):
    try:
        import cv2 as cv
    except ImportError as exc:
        raise ValueError(
            '自动视觉匹配需要安装项目的 vision 依赖：pip install -e ".[vision]"'
        ) from exc
    from multires_app.preview.target_layout import SCHEMA

    cv.setNumThreads(2)
    cv.setRNGSeed(31)
    ref = context["reference"].copy()
    ref.thumbnail((960, 960), Image.Resampling.LANCZOS)
    reference = np.asarray(ref)
    sift = cv.SIFT_create(nfeatures=6000, contrastThreshold=0.02)
    features = sift.detectAndCompute(cv.cvtColor(reference, cv.COLOR_RGB2GRAY), None)
    priority = {
        "datetime": 0,
        "header": 1,
        "content": 2,
        "control": 2,
        "footer": 3,
        "decoration": 4,
    }
    groups = sorted(
        [g for g in context["catalog"]["groups"] if not g["locked"]],
        key=lambda g: min((priority.get(r, 2) for r in g.get("roles", [])), default=2),
    )
    all_rows = {}
    truncated = set()
    uninformative = set()
    started = time.monotonic()
    budget = float(context.get("max_seconds", 180))
    if not math.isfinite(budget) or not 0 < budget <= 1800:
        raise ValueError("自动匹配搜索预算需为 (0,1800] 秒")
    deadline = started + budget
    for g in groups:
        if time.monotonic() >= deadline:
            all_rows[g["id"]] = []
            truncated.add(g["id"])
            continue
        template = np.asarray(context["templates"][g["id"]].convert("RGBA"))
        gray = cv.cvtColor(template[:, :, :3], cv.COLOR_RGB2GRAY)
        landmarks = cv.countNonZero(cv.Canny(gray, 60, 150)) + cv.countNonZero(
            cv.Canny(template[:, :, 3], 60, 150)
        )
        if landmarks < 16:
            all_rows[g["id"]] = []
            uninformative.add(g["id"])
            continue
        variants = [("script-render", template)]
        observed = context.get("observation_templates", {}).get(g["id"])
        if observed is not None:
            variants.append(("source-observation", np.asarray(observed.convert("RGBA"))))
        rows = []
        for basis, variant in variants:
            candidates = feature_candidates(reference, variant, cv, features)
            for row in candidates:
                row["templateBasis"] = basis
            rows.extend(candidates)
        if not any(r["score"] >= 0.92 for r in rows):
            group_deadline = min(deadline, time.monotonic() + 30)
            for basis, variant in variants:
                if time.monotonic() >= group_deadline:
                    break
                # Keep time for the observed current clock digits when a raw
                # script template contains a different system time/weather.
                remaining = group_deadline - time.monotonic()
                end = time.monotonic() + remaining / (
                    2 if basis == "script-render" and len(variants) > 1 else 1
                )
                candidates = dense_candidates(reference, variant, cv, end)
                for row in candidates:
                    row["templateBasis"] = basis
                rows.extend(candidates)
            if time.monotonic() >= group_deadline:
                truncated.add(g["id"])
        rows.sort(key=lambda r: r["score"], reverse=True)
        kept = []
        for row in rows:
            if row["score"] < 0.5 or any(iou(row["bbox"], r["bbox"]) > 0.55 for r in kept):
                continue
            kept.append(row)
            if len(kept) >= 6:
                break
        all_rows[g["id"]] = kept
    # Merge competing observations of the same target object before assignment.
    slots = []
    for rows in all_rows.values():
        for row in rows:
            options = [(iou(row["bbox"], b), i) for i, b in enumerate(slots)]
            if options and max(options)[0] > 0.55:
                row["slot"] = max(options)[1]
            else:
                row["slot"] = len(slots)
                slots.append(row["bbox"])
    scores = []
    choices = {}
    for g in groups:
        row_scores = [0.0] * len(slots)
        for row in all_rows[g["id"]]:
            slot = row["slot"]
            if row["score"] > row_scores[slot]:
                row_scores[slot] = row["score"]
                choices[g["id"], slot] = row
        scores.append(row_scores)
    minimum = float(context.get("minimum_confidence", 0.78))
    assigned = assignment(scores, minimum)
    matches = []
    unmatched = []
    for g, slot in zip(groups, assigned):
        candidates = all_rows[g["id"]]
        if slot is None or (g["id"], slot) not in choices:
            reason = (
                "搜索预算耗尽，未进行可靠定位"
                if g["id"] in truncated
                else (
                    "素材缺少可区分的轮廓或纹理，未进行全图猜测"
                    if g["id"] in uninformative
                    else "自动外观/几何证据不足或与其他组争用同一目标区域"
                )
            )
            unmatched.append(
                {
                    "id": g["id"],
                    "reason": reason,
                    "bestScore": candidates[0]["score"] if candidates else None,
                }
            )
            continue
        row = choices[g["id"], slot]
        x, y, w, h = row["bbox"]
        matches.append(
            {
                "id": g["id"],
                "bbox": [
                    x / ref.width * 1000,
                    y / ref.height * 1000,
                    w / ref.width * 1000,
                    h / ref.height * 1000,
                ],
                "confidence": row["score"],
                "evidence": {k: v for k, v in row.items() if k not in {"bbox", "slot", "score"}},
            }
        )
    directory = context["job_directory"]
    payload = {
        "provider": "opencv-sift+dense-pyramid+hungarian",
        "opencv": cv.__version__,
        "minimumConfidence": minimum,
        "candidates": all_rows,
        "targetSlots": slots,
        "manualCoordinates": False,
        "searchSeconds": time.monotonic() - started,
        "budgetSeconds": budget,
        "truncatedGroups": sorted(truncated),
        "uninformativeGroups": sorted(uninformative),
    }
    (directory / "automatic-candidates.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf8"
    )
    overlay = ref.copy()
    draw = ImageDraw.Draw(overlay)
    for row in matches:
        x, y, w, h = row["bbox"]
        x = x / 1000 * ref.width
        y = y / 1000 * ref.height
        w = w / 1000 * ref.width
        h = h / 1000 * ref.height
        draw.rectangle((x, y, x + w, y + h), outline="#ef563b", width=2)
        draw.text((x + 3, y + 3), row["id"], fill="#e32419", stroke_width=1, stroke_fill="white")
    overlay.save(directory / "automatic-overlay.png")
    return {
        "schema": SCHEMA,
        "matches": matches,
        "unmatched": unmatched,
        "provider": payload["provider"],
        "notes": [
            "代码自动匹配；没有读取人工坐标或主题名称。重复目标保留为候选，一对一分配已有组件。"
        ],
        "automatic": True,
        "training": False,
    }

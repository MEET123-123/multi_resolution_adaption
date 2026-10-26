"""Source-only geometry features and an aspect-preserving regression decoder.

This is a separate numerical-head contract, not an autoregressive chat adapter.
No teacher/target box is available to feature extraction or inference.
"""

from __future__ import annotations
import math

VERSION = "multires-numeric-head/1"
ROLES = ("datetime", "header", "footer", "content", "shortcut-controls", "settings-panel")
CONTAINERS = ("rigid", "row", "column", "overlay")


def geometry(request, group):
    sw, sh = (request["sourceViewport"][k] for k in ("width", "height"))
    tw, th = (request["safeRegion"][k] for k in ("w", "h"))
    x, y, w, h = group["sourceBox"]
    if min(sw, sh, tw, th, w, h) <= 0:
        raise ValueError("positive source, target and group dimensions required")
    anchor = group["anchor"].split("_")
    ax = 0.0 if "left" in anchor else 1.0 if "right" in anchor else 0.5
    ay = 0.0 if "top" in anchor else 1.0 if "bottom" in anchor else 0.5
    px, py = (x + ax * w) / 1000, (y + ay * h) / 1000
    if "x" in group["centerAxes"]:
        ax, px = 0.5, 0.5
    if "y" in group["centerAxes"]:
        ay, py = 0.5, 0.5
    base_scale = min(tw / sw, th / sh)
    # [source physical dimensions / target dimensions, anchor fractions,
    #  normalized anchor positions, reference uniform physical scale]
    constants = [
        w / 1000 * sw / tw,
        h / 1000 * sh / th,
        ax,
        ay,
        px,
        py,
        base_scale,
        float("x" not in group["centerAxes"]),
        float("y" not in group["centerAxes"]),
    ]
    base = decode(constants, [0.0, 0.0, 0.0])
    features = [v / 1000 for v in group["sourceBox"]] + [tw / sw, th / sh, (tw / th) / (sw / sh)]
    features += [v / 1000 for v in base] + [ax, ay, px, py]
    features += [float(role in group["roles"]) for role in ROLES]
    features += [float(group["container"] == kind) for kind in CONTAINERS]
    features += [float(axis in group["centerAxes"]) for axis in ("x", "y")]
    features += [
        float(group["edges"].get(axis) == edge)
        for axis, edge in (("x", "left"), ("x", "right"), ("y", "top"), ("y", "bottom"))
    ]
    features += [float(group["fixed"]), math.log1p(group["memberCount"]) / math.log(17)]
    return features, constants


def decode(constants, residual):
    """Bounded offsets and ONE physical scale prevent arbitrary aspect collapse."""
    rw, rh, ax, ay, px, py, scale, move_x, move_y = constants
    dx, dy, ds = residual
    scale *= math.exp(math.log(4) * math.tanh(ds))
    w, h = rw * scale, rh * scale
    return [
        1000 * (px + move_x * 0.25 * math.tanh(dx) - ax * w),
        1000 * (py + move_y * 0.25 * math.tanh(dy) - ay * h),
        1000 * w,
        1000 * h,
    ]


def tensor_decode(constants, residual):
    import torch

    rw, rh, ax, ay, px, py, scale, move_x, move_y = constants.unbind(-1)
    dx, dy, ds = residual.unbind(-1)
    scale = scale * torch.exp(math.log(4) * torch.tanh(ds))
    w, h = rw * scale, rh * scale
    return torch.stack(
        (
            px + move_x * 0.25 * torch.tanh(dx) - ax * w,
            py + move_y * 0.25 * torch.tanh(dy) - ay * h,
            w,
            h,
        ),
        dim=-1,
    )


def make_head(feature_count, hidden_size=0):
    import torch
    from torch import nn

    class Head(nn.Module):
        def __init__(self):
            super().__init__()
            self.geometry = nn.Sequential(
                nn.Linear(feature_count, 64),
                nn.SiLU(),
                nn.Linear(64, 64),
                nn.SiLU(),
                nn.Linear(64, 3),
            )
            nn.init.zeros_(self.geometry[-1].weight)
            nn.init.zeros_(self.geometry[-1].bias)
            if hidden_size:
                self.fusion = nn.Sequential(
                    nn.LayerNorm(hidden_size),
                    nn.Linear(hidden_size, 64),
                    nn.SiLU(),
                    nn.Linear(64, 3),
                )
                nn.init.zeros_(self.fusion[-1].weight)
                nn.init.zeros_(self.fusion[-1].bias)

        def forward(self, features, constants, hidden=None):
            residual = self.geometry(features)
            if hidden is not None:
                residual = residual + self.fusion(hidden.float())
            return tensor_decode(constants, residual)

    return Head()

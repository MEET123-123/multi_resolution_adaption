"""Replace this policy with your own layout algorithm.

Run: multires.py scenes ... --algorithm samples/custom_scene_layout.py:MySceneLayout
"""
from multires_app.pipeline.scene_pipeline import SemanticReflow


class MySceneLayout:
    name = "my-scene-layout"

    def layout_scene(self, components, source_profile, target_profile, context):
        # Each component contains roots, stable member IDs, inferred role,
        # current visible elements and their evaluated global bounds.
        # context['state']: target-replayed variables, interaction trace.
        # context['graph']: source state/transition graph and exploration bounds.
        # context['logicalViewport']: XML coordinates, not physical pixels.
        # context['layoutRequest']: full-reflow / local-reflow / overlay.
        # context['fixedTransforms']: public components to keep fixed.
        # context['baseTransforms'], ['baseDescriptors']: previous layout.
        # Reused layouts bypass this method. Local/overlay requests include
        # only affected components; the scheduler preserves the fixed ones.
        # Return component transforms {id: {sx, sy, tx, ty}}. Keep paired
        # visual/button members together; the exporter handles nested geometry.
        return SemanticReflow().layout_scene(components, source_profile, target_profile, context)

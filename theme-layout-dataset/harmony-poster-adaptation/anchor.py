"""Apply soft proposals while preserving the solver's exact defaults for audited groups."""
from multires_app.learning.algorithm import LearnedAnchor


class PosterAnchor(LearnedAnchor):
    def propose_group_boxes(self, ir, desired, source, target, context):
        proposed = super().propose_group_boxes(ir, desired, source, target, context)
        meta = context["layoutProposal"]
        if meta["status"] != "proposed":
            return proposed
        row = self.predictions[meta["key"]]
        for gid in row.get("holdGroups", []):
            actual = meta["mapping"][gid]
            proposed[actual] = desired[actual]
        return proposed

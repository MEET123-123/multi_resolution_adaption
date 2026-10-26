"""Bounded proposals via the existing layout algorithm interface."""

import copy


class RescheduleRefiner:
    name = "reschedule"
    version = "1.0"

    def propose(self, project, observations, options):
        from multires_app.pipeline.scene_clusters import schedule_device
        from multires_app.pipeline.scene_pipeline import load_scene_algorithm

        candidate = copy.deepcopy(project)
        candidate["anchorIRs"] = {}
        specification = options.get("algorithm", project.get("algorithmSpec", "anchor"))
        algorithm = load_scene_algorithm(specification)
        candidate["algorithmSpec"] = specification
        candidate["algorithm"] = getattr(algorithm, "name", specification)
        for device in candidate["devices"]:
            schedule_device(candidate, device, algorithm)
        return candidate

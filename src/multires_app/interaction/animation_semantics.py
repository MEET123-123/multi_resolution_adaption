"""Theme-independent classification of human-controlled visual animation state.

Consumes the parsed node/rule model. No theme names, asset paths, variable names
or device coordinates participate in classification. New script adapters can
reuse it by emitting the same normalized model.
"""

STATE_POLICY = {
    "version": 2,
    "classification": "script-dependencies-and-human-rules",
    "initialization": "preserve-stopped-and-assigned-values",
    "media": "first-frame-affine",
    "waiting": "retain-source-edge-outside-target-canvas",
}


def pointer_effect_ids(model):
    """Visual feedback attached to the current pointer on both axes."""
    from multires_app.engine.scene_engine import REF, VISUAL
    from multires_app.interaction.interaction_ir import dependencies

    _, transitive = dependencies(model)

    def depends(expression, name):
        refs = set(REF.findall(expression))
        return name in refs or any(name in transitive.get(ref, set()) for ref in refs)

    result = set()
    for uid, node in model["nodes"].items():
        if node["tag"] not in VISUAL:
            continue
        owner = uid
        while owner:
            n = model["nodes"][owner]
            a = n["attributes"]
            if depends(a.get("x", ""), "touch_x") and depends(a.get("y", ""), "touch_y"):
                result.add(uid)
                break
            owner = n["parent"]
    return result


def scene_animation_names(model):
    """Interaction-driven visual poses, including opacity and individual images.

    Includes page transitions, drawers and reveal effects. A frame
    selector alone is media playback; a user-controlled position, size,
    visibility or opacity is part of the human scene state.
    """
    from multires_app.engine.scene_engine import REF, VISUAL
    from multires_app.interaction.interaction_ir import dependencies

    _, transitive = dependencies(model)
    animated = {
        name
        for name, node in model.get("variables", {}).items()
        if any(model["nodes"][c]["tag"] == "VariableAnimation" for c in node["children"])
    }
    started = set()
    positions = set()

    def refs(value):
        direct = set(REF.findall(value))
        return direct | set().union(*(transitive.get(n, set()) for n in direct))

    # Follow Var change handlers reached by a human assignment as well as
    # animation commands placed directly in a Button handler.
    human_writes = set()
    reachable = set()
    while True:
        previous = (len(reachable), len(human_writes))
        for trigger in model.get("triggers", []):
            owner = model["nodes"][trigger["owner"]]
            if owner["tag"] == "Button" or (
                owner["tag"] == "Var"
                and (
                    owner["attributes"].get("name") in human_writes
                    or transitive.get(owner["attributes"].get("name"), set()) & human_writes
                )
            ):
                reachable.add(trigger["id"])
                human_writes.update(
                    c["attributes"].get("name", "")
                    for c in trigger["commands"]
                    if c["tag"] == "VariableCommand"
                )
                human_writes.update(
                    c["attributes"]["target"][:-10]
                    for c in trigger["commands"]
                    if c["tag"] == "Command"
                    and c["attributes"].get("target", "").endswith(".animation")
                )
        if previous == (len(reachable), len(human_writes)):
            break
    for trigger in model.get("triggers", []):
        if trigger["id"] not in reachable:
            continue
        for c in trigger["commands"]:
            target = c["attributes"].get("target", "")
            if c["tag"] == "Command" and target.endswith(".animation"):
                started.add(target[:-10])
    for node in model["nodes"].values():
        if node["tag"] in VISUAL:
            for field in (
                "x",
                "y",
                "w",
                "h",
                "width",
                "height",
                "size",
                "space",
                "alpha",
                "visibility",
            ):
                positions.update(refs(node["attributes"].get(field, "")))
            for child in node["children"]:
                animation = model["nodes"][child]
                if animation["tag"] not in {"PositionAnimation", "SizeAnimation", "AlphaAnimation"}:
                    continue
                for frame in animation["children"]:
                    positions.update(refs(str(model["nodes"][frame]["attributes"])))
    # Some native themes implement paging with a user-written start timestamp
    # and expressions such as min((#time-#pageStart)/500,1), without an
    # explicit VariableAnimation. Keep these visual poses at the current scene
    # value; resetting #time to zero would put an opened drawer offscreen.
    clocks = set()
    for trigger in model.get("triggers", []):
        if trigger["id"] not in reachable:
            continue
        for command in trigger["commands"]:
            a = command["attributes"]
            if command["tag"] == "VariableCommand" and "time" in refs(a.get("expression", "")):
                clocks.add(a.get("name", ""))
    timed_positions = {
        name
        for name in positions
        if name in model.get("variables", {})
        and "time" in transitive.get(name, set())
        and transitive.get(name, set()) & clocks
    }
    driven = {name for name in animated if transitive.get(name, set()) & human_writes}
    return (animated & (started | human_writes | driven) & positions) | timed_positions

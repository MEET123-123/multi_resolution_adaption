"""Give an opaque backdrop its own cover affine beneath layered artwork."""

import copy


def split_background_layers(project):
    nodes = project["model"]["nodes"]
    vp = project.get("sourceDevice", {}).get("viewport", {})
    if not vp:
        return []
    rows = {}
    for layout in project["sourceDevice"]["layouts"]:
        for row in layout.get("rows", []):
            if row[6] >= 0:
                rows.setdefault(project["nodeOrder"][row[0]], row)
    output = []
    changes = []
    for comp in project["components"]:
        if comp.get("backgroundFrameNodes") and comp.get("backgroundComposition"):
            separated = [
                u
                for u in comp["backgroundFrameNodes"]
                if project["nodeComponents"].get(u) != comp["id"] and nodes[u]["tag"] == "Image"
            ]
            if separated and not any(nodes[u]["tag"] == "Button" for u in comp["members"]):
                comp.update(role="decoration", backgroundComposition=False, layeredArtwork=True)
                changes.append(
                    {
                        "composition": comp["id"],
                        "policy": "separate artwork containment from backdrop cover",
                    }
                )
        if not comp.get("backgroundComposition") or comp.get("backgroundFrameNodes"):
            output.append(comp)
            continue
        photos = []
        for uid in comp["members"]:
            row = rows.get(uid)
            if (
                row
                and nodes[uid]["tag"] == "Image"
                and project["assetOrder"][row[6]].lower().endswith((".jpg", ".jpeg"))
                and row[3] >= vp["width"] * 0.9
                and row[4] >= vp["height"] * 0.9
            ):
                photos.append(uid)
        if not photos:
            output.append(comp)
            continue
        canvases = set(photos)
        for uid in comp["members"]:
            if nodes[uid]["tag"] != "Image":
                continue
            if any(
                all(
                    nodes[uid]["attributes"].get(k) == nodes[photo]["attributes"].get(k)
                    for k in ("x", "y", "w", "h", "align", "alignV")
                )
                for photo in photos
            ):
                canvases.add(uid)
        members = [uid for uid in comp["members"] if uid not in canvases]
        if not any(nodes[uid]["tag"] != "Group" for uid in members):
            output.append(comp)
            continue
        artwork_role = (
            "decoration"
            if not any(nodes[uid]["tag"] == "Button" for uid in members)
            else comp["role"]
        )
        output.append(
            {
                **comp,
                "members": members,
                "backgroundFrameNodes": list(comp["members"]),
                "role": artwork_role,
                "backgroundComposition": (
                    False if artwork_role == "decoration" else comp.get("backgroundComposition")
                ),
                "layeredArtwork": artwork_role == "decoration",
                "layoutFollowers": [
                    uid for uid in comp.get("layoutFollowers", []) if uid in members
                ],
            }
        )
        photo = copy.deepcopy(comp)
        photo.update(
            id=photos[0],
            name=comp["name"] + " / backdrop",
            roots=[uid for uid in comp["members"] if uid in canvases],
            members=[uid for uid in comp["members"] if uid in canvases],
            backgroundComposition=False,
            layoutFollowers=[],
            bindingButtons=[],
        )
        output.append(photo)
        changes.append({"composition": comp["id"], "backdrop": photos[0]})
    if changes:
        project["components"] = output
        project["nodeComponents"] = {uid: c["id"] for c in output for uid in c["members"]}
        project.setdefault("backgroundLayerSplits", []).extend(changes)
    return changes

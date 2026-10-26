"""Deterministic compatibility signature for the historical and V4 theme generators.

Includes all reachable states, all 6 targets, source and adapted XML, geometry,
fixed toolkit image bytes and HWT ZIP resources (no dataset output).
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"src"),str(ROOT/"studio_reverse"/"web_theme_pipeline")]
import interactive as old
import interaction_families_v4 as v4
from PIL import Image

def hash_bytes(data):
    return hashlib.sha256(data).hexdigest()

def xml_bytes(root):
    return ET.tostring(root,encoding="utf8",xml_declaration=True)

def snapshot():
    checks={}
    checks["legacy-devices"]=old.DEV
    checks["legacy-groups"]=old.BOX
    checks["legacy-base-xml"]=hash_bytes(xml_bytes(old.template()))
    checks["legacy-art"]= {n:hash_bytes(blob) for n,blob in sorted(old.panels().items())}
    checks["legacy-adapted"]={}
    for d in old.DEV:
        name=d[0]
        checks["legacy-adapted"][name]=hash_bytes(xml_bytes(old.target_xml(old.template(),d)))
    checks["v4"]={}
    state_count=0
    with tempfile.TemporaryDirectory() as tmp:
        for family in sorted(v4.FAMILIES):
            root=v4.construct(family)
            states=v4.states(family)
            state_count+=len(states)
            family_data={"source":hash_bytes(xml_bytes(root)),
                         "states":states,"devices":{}}
            for device in v4.base.DEV:
                adapted=v4.adapt(root,device,family)
                family_data["devices"][device[0]]={
                    "xml":hash_bytes(xml_bytes(adapted)),
                    "geometry":[{"state":s["id"],"positions":v4.positions(family,device,s["variables"])}
                                for s in states]}
            checks["v4"][family]=family_data
        # Package a representative for each family; its resources and archive structure
        # must stay byte-identical after moving primitives.
        support={"background.jpg":b"x","knob.png":b"y"}
        for family in sorted(v4.FAMILIES):
            target=Path(tmp)/(family+".hwt")
            v4.package(target,v4.construct(family),support,{"license":"CC0","page":"test"})
            # Compare ZIP member payloads, not archive bytes: ZIP timestamps vary
            # between invocations even for identical XML and assets.
            with zipfile.ZipFile(target) as archive:
                payload=[(name,hash_bytes(archive.read(name)))
                         for name in sorted(archive.namelist())]
            checks["v4"][family]["package"]=payload
    json_bytes=json.dumps(checks,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode("utf8")
    return {"sha256":hash_bytes(json_bytes),"families":len(v4.FAMILIES),
            "targetDevices":len(v4.base.DEV),"statePaths":state_count,
            "stateDevicePairs":state_count*len(v4.base.DEV),
            "sections":checks}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--output",required=True)
    ap.add_argument("--compare")
    args=ap.parse_args()
    doc=snapshot()
    mismatch=False
    if args.compare:
        previous=json.loads(Path(args.compare).read_text(encoding="utf8"))
        if doc["sha256"]!=previous["sha256"]:
            print("BEHAVIOR_MISMATCH before="+previous["sha256"]+" after="+doc["sha256"])
            mismatch=True
        else:
            print("BEHAVIOR_SIGNATURE_MATCH "+doc["sha256"])
            mismatch=False
    output=Path(args.output)
    if output.exists():raise ValueError("Output file already exists")
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(doc,ensure_ascii=False,indent=2),encoding="utf8")
    print("BEHAVIOR_SIGNATURE",doc["sha256"])
    print("FAMILIES",doc["families"],"DEVICES",doc["targetDevices"],"PAIRS",doc["stateDevicePairs"])
    return 1 if mismatch else 0

if __name__=="__main__":
    raise SystemExit(main())

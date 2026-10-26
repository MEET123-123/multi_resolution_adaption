"""Apply the verified theme-primitives extraction with rollback on signature mismatch.

Non-target user files and datasets are never touched.
"""
from __future__ import annotations
import ast
import datetime
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DIR=ROOT/"studio_reverse"/"web_theme_pipeline"
LEGACY=DIR/"interactive.py"
V4=DIR/"interaction_families_v4.py"
PRIMITIVES=DIR/"theme_primitives.py"
BASELINE=ROOT/"artifacts"/"organization"/"theme-primitives-baseline-payload-20261024.json"
EXTRACT={"DEV","BOX","font","cover","template","panels","position",
         "state_position","conditional_xy","target_xml","draw_scene"}
FUNC_EXPORT=("font","cover","template","panels","position",
             "state_position","conditional_xy","target_xml","draw_scene")

def digest(data):
    return hashlib.sha256(data).hexdigest()

def migrate():
    if PRIMITIVES.exists():
        raise RuntimeError("theme_primitives.py exists: refusing overwrite")
    if not BASELINE.is_file():
        raise RuntimeError("Missing pre-migration reference signature")
    originals={LEGACY:LEGACY.read_bytes(),V4:V4.read_bytes()}
    text=originals[LEGACY].decode("utf-8-sig").replace("\r\n","\n")
    lines=text.splitlines(keepends=True)
    tree=ast.parse(text)
    nodes={}
    for node in tree.body:
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name in EXTRACT:
            nodes[node.name]=node
        if isinstance(node,ast.Assign):
            for target in node.targets:
                if isinstance(target,ast.Name) and target.id in ("DEV","BOX"):
                    nodes[target.id]=node
    missing=EXTRACT-set(nodes)
    if missing:raise RuntimeError("Expected definitions missing: "+str(missing))
    selected=sorted(nodes.items(),key=lambda pair:pair[1].lineno)
    excluded=set()
    snippets=[]
    for name,node in selected:
        excluded.update(range(node.lineno,node.end_lineno+1))
        snippets.append(ast.get_source_segment(text,node).rstrip()+"\n\n")
    # The new shared module contains precisely the original implementation bodies.
    prim=("\"\"\"Shared tools for legacy and V4 multistate theme experiments.\n"
          "Do not confuse this experimental XML generator with the official Huawei runtime.\n"
          "\"\"\"\n"
          "import copy\nimport io\nfrom PIL import Image, ImageDraw, ImageFont\n"
          "from xml.etree import ElementTree as E\n\n"
          + "".join(snippets))
    export="from theme_primitives import "+", ".join(("DEV","BOX",*FUNC_EXPORT))+"\n"
    kept="".join(part for i,part in enumerate(lines,1) if i not in excluded)
    anchor="from xml.etree import ElementTree as E\n"
    if kept.count(anchor)!=1:
        raise RuntimeError("Legacy import anchor changed")
    legacy_new=kept.replace(anchor,anchor+export,1)
    v4_text=originals[V4].decode("utf-8-sig")
    if v4_text.count("import interactive as base")!=1:
        raise RuntimeError("V4 direct import does not match expected source")
    v4_new=v4_text.replace("import interactive as base",
                           "import theme_primitives as base",1)
    # Prior to modifications, verify that each proposed script compiles.
    for name,value in (("shared",prim),("legacy",legacy_new),("v4",v4_new)):
        compile(value,name,"exec")
    tag=datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_dir=ROOT/"artifacts"/"organization"/("theme-primitives-refactor-"+tag)
    backup_dir.mkdir(parents=True,exist_ok=False)
    for p,content in originals.items():
        (backup_dir/p.name).write_bytes(content)
    backup_info={"status":"started","baseline":str(BASELINE.relative_to(ROOT)),
                 "originals":{p.name:{"sha256":digest(data),"bytes":len(data)}
                              for p,data in originals.items()},
                 "newModule":"studio_reverse/web_theme_pipeline/theme_primitives.py"}
    manifest=backup_dir/"manifest.json"
    manifest.write_text(json.dumps(backup_info,indent=2),encoding="utf8")
    try:
        PRIMITIVES.write_text(prim,encoding="utf8",newline="\n")
        LEGACY.write_text(legacy_new,encoding="utf8",newline="\n")
        V4.write_text(v4_new,encoding="utf8",newline="\n")
        checkfile=backup_dir/"after-signature.json"
        result=subprocess.run(
            [sys.executable,str(ROOT/"scripts"/"theme_signature.py"),
             "--output",str(checkfile),"--compare",str(BASELINE)],
            cwd=ROOT,capture_output=True,text=True,timeout=90)
        if result.returncode!=0:
            raise RuntimeError("Signature comparison failed: "+result.stdout+"\n"+result.stderr)
        backup_info["status"]="committed"
        backup_info["signature"]=json.loads(checkfile.read_text(encoding="utf8"))["sha256"]
        backup_info["comparison"]=result.stdout.strip()
        manifest.write_text(json.dumps(backup_info,indent=2),encoding="utf8")
        print("REFACTOR_SUCCESS")
        print("BACKUP",backup_dir)
        print("SIGNATURE",backup_info["signature"])
        print("MODIFIED",LEGACY.relative_to(ROOT),V4.relative_to(ROOT))
        print("CREATED",PRIMITIVES.relative_to(ROOT))
    except Exception as error:
        for p,content in originals.items():
            p.write_bytes(content)
        if PRIMITIVES.exists():PRIMITIVES.unlink()
        backup_info["status"]="rolled-back"
        backup_info["reason"]=str(error)
        manifest.write_text(json.dumps(backup_info,indent=2),encoding="utf8")
        print("REFACTOR_ROLLBACK",error,file=sys.stderr)
        raise

if __name__=="__main__":
    migrate()

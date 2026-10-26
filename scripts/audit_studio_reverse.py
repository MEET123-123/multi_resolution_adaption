"""Audit studio_reverse dependencies, duplicate names and external references.
Only creates new reports under artifacts/organization; no changes to source files.
"""
from __future__ import annotations
import argparse, ast, collections, datetime, json, os, re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
STUDIO=ROOT/"studio_reverse"
OUT=ROOT/"artifacts"/"organization"
EXCLUDE={".git",".venv",".venv-ml","__pycache__","node_modules","dist"}
EXTERNAL_DIRS=("src","tests","scripts","docs","configs","samples")
ROLES={
 "server.py":("preview", "多设备 HWT 预览服务", "active-experiment"),
 "browser_automation/server.py":("browser", "Playwright 操作 API", "active-experiment"),
 "browser_automation/test_browser.py":("test", "浏览器行为测试", "test"),
 "dataset_pipeline/generate.py":("static-dataset", "静态合成布局配对", "active-experiment"),
 "dataset_pipeline/audit.py":("static-dataset", "静态配对完整性检查", "validator"),
 "dataset_pipeline/pack.py":("static-dataset", "静态数据打包", "utility"),
 "web_theme_pipeline/collect.py":("licensed-assets", "公开许可背景素材检索", "active-experiment"),
 "web_theme_pipeline/interactive.py":("legacy-interactions", "早期固定状态交互模板", "legacy-keep"),
 "web_theme_pipeline/interaction_families_v4.py":("multi-interaction", "多交互图状态生成", "candidate-current"),
 "web_theme_pipeline/audit_interactions_v4.py":("multi-interaction", "结构/触发器审计", "validator"),
 "web_theme_pipeline/validate_pointer_v4.py":("multi-interaction", "手势命中回放", "validator"),
 "web_theme_pipeline/probe_slider_motion_v4.py":("multi-interaction", "滑块与动画探针", "diagnostic"),
 "web_theme_pipeline/browser_runtime_render.py":("quality", "由本地 Runtime 快照重建浏览器画面", "utility"),
 "web_theme_pipeline/runtime_quality_gate.py":("quality", "运行时质量门禁与 SFT 导出", "experimental-validator"),
 "web_theme_pipeline/patch_stateful.py":("legacy-interactions", "一次性补丁；不可直接当主入口", "migration-candidate"),
}
def scan_files(start):
    for base, dirs, names in os.walk(start):
        dirs[:]=[d for d in dirs if d not in EXCLUDE]
        for name in names:
            yield Path(base)/name
def small_text(path):
    try:
        if path.stat().st_size>500_000 or path.suffix.lower() not in (".py",".js",".html",".md",".json",".sh",".ps1",".bat"):
            return ""
        return path.read_text(encoding="utf-8-sig",errors="replace")
    except OSError:
        return ""
def dependencies(script,source):
    try: tree=ast.parse(source)
    except SyntaxError as e:return {"parseError":str(e)[:130]}
    imports=[];functions=[];calls=[]
    for node in ast.walk(tree):
        if isinstance(node,ast.Import):
            imports.extend(x.name for x in node.names)
        if isinstance(node,ast.ImportFrom):
            imports.append(("."*node.level)+(node.module or ""))
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.col_offset==0:
            functions.append(node.name)
        if isinstance(node,ast.Call):
            if isinstance(node.func,ast.Name) and node.func.id in ("subprocess","runpy"):
                calls.append(node.func.id)
    return {"imports":sorted(set(imports)),
            "topFunctions":sorted(set(functions)),
            "hasMainGuard":bool(re.search(r'if\s+__name__\s*==\s*["\']__main__["\']',source)),
            "mainGuardApproximate":True,
            "specialCalls":sorted(set(calls))}
def evidence(path):
    return {"path":path.relative_to(ROOT).as_posix(),"bytes":path.stat().st_size}
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--max-ref-files",type=int,default=25000)
    args=p.parse_args()
    if not STUDIO.is_dir():raise SystemExit("studio_reverse directory not found")
    files=[x for x in scan_files(STUDIO) if x.is_file() and x.suffix==".py"]
    file_paths={x.relative_to(STUDIO).as_posix():x for x in files}
    records={}; names=collections.defaultdict(list); script_text={}
    for rel,path in sorted(file_paths.items()):
        source=small_text(path)
        script_text[rel]=source
        deps=dependencies(path,source)
        role=ROLES.get(rel,("unclassified","待人工审阅","unknown"))
        records[rel]={"role":role[0],"purpose":role[1],"status":role[2],
                      "bytes":path.stat().st_size,**deps}
        for f in deps.get("topFunctions",[]):names[f].append(rel)
    same_names={n:items for n,items in names.items() if len(items)>=2}
    # Script-internal imports, based on local module basename and text references.
    internal=[]
    for rel,record in records.items():
        for imp in record.get("imports",[]):
            module=imp.lstrip(".").split(".")[0]
            for other in records:
                if other==rel:continue
                # Local imports resolve within the script directory first. A same-named
                # server.py elsewhere is not evidence of an actual import edge.
                if Path(other).stem==module and Path(other).parent==Path(rel).parent:
                    internal.append({"from":rel,"to":other,"kind":"same-directory import"})
    # Narrow external reference check, avoids scanning large datasets/assets.
    external=[]
    scripts={Path(r).stem:r for r in records}
    aliases=["studio_reverse", "web_theme_pipeline", "dataset_pipeline",
             "browser_automation", "patch_stateful.py","interaction_families_v4.py",
             "interactive.py","runtime_quality_gate.py"]
    maxfiles=0
    for folder in EXTERNAL_DIRS:
        path=ROOT/folder
        if not path.exists():continue
        for target in scan_files(path):
            if not target.is_file():continue
            maxfiles+=1
            if maxfiles>args.max_ref_files:break
            data=small_text(target)
            if not data:continue
            matched=[t for t in aliases if t in data]
            if matched:
                external.append({"file":target.relative_to(ROOT).as_posix(),
                                 "mentions":matched})
        if maxfiles>args.max_ref_files:break
    # Detect candidates only; never move or delete based on this.
    candidates=[]
    for rel,record in records.items():
        if record["status"] not in ("migration-candidate","legacy-keep"):continue
        importers=[r["from"] for r in internal if r["to"]==rel]
        candidates.append({"script":rel,"role":record["status"],
                           "internalImporters":importers,
                           "recommendation":"retain-current-path; review references and test before any move"})
    report={"generatedAt":datetime.datetime.now().isoformat(timespec="seconds"),
            "root":str(STUDIO),"scripts":records,"sameNamedFunctions":same_names,
            "internalImports":internal,"externalReferences":external,
            "referenceFilesVisited":maxfiles,"referenceScanPartial":maxfiles>args.max_ref_files,
            "candidates":candidates,
            "policy":"dependency/duplicate-name analysis only; not proof of equivalent behavior; no files moved"}
    OUT.mkdir(parents=True,exist_ok=True)
    now=datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    mdpath=OUT/("studio-audit-"+now+".md")
    jsonpath=OUT/("studio-audit-"+now+".json")
    jsonpath.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    lines=["# studio_reverse 脚本归类与依赖审计","",
           "仅审查，不移动或删除文件。**相同函数名不等于重复实现。**","",
           "## 当前脚本清单","",
           "| 脚本 | 作用 | 建议分类 | 大小 |","|---|---|---|---:|"]
    for rel,row in records.items():
        lines.append("| "+rel+" | "+row["purpose"]+" | "+row["status"]+" | "+str(row["bytes"])+" |")
    lines+=["","## 脚本间疑似导入",""]
    lines += [f'- {x["from"]} → {x["to"]} ({x["kind"]})' for x in internal] or ["无可静态确认的导入"]
    lines+=["","## 仓库外部引用",""]
    for x in external[:100]:
        lines.append("- "+x["file"]+": "+", ".join(x["mentions"]))
    if not external:lines.append("未发现匹配文本（不等于没有运行时引用）")
    lines+=["","## 可能重复的顶层函数名称",""]
    for name,which in sorted(same_names.items()):
        lines.append("- "+name+": "+", ".join(which))
    lines+=["","## 迁移前提","",
            "1. 一次性补丁与旧生成器先保持原路径，不直接删除。",
            "2. 修改前必须找到直接导入、硬编码路径与动态调用。",
            "3. 只合并语义相同、测试充分且有单一权威实现的函数。",
            "4. XML Runtime 应优先复用 src/multires_app 主包，不复制一套独立解释器。",
            "5. 本脚本只生成报告，不触碰 HWT、模型权重及训练数据。",
            ""]
    mdpath.write_text("\n".join(lines),encoding="utf-8")
    print("AUDIT_MD",mdpath)
    print("AUDIT_JSON",jsonpath)
    print("SCRIPTS",len(records),"DUPLICATE_NAMES",len(same_names),"IMPORT_EDGES",len(internal))
    print("NO_MOVE_OR_DELETE")
if __name__=="__main__":main()

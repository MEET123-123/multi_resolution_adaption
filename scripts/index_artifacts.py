"""Index artifacts without touching source data; reports stored under artifacts/organization."""
from __future__ import annotations
import argparse, datetime, json, os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/"artifacts"
CATEGORIES={
 "learning":"训练数据、SFT、生成主题和实验","models":"模型权重与推理资源",
 "evaluation":"评测证据","reports":"报告","logs":"日志",
 "organization":"迁移与目录索引","shots":"截图","research":"调研",
 "reference-pairs":"参考布局配对","reference-pairs-v2":"参考配对新版",
 "reference-pairs-regression":"参考图回归","corpus-adaptation":"批量适配",
 "corpus-fixes":"问题修复","automatic-matching":"视觉匹配",
 "target-reflow":"目标重排","generated-reference":"生成参考图",
 "workbench":"实验工作台","pip-cache":"依赖缓存（待核实）",
 "wheels":"安装包（待核实）","test-tmp":"测试暂存（待核实）",
 "env-tmp":"环境暂存（待核实）"}
FIELDS=("schema","total","themes","pairs","counts","splitCounts","splits","origin",
        "labelOrigin","supervision","nativeValidated","interactionVerified",
        "realDesignerGroundTruth","trainReady","complete")
def summarize(path,limit):
    # Fast inventory: cap recursion depth to avoid scanning multi-GB models/caches.
    if path.is_file():
        try:return {"files":1,"bytes":path.stat().st_size,"partial":False}
        except OSError:return {"files":0,"bytes":0,"partial":True}
    total=count=0;partial=False
    for base,dirs,files in os.walk(path):
        depth=len(Path(base).relative_to(path).parts)
        dirs[:]=[d for d in dirs if d not in (".git","__pycache__")]
        if depth>=1 and dirs:
            # Only aggregate immediate children. Full size is intentionally unknown.
            dirs.clear();partial=True
        for name in files:
            try:
                total+=(Path(base)/name).stat().st_size
                count+=1
            except OSError:pass
            if count>=limit:partial=True;break
        if partial:break
    return {"files":count,"bytes":total,"partial":partial}
def manifests(folder):
    result=[]
    if not folder.is_dir():return result
    for sub in ("manifest.json","dataset/manifest.json","reports/manifest.json","sft/manifest.json"):
        p=folder/sub
        try:
            if not p.is_file() or p.stat().st_size>1_000_000:continue
            raw=json.loads(p.read_text(encoding="utf-8-sig"))
            display_path=p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else p.as_posix()
            result.append({"file":display_path,
                           "fields":{key:raw[key] for key in FIELDS if isinstance(raw,dict) and key in raw}})
        except (OSError,UnicodeError,ValueError) as e:
            result.append({"file":sub,"error":str(e)[:100]})
    return result
def listing(folder,limit):
    return {"path":folder.relative_to(ROOT).as_posix(),"kind":"directory" if folder.is_dir() else "file",
            "stats":summarize(folder,limit),"manifests":manifests(folder)}
def human(byte_count):
    b=float(byte_count)
    for unit in ("B","KB","MB","GB","TB"):
        if b<1024:return f"{b:.1f} {unit}"
        b/=1024
    return f"{b:.1f} PB"
def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--max-files-per-entry",type=int,default=70000)
    args=parser.parse_args()
    if not BASE.exists():raise SystemExit("No artifacts/ directory")
    max_files=max(1000,args.max_files_per_entry)
    top=[]
    for p in sorted(BASE.iterdir(),key=lambda p:p.name.casefold()):
        entry=listing(p,max_files)
        entry["description"]=CATEGORIES.get(p.name,"其它历史实验")
        top.append(entry)
    sub=[]
    for category in ("learning","models","evaluation","organization"):
        folder=BASE/category
        if folder.is_dir():
            for p in sorted(folder.iterdir(),key=lambda p:p.name.casefold()):
                sub.append(listing(p,max_files))
    now=datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    out=BASE/"organization"
    out.mkdir(exist_ok=True)
    json_file=out/f"artifact-index-{now}.json"
    md_file=out/f"artifact-index-{now}.md"
    report={"generatedAt":datetime.datetime.now().isoformat(timespec="seconds"),
            "root":str(ROOT),"top":top,"detail":sub,"maxFilesPerEntry":max_files,
            "readOnly":True,"warning":"No data moved/deleted. Partial counts are not exhaustive."}
    json_file.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    md=["# Artifacts 实验产物索引","","本报告是只读盘点，未移动、覆盖或删除源文件。",
        "标记为 partial 时文件数量和大小并非全量统计。","",
        "## 一级目录","","| 目录 | 功能 | 文件数 | 估计体积 |", "|---|---|---:|---:|"]
    for r in top:
        s=r["stats"]
        md.append(f'| {r["path"]} | {r["description"]} | {s["files"]}'+
                  ("+" if s["partial"] else "")+f' | {human(s["bytes"])} |')
    md.extend(["","## 重要子目录和 Manifest","",
               "| 目录 | 文件数量 | 发现的 manifest |","|---|---:|---|"])
    for r in sub:
        paths="; ".join(m["file"] for m in r["manifests"]) or "未发现"
        s=r["stats"]
        md.append(f'| {r["path"]} | {s["files"]}'+("+" if s["partial"] else "")+f' | {paths} |')
    md+=["","## 安全边界","",
        "- learning/models/evaluation/organization 内的数据、权重和证据全部保留。",
        "- 临时或依赖缓存也不因名称含 tmp/cache 就自动删除。",
        "- 训练数据中的规则标签与人工真值应在 manifest 中区分。",
        "- 本工具只能建立索引，不会判断 HWT 是否通过华为原生引擎验收。",
        "","JSON 文件含各 manifest 的标签来源和状态字段。"]
    md_file.write_text("\n".join(md)+"\n",encoding="utf-8")
    print("INDEX_MD",md_file)
    print("INDEX_JSON",json_file)
    print("TOP_LEVEL",len(top),"DETAIL",len(sub))
    print("NO_MOVE_OR_DELETE")
if __name__=="__main__":main()

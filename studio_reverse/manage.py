"""Unified entry for experimental tools without moving existing scripts.

Examples (run from F:\\multi-res):
  python studio_reverse/manage.py list
  python studio_reverse/manage.py doctor
  python studio_reverse/manage.py artifacts-index
  python studio_reverse/manage.py studio-audit
  python studio_reverse/manage.py families --help
  python studio_reverse/manage.py browser --port 8797
"""
from __future__ import annotations
import argparse, importlib.util, os, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUDIO = ROOT / "studio_reverse"
SCRIPTS = {
    "preview": ("server.py", "本地多设备预览 (8796)"),
    "browser": ("browser_automation/server.py", "Playwright 浏览器操作 (8797)"),
    "collect": ("web_theme_pipeline/collect.py", "公开授权素材采集"),
    "families": ("web_theme_pipeline/interaction_families_v4.py", "推荐：V4 多交互主题生成"),
    "family-audit": ("web_theme_pipeline/audit_interactions_v4.py", "V4 交互结构检查"),
    "pointer-replay": ("web_theme_pipeline/validate_pointer_v4.py", "源主题手势命中回放"),
    "runtime-qa": ("web_theme_pipeline/runtime_quality_gate.py", "运行时浏览器质量门禁"),
    "slider-probe": ("web_theme_pipeline/probe_slider_motion_v4.py", "滑块/动画专项测试"),
    "static-generate": ("dataset_pipeline/generate.py", "早期静态合成数据"),
    "static-audit": ("dataset_pipeline/audit.py", "静态合成样本审计"),
    "static-pack": ("dataset_pipeline/pack.py", "静态合成数据压缩备份"),
    "interactive-legacy": ("web_theme_pipeline/interactive.py", "历史固定状态交互生成器"),
    "artifacts-index": ("../scripts/index_artifacts.py", "实验产物目录索引"),
    "studio-audit": ("../scripts/audit_studio_reverse.py", "脚本依赖与重复函数名审计"),
    "project-inventory": ("../scripts/project_inventory.py", "全项目静态索引"),
}
def resolve(name):
    if name not in SCRIPTS:
        raise KeyError(name)
    p=(STUDIO/SCRIPTS[name][0]).resolve()
    if not p.is_relative_to(ROOT):raise ValueError("unsafe tool path")
    if not p.is_file():raise FileNotFoundError(p)
    return p
def doctor():
    print("Project:",ROOT)
    print("Python:",sys.version.split()[0])
    print("Main package:", bool(importlib.util.find_spec("multires_app")))
    for name,lib in (("Pillow","PIL"),("Playwright","playwright")):
        print(name+":",bool(importlib.util.find_spec(lib)))
    print("Tool scripts:",sum(resolve(name).is_file() for name in SCRIPTS),"/",len(SCRIPTS))
    print("Raw themes directory:",(ROOT/"theme").is_dir())
    print("Experiment data directory:",(ROOT/"artifacts"/"learning").is_dir())
    print("No files changed by doctor.")
def main(argv=None):
    args=list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h","--help","list"):
        print("Experiment tool entry - run from anywhere")
        print("Usage: python studio_reverse/manage.py COMMAND [original tool arguments]")
        print("Special: list, doctor")
        for key,(_,description) in SCRIPTS.items():
            print(f"  {key:19} {description}")
        return 0
    command=args[0]
    if command=="doctor":
        doctor();return 0
    if command not in SCRIPTS:
        print("Unknown command: "+command, file=sys.stderr)
        return 2
    script=resolve(command)
    env=os.environ.copy()
    prefix=str(ROOT/"src")
    env["PYTHONPATH"]=prefix+os.pathsep+env.get("PYTHONPATH","")
    print("Dispatch:",command,"->",script.relative_to(ROOT),flush=True)
    try:
        process=subprocess.run([sys.executable,str(script),*args[1:]],cwd=ROOT,env=env,check=False)
        return process.returncode
    except KeyboardInterrupt:
        return 130
if __name__=="__main__":raise SystemExit(main())

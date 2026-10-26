"""Declare the public CLI; no engine work happens while constructing help."""

import argparse
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="鸿蒙锁屏主题多分辨率分析/回写原型")
    sub = parser.add_subparsers(dest="command", required=True)
    p_analyze = sub.add_parser("analyze", help="分析主题目录、XML 或 ZIP/HWT 压缩包")
    p_analyze.add_argument("source", type=Path)
    p_analyze.add_argument(
        "--engine",
        choices=["auto", "harmonyos-5+", "harmonyos-4.x-or-earlier", "unknown"],
        default="auto",
    )
    p_analyze.add_argument("--out", type=Path, default=Path("multires-analysis.json"))
    p_apply = sub.add_parser("apply", help="根据人工审核的 patch JSON 回写单个 XML 脚本")
    p_apply.add_argument("script", type=Path)
    p_apply.add_argument("--patches", type=Path, required=True)
    p_apply.add_argument("--target", help="布局计划中的目标 profile id")
    p_apply.add_argument("--out", type=Path, required=True)
    p_layout = sub.add_parser("layout", help="将分析结果送入可插拔布局算法")
    p_layout.add_argument("analysis", type=Path)
    p_layout.add_argument("--source-profile", type=Path, required=True)
    p_layout.add_argument("--profiles", type=Path, required=True)
    p_layout.add_argument(
        "--algorithm", default="demo", help="demo 或 Python 算法 module:Class / file.py:Class"
    )
    p_layout.add_argument("--out", type=Path, default=Path("layout-plan.json"))
    p_preview = sub.add_parser("preview", help="从布局计划生成独立 HTML/SVG 预览器")
    p_preview.add_argument("layout_plan", type=Path)
    p_preview.add_argument("--source-image", type=Path, help="将主题包预览图嵌入预览器")
    p_preview.add_argument("--out", type=Path, default=Path("layout-preview.html"))
    p_scenes = sub.add_parser("scenes", help="解析锁屏交互状态，真实素材渲染、组件重排与 XML 回写")
    p_scenes.add_argument("source", type=Path)
    p_scenes.add_argument("--source-profile", type=Path, required=True)
    p_scenes.add_argument("--profiles", type=Path, required=True)
    p_scenes.add_argument(
        "--algorithm",
        default="anchor",
        help="anchor 九宫格/容器/约束求解；adaptive 旧版两列重排；或 file.py:Class",
    )
    p_scenes.add_argument("--components", type=Path, help="覆盖自动推断的组件成员与角色")
    p_scenes.add_argument("--anchor-config", type=Path, help="锚点、嵌套容器和关系约束 JSON")
    p_scenes.add_argument("--inputs", type=Path, help="预览系统输入和初始持久变量 JSON")
    p_scenes.add_argument("--max-states", type=int, default=48)
    p_scenes.add_argument("--max-depth", type=int, default=4)
    p_scenes.add_argument("--out", type=Path, default=Path("scene-project"))
    p_export = sub.add_parser("export", help="将场景重排脚本写入新的 HWT 包")
    p_export.add_argument("project", type=Path)
    p_export.add_argument("--target", required=True)
    p_export.add_argument("--out", type=Path, required=True)
    p_relayout = sub.add_parser("relayout", help="使用缓存场景更换布局算法或组件配置")
    p_relayout.add_argument("project", type=Path)
    p_relayout.add_argument("--algorithm", default="anchor")
    p_relayout.add_argument("--components", type=Path)
    p_relayout.add_argument("--anchor-config", type=Path)
    p_serve = sub.add_parser("serve", help="启动本地实时交互解释器和布局聚类预览")
    p_serve.add_argument("project", type=Path)
    p_serve.add_argument("--port", type=int, default=8765)
    p_live = sub.add_parser("live", help="实时预览主题包：导入 HWT、手势执行与动画播放")
    p_live.add_argument(
        "source", type=Path, nargs="?", help="要预览的 .hwt/.zip，留空则启动后在页面里导入"
    )
    p_live.add_argument(
        "--port", type=int, default=8767, help="监听端口，默认 8767（serve 用的是 8766）"
    )
    p_live.add_argument(
        "--out", type=Path, default=Path("preview-live"), help="素材解包目录，默认 preview-live"
    )
    p_live.add_argument("--no-import", action="store_true", help="只预览启动时指定的主题包")
    p_live.add_argument("--vlm-config", type=Path, help="布局重构的 OpenAI 兼容服务配置 JSON")
    p_tools = sub.add_parser("tools", help="批量转换、重新布局、打包和服务管理工具")
    p_tools.add_argument("tool_args", nargs=argparse.REMAINDER)
    return parser

# Python 主引擎阅读导航

负责已有 HWT/XML 的真实状态探索、多分辨率求解和保真反写。新设计编辑器在 [theme_studio_v2](../../theme_studio_v2/README.md)，二者的输入协议与职责不同。

## 第一条阅读路径

`__main__.py → cli/parser.py → cli/commands.py → pipeline/projects.py`

然后沿 `create_project → create_model_project → scene_clusters.schedule_device → export/scene_export.export_script` 阅读完整流程。

| 职责 | 入口 |
|---|---|
| 参数声明 / 命令执行 | `cli/parser.py` / `cli/commands.py` |
| 静态 XML 分析 | `engine/theme_analysis.py` |
| 表达式与动画解释 | `engine/scene_engine.py` |
| 手势、可达状态与绑定 | `interaction/interaction_semantics.py` / `interaction_ir.py` |
| 项目创建 / 缓存重排 | `pipeline/projects.py` |
| 组件推断 / 逻辑画布 | `pipeline/components.py` / `pipeline/profiles.py` |
| 布局插件协议与加载 | `layout/scene_api.py`，旧分析布局接口为 `layout/layout_api.py` |
| 锚点和约束 | `layout/anchor_ir.py` / `anchor_layout.py` / `anchor_solver.py` |
| 状态条件编译 | `export/state_guards.py` |
| XML / HWT 导出 | `export/scene_export.py`，精确保留源文本为 `export/layout_writeback.py` |
| 显式 JSON 补丁 | `export/xml_patches.py` |
| 预览 HTTP / 运行会话 | `preview/live_preview.py` / `preview/live_session.py` |
| 预览动画 / 几何与 Slider | `preview/live_animation.py` / `preview/live_geometry.py` |
| 参考图匹配 | `preview/target_layout.py` / `automatic_matching.py` |
| 模型构图规划 / 求解 / 反馈 | `reconstruction/contract.py` / `solver.py` / `workflow.py` |
| 评价 / 数据与模型实验 / 在线任务 | `evaluation/` / `learning/` / `service/` |
| 批量工具 | `tools/runner.py` 中的命令注册表 |

## 兼容入口

`pipeline/scene_pipeline.py` 保留旧公开符号；`cli/multires.py` 保留旧分析 API；`preview/live_preview.py` 保留原 `LiveSession` 和辅助函数导入。新代码直接引用职责模块，已有项目和自定义插件不需要批量改路径。

目前部分业务仍存在动态导入和跨模块协作，没有宣称整个项目已经成为无环架构。拆分时保留了已抽取函数/类的语法树；统一格式使用 Black 的等价检查。

## 数据放置

配置在根 `configs/`，小示例在 `samples/`。源 HWT 与历史适配仍在 `theme/`；新运行产物写 `artifacts/`，不是源码依赖。不要将模型、主题和本地安装程序复制到此目录。

## 开发格式

从仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m black src/multires_app multires.py
```

四空格、100 列、明确的流程分支。避免重新压缩成分号链和长单行字典。

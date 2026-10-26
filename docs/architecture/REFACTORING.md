# 项目重构与阅读路径（2026-10-24）

## 目录职责

| 位置 | 职责 | 推荐入口 |
|---|---|---|
| `src/multires_app/` | 已有主题的脚本引擎、状态探索、适配、保真写回 | [模块导航](../../src/multires_app/README.md) |
| `theme_studio_v2/` | 从设计创建锁屏，推断目标布局并编译脚本 | [使用与开发](../../theme_studio_v2/README.md) |
| `theme_studio/` | 旧桌面程序，本地格式参考 | 不作为新版源码或依赖提交 |
| `studio_reverse/` | 独立研究和自动化实验 | 保留已有工作，未合并到主业务 |
| `theme/` / `artifacts/` / 模型工作区 | 输入、历史交付、运行证据与实验数据 | 保留路径，未当作临时目录清空 |

## Python：从大入口拆成职责模块

| 原入口 | 新模块 |
|---|---|
| `cli/multires.py` 混合参数、分析和写回 | `cli/parser.py`、`cli/commands.py`、`engine/theme_analysis.py`、`export/xml_patches.py` |
| `pipeline/scene_pipeline.py` 混合组件、算法、生成和导出 | `pipeline/projects.py`、`components.py`、`profiles.py`、`layout/scene_api.py`、`layout/geometry.py`、`export/state_guards.py`、`scene_export.py` |
| `preview/live_preview.py` 混合 HTTP、会话和动画 | `preview/live_preview.py`、`live_session.py`、`live_geometry.py`、`live_animation.py` |

旧导入路径保留显式转出层。现有工具和插件可继续导入 `scene_pipeline` 等路径；新代码使用职责模块。格式使用 Black 25.1.0、100 列，避免分号链和压缩控制流；`.editorconfig` 统一编辑器约定。

拆分没有变更布局算法、交互和动画的业务规则。迁出的 36 个顶层函数/类经过静态 AST 对照，定义保持一致。不是一次重写所有领域模型，也没有宣称消除了全仓库所有循环依赖。

## 新编辑器：设计推断、布局、编译分离

```text
core/types + document
  → layout/legacy：参考画布度量与显式变体
  → layout/infer：语义、分组、锚点
  → layout/planner：构图、可用区域、组框
  → layout/adaptive：校验与实际组变换
  → ResolvedScene
     → renderer：预览
     → validate：布局问题
     → export/materialize → XML / 实际素材 / HWT 候选
```

`LayoutPlanner` 是替换算法的接口。设备配置支持任意分辨率、逻辑密度、安全区、姿态和显式障碍；内置数据是示例，不声称为官方机型参数。自动流程不依赖主题名称，也不需要对每个设备填写位置变体。

新编辑器不直接替代 Python 的完整 HWT 状态分析；视频、序列帧、位图字库和原脚本交互不能靠静态设计 JSON 无损迁移。具体边界和扩展点见 [响应式架构](../../theme_studio_v2/docs/RESPONSIVE_ARCHITECTURE.md)。

## 本轮检查和产物

- Python：161 个模块语法解析，迁出的 36 个定义 AST 对照、兼容入口导入和迁出模块全局符号静态检查。
- 前端：TypeScript 类型检查和 Vite 构建。
- 示例：从无逐机型变体的设计生成 24 个设备/方向状态；生产流程执行布局门禁，生成逐目标目录和 ZIP。
- 没有运行或新增单元/回归测试；未进行鸿蒙真机验收。上述示例结果不等于所有真实主题已验证。

本地检查证据在 `artifacts/refactoring/`；源码初始副本在 `before/`。前端示例交付的最终位置以本轮报告 [REFACTORING_RESULTS](../reports/REFACTORING_RESULTS.md) 为准。

## 后续修改约定

1. 新 CLI 只负责参数和调用，不放领域算法。
2. 算法、编译器和 UI 分层；预览与交付共用实际求解结果。
3. 主题输入和模型输出作为数据，不加载主题包携带的可执行插件。
4. 新增设备使用配置，不添加机型名称分支。
5. 保留候选与失败证据；失败目标不混入旧版成功文件。
6. 新项目默认创建于 `G:\codex_workspace`，当前项目继续位于 `F:\multi-res`。

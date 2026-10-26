# 多交互主题生成与评测工具索引

本目录是研究/实验管线，不是主程序的 XML Runtime。完整业务代码在 `src/multires_app/`，本目录生成的 HWT 为候选包，规则布局为弱监督。

**统一运行方式**：在 `F:\multi-res` 根目录运行 `python studio_reverse/manage.py <命令> [参数]`。工具实际路径仍保持不变。

## 推荐流程

| 阶段 | 统一子命令 | 实际脚本 | 产物 |
|---|---|---|---|
| 1. 素材检索 | `collect` | `collect.py` | Commons 元数据、授权和背景图片 |
| 2. 多交互主题 | `families` | `interaction_families_v4.py` | HWT 候选、状态图、6 设备配对 |
| 3. 脚本语义检查 | `family-audit` | `audit_interactions_v4.py` | XML 触发器/变量结构报告 |
| 4. 点击/长按回放 | `pointer-replay` | `validate_pointer_v4.py` | 手势路径命中结果 |
| 5. 浏览器渲染质量门禁 | `runtime-qa` | `runtime_quality_gate.py` | 浏览器截图、错误报告、合格 SFT |
| 6. 训练 | **主项目入口** | `src/multires_app/learning/qwen_runner.py` | Qwen3-VL 微调/评测；必须单独验证 |

示例（只是帮助命令，不写数据）：

```powershell
.\.venv\Scripts\python.exe studio_reverse\manage.py families --help
.\.venv\Scripts\python.exe studio_reverse\manage.py runtime-qa --help
```

## 辅助与历史脚本

| 文件 | 分类 | 保留原因 |
|---|---|---|
| `interactive.py` | **legacy-compatible** | 旧固定状态入口。已通过 `from theme_primitives import ...` 保留兼容，不再作为 V4 的底层依赖 |
| `patch_stateful.py` | **一次性补丁 / 归档候选** | 会修改源文件，不属于日常 CLI。当前不移动、不删除、不暴露为统一命令 |
| `probe_slider_motion_v4.py` | 诊断工具 | 滑块终点和动画轨道专项检查 |
| `browser_runtime_render.py` | 底层工具 | 质量门禁调用的浏览器绘制函数，不能单独当训练结果 |
| `validate_pointer_v4.py` | 回放验证 | 与结构审计不同，不重复 |
| `README_RUNTIME_QA.md` | 运行时检查说明 | 指明本地 Runtime 与真机验证边界 |

`interactive.py` 不能简单归档：目前 V4 直接导入它。真正拆除依赖，需要先把被复用的常量、绘图工具、模板等提取为独立模块，并进行 8 种交互结构回归。

## 不可混淆的三个通过级别

- **静态校验通过**：XML、资源映射或配对 schema 可以读取。
- **本地交互及截图通过**：项目的 `LiveSession` 模拟器可重放状态。
- **华为原生引擎/真机通过**：单独验收，不可由前两项推断。

## 数据集标签

输出仍保存在独立的 `artifacts/learning/<dataset-version>/` 中。任何 `rule-generated` 都只能算弱监督。训练/验证/测试要按源主题或交互家族隔离。

## 进一步重构

下一步将 V4 从旧脚本 `interactive.py` 中引用的公共能力抽到单独模块 `common/`，但必须先写回归和包结构验收。当前工具已归类、入口已统一，**源码保留原路径**。

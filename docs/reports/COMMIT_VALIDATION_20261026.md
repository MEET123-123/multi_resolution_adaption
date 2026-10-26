# 2026-10-26 源码提交检查

本次提交保存工作区现有的 Python 模块拆分、Theme Studio v2 响应式编辑器、浏览器预览与数据集实验工具，以及对应测试和架构文档。

本地原始主题、采集图片和视频、浏览器会话、Python 依赖、模型及生成产物不进入版本库。`datasets/tools/*.py` 保留，其他 `datasets/` 内容保持本地。

## 实际执行结果

| 检查 | 结果 |
|---|---|
| Python 源码 AST 语法解析 | 300 个模块通过；`project_inventory.py` 有非阻断的转义字符警告 |
| 常见私钥、GitHub/OpenAI 密钥和 AWS 访问密钥模式扫描 | 未发现匹配；此检查不等于全面安全审计 |
| Python `unittest discover -s tests` | 355 项，352 项通过、3 项错误，见下文 |
| `test_layout_reconstruction.py` 在 `PYTHONUTF8=1` 下重跑 | 12 项全部通过，确认其中一个错误来自 Windows 默认 GBK 编码 |
| 编辑器 Vitest | 39 项，33 项通过、6 项失败，见下文 |
| 编辑器 TypeScript `tsc --noEmit` | 通过 |
| 编辑器 Vite 生产构建 | 通过 |

未在本轮修改业务逻辑来消除测试失败。构建通过不代表全部适配与导出功能已经通过回归。

## Python 尚未解决的问题

- `test_adaptive_layout.AdaptiveIntegrationTests.test_default_auto_policy_writeback_and_source_archive`：导出前屏内约束检查抛出 `RequiredConstraintError`，一个稳定按钮资源的目标边界越出画布。
- `test_scene_flow.SceneFlowTests.test_export_matches_reflow_and_preserves_archive`：期望交互后的 `panel` 状态时发生 `KeyError`。
- 默认 Windows 编码下，`test_layout_reconstruction.ReconstructionWorkflowTests.test_changed_request_or_source_rejected` 使用未显式指定编码的 `Path.read_text()` 触发 GBK 解码异常。使用 UTF-8 模式重跑该测试模块全部通过；测试代码本身尚未修复。

## 编辑器尚未解决的问题

- 3 项 HWT 导出测试仍使用位图时钟资源；当前导出器明确拒绝缺少整套数字资源族缩放能力的位图时钟。
- 2 项批量导出测试按 12 台设备断言，当前导出包含旋转后的 24 个设备状态；当前测试实际报告 18 个状态无错误，仍需逐项定位其余 6 个状态的错误，不能只修改数量断言。
- 1 项示例布局测试要求所有设备均无警告，实际折叠屏 `medium` 断点缺少变体，触发布局诊断。

## 复现

在仓库根目录使用项目 Python 环境：

```powershell
$env:PYTHONUTF8 = '1'
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'
```

在 `theme_studio_v2/` 安装依赖后执行：

```powershell
npm ci
npm test
npm run build
```

本机没有独立的 `npm` 命令，因此本次前端验证通过 Node 直接调用已安装的 `vitest/vitest.mjs`、`typescript/bin/tsc` 和 `vite/bin/vite.js`。

Python 测试日志保存在本地 `artifacts/logs/commit-20261026-*.log`，不随源码上传。部分实验测试依赖现有 `artifacts/` 数据；干净克隆需要先生成相应测试素材。

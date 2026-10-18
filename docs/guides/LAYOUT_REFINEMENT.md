# 布局 JSON 微调与参考图匹配

两条路线都先生成建议，再审查、应用。项目不调用大模型服务；请求 JSON 可自行交给任何模型。主题中的变量、命令、资源路径不会从模型返回值中执行或替换。

## 离线 JSON 流程

先选择项目、目标设备和交互状态。目标 ID 在 `project.json` 的 `devices[].profile.id` 中查看：

```powershell
.\.venv\Scripts\python.exe layout_refinement.py export theme/adaptive-code-demo/bear/project.json --target tablet-landscape --state state-000 --out layout-request.json
```

导出内容包含原始 bbox、当前 bbox、组件语义/分组、安全区、物理分辨率、逻辑画布，以及请求摘要。**bbox 使用主题引擎逻辑坐标，不是截图像素。** 将完整请求交给模型，让它填写 `responseTemplate`，只返回需要变化的组件：

```json
{
  "schemaVersion": 1,
  "kind": "layout-refinement-proposal",
  "target": "tablet-landscape",
  "state": "state-000",
  "baseDigest": "使用导出请求中的完整摘要",
  "applyScope": "matching-components",
  "components": [
    {"id": "使用请求中真实的组件 ID", "bbox": {"x": 40, "y": 30, "w": 200, "h": 80}}
  ]
}
```

示例数值仅说明结构。宽高必须与该组件源 bbox 保持同一比例，不能直接用于真实主题。

```powershell
.\.venv\Scripts\python.exe layout_refinement.py apply theme/adaptive-code-demo/bear/project.json --proposal layout-proposal.json
```

应用前检查摘要、ID、有限数值、正尺寸、安全区、等比缩放、显式同组变换及新产生的组件重叠。旧建议不能覆盖已经变化的布局。背景和全屏手势层默认锁定。模型输出是待校验数据，不能携带可执行布局插件。

`matching-components` 为默认范围：将调整作为组件锚点，应用到该目标中几何相同的已有状态，并逐个验证这些状态。以后出现相同几何的实时状态时，也尝试复用锚点；若出现碰撞或越界，使用自动布局并记录 `manual-refinement-conflict`。组件结构/几何变了则需要另行审查。锚点与变量运行状态分别保存，重新加载项目仍有效。

`applyScope: "state"` 只修改指定状态。若原生状态条件无法区分两个画面，而它们要求不同变换，导出将明确报错，防止生成条件有歧义的 XML。此时应使用一致锚点，或在原主题中增加可区分的状态变量。

写回会先编译 XML 和预览，再替换 `project.json`、`scripts/<target>.xml`、`preview.html`；保存 `project.layout-backup-*.json` 和应用报告。提交失败时恢复已替换文件。该操作具备异常回滚，但不等同于断电安全的数据库事务；在线服务另外以不可变版本隔离未完成结果。

需要 HWT 时继续导出：

```powershell
.\.venv\Scripts\python.exe multires.py export theme/adaptive-code-demo/bear/project.json --target tablet-landscape --out reviewed-bear.hwt
```

## 预期渲染图

```powershell
.\.venv\Scripts\python.exe layout_refinement.py match theme/adaptive-code-demo/bear/project.json --target tablet-landscape --state state-000 --image expected.png --out reference-proposal.json
```

使用与目标画布同宽高比、裁掉系统栏和设备外框的 PNG/JPEG/WebP。文件不超过 10MB、2000 万像素，宽高比容差 2.5%。实际图片可高于或低于目标分辨率，匹配会换算为逻辑 bbox。

当前实现是透明蒙版、多尺度模板匹配：对每个可匹配组件选择主要位图，从参考图中的位置和尺度推导整个组件的等比变换。最多匹配 24 个组件，默认置信度至少 0.78、与次优峰值差至少 0.035。重复图标、遮挡、纯文本、无可读位图、参考图重绘了角色等情况可能无法可靠匹配；未匹配项保持原样，原因记录在建议诊断中。

它能处理已有素材重新摆放和缩放，不能理解任意新构图，也不会生成新资源。输出仍是 proposal，审查后通过同一套校验应用。参考图不会发送给第三方模型。

## 预览入口

生成的预览包含布局 JSON/参考图微调面板。直接打开 HTML 可导出/导入 JSON、显示建议框并调整当前画面的预览；离线 HTML 不会直接写硬盘，持久写回使用 CLI。

```powershell
.\.venv\Scripts\python.exe multires.py serve theme/adaptive-code-demo/bear/project.json --port 8765
```

本地服务中可匹配参考图、应用到当前会话及后续相同组件，并通过会话导出保存修改后的项目和 HWT。本地服务仅供本机调试。客户入口见 [SAAS_READINESS.md](SAAS_READINESS.md)；布局规则见 [ADAPTATION_WORKFLOW.md](ADAPTATION_WORKFLOW.md)。

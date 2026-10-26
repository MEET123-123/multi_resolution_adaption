# Runtime QA：多交互主题自动验收

本目录现在增加两个模块：

- `browser_runtime_render.py`：把现有 `LiveSession.plan()` 中的图片、文本和实际坐标交给 Chromium/Edge 渲染并截图，记录资源缺失与不支持的媒体。
- `runtime_quality_gate.py`：在源主题与六设备适配主题上按状态路径重放真实手势，核对最终变量、公共时钟/日期跨状态位置与浏览器渲染问题；仅将通过的样本导出至新的 `sft-gated/`。

注意：浏览器截图是本项目 XML 解释器的渲染，不是华为真机引擎结果；规则几何答案仍是弱监督。

在 `F:\\multi-res` 根目录运行：

```powershell
.\\.venv\\Scripts\\python.exe -m py_compile studio_reverse\\web_theme_pipeline\\browser_runtime_render.py studio_reverse\\web_theme_pipeline\\runtime_quality_gate.py

.\\.venv\\Scripts\\python.exe studio_reverse\\web_theme_pipeline\\interaction_families_v4.py --catalog artifacts\\learning\\web-themes-1000\\catalog.json --out artifacts\\learning\\interaction-families-v4-runtime-qa --themes 8

.\\.venv\\Scripts\\python.exe studio_reverse\\web_theme_pipeline\\audit_interactions_v4.py --dataset artifacts\\learning\\interaction-families-v4-runtime-qa

.\\.venv\\Scripts\\python.exe studio_reverse\\web_theme_pipeline\\runtime_quality_gate.py --dataset artifacts\\learning\\interaction-families-v4-runtime-qa --out artifacts\\learning\\interaction-families-v4-runtime-qa-results --limit 12
```

当小样本真实回放及截图验收通过后，再更换新的输出目录并移除 `--limit` 执行全量门禁。

门禁产物包括 `report.json`、`records.jsonl`、`source/`、`target/`、`sft-gated/`，不修改原始 SFT。 `motion_control` 的真实时间相位恢复和 `swipe_carousel` 的连续滑动在未专项验收前默认不通过。

只有 `sft-gated/train.jsonl` 非空且有保留测试集，才能运行 Qwen3-VL 的弱监督微调烟测；不把训练损失视为真实布局设计质量。

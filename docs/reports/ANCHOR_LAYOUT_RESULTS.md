# AnchorLayout V1 静态轮次结果（媒体首帧改造前）

后续媒体首帧改造与最新验证见 [MEDIA_LAYOUT_RESULTS.md](MEDIA_LAYOUT_RESULTS.md) 和 [跨时刻报告](../../theme/adaptation/media-validation.json)。以下数据保留为上一轮静态检查记录。

2026-10-17。完整实现、配置与限制见 [ANCHOR_LAYOUT.md](../guides/ANCHOR_LAYOUT.md)。

## 已通过的工程检查

| 项目 | 实测结果 |
|---|---:|
| 自动测试 | 104 项通过 |
| 原始 HWT | 24 个，SHA256 全部保持不变 |
| 场景项目 | 25 个，包含 UCD 锁屏变体 |
| 有界采样源状态 | 1,670 个 |
| 设备/方向布局及 XML | 550 份 |
| 候选 HWT | 48 个 |
| 采样反写坐标比较 | 6,132 次，差异 0 |
| 跳过的原生字形等估计项 | 154 次 |
| 代表性交互与撤销检查 | 失败 0 |
| 生成预览 JavaScript | 25 页语法检查通过 |
| 最终布局选择 | 443 个设备/方向使用锚点方案，107 个回退旧候选 |

所有生成 XML 解析并经过精确字段补丁校验。代表性包为平板横屏、双折叠展开竖屏；每个包只抽查初始/中间/末尾状态的反写坐标，并从初始状态执行一个可用完整手势。其他分支、原生字形、视频时序、安装与设备行为不在通过范围内。

浏览器实际检查了原始直板/平板横屏对比、九宫格和组轮廓、真实点击的新场景、撤销恢复，以及“求解并应用”的成功结果。截图位于 `theme/adaptation/anchor-preview.png` 和 `anchor-editor-proof.png`。

## 构图复核仍然需要

同一批源场景、相同组件，在两个代表设备上比较全部采样状态，共 50 个项目/设备组合。以下是**组件矩形代理指标**，不是像素遮挡检测，也不是设计师确认的视觉错误：

| 候选 | 新增裁剪候选快照 | 原来不相交的组件新增重叠候选 | 公共冻结漂移 |
|---|---:|---:|---:|
| 旧算法按自身调度 | 0 | 120 | 0（按自身请求） |
| 旧候选补齐与新方案相同的公共冻结要求 | 0 | 756 | 0 |
| 最终选择的锚点/回退候选 | 19 | 293 | 0 |

旧算法会在局部冲突时提升到整页重排，因此第一行不具备与后两行完全相同的冻结条件，不能据此宣称新方案视觉更好。最终 50 个代表组合中，12 个仍有上述裁剪或重叠复核候选。对存在指标取舍、透明图层或有意叠加的主题，不自动把矩形相交当作设计错误；报告保留候选与原因，供明确分组、锚点和禁止遮挡关系后重新求解。

**结论：V1 功能链路与工程检查完成；所有主题无需人工修改的效果验收尚未完成。** 没有人工金标准，分组准确率、一次通过率、人工修正耗时未测；原生设备安装、动态资源连续性及全主题兼容验收也未完成。

## 查看与复现

- 本地服务：[主题目录](http://127.0.0.1:8766/)。
- 明细：`theme/adaptation/anchor-validation-summary.json`。
- 同场景比较：`theme/adaptation/anchor-ab-report.json`。
- 每包的 `scenes/anchor-layout.json` 给出组、锚点、约束残差、布局选择与复核标记。
- 每份 XML 的 `scripts/*.patch.json` 给出实际字段差异。

```powershell
.\.venv\Scripts\python.exe tests/run_quality_checks.py
.\.venv\Scripts\python.exe .\multires.py tools iterate-anchor-layout --workers 3
.\.venv\Scripts\python.exe tests/validate_anchor_real.py
.\.venv\Scripts\python.exe tests/check_preview_javascript.py node
```

新增真实包用 `batch_convert.py --resume --relayout`；替换算法仍使用原 `layout_scene()` 插件接口。显式布局规则冲突会被拒绝，不能为追求自动通过而删除公共组件锁定。

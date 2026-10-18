# 稳定场景边界与平衡缩放

## 处理规则

- 缩放以源、目标屏幕比例和资源占屏比例共同决定。接近原比例时沿宽度缩放；大幅改变比例时平衡宽高变化，默认相对面积倍率上限为 1.25，单轴占比倍率上限为 1.6。
- 在此基准上，对稳定场景的可见资源和按钮热区施加安全区域硬约束。使用组内实际资源矩形，覆盖负坐标子元素和大于图片的按钮；组内保持统一等比缩放及相对位置。
- 贴边、居中、底部均分、九宫格关系继续参与求解。空间不足时优先通过缩小和调整位置解决；推断关系是软偏好，不能保证每项都与原作精确一致。
- 序列帧、视频、入场和离场动画沿用同一仿射变换。边界限制针对稳定场景，动画途中可以出屏。背景保持铺满画布。
- 原始稳定场景中完全位于屏外的待展开内容保持屏外。人工交互使其出现后，转入局部布局；未变化的公共组件仍被冻结。内容计数等变化只要不引入越界，继续复用布局。
- 实时交互的新状态同步保存原始直板机快照，使用相同的活跃资源识别与边界规则。手势失败时一并回滚快照。
- 导出时复查稳定资源的最终变换，阻止布局复用、质量回退或手动修改重新引入越界。人工硬约束或冻结要求与边界矛盾时拒绝导出。

## 扩展接口

自有算法仍实现 `layout_scene(components, source, target, context)`，返回每个组件的 `{sx, sy, tx, ty}`。`context.stableResources` 提供当前稳定资源，`context.mediaLayout` 提供首帧参考。最终边界检查独立于求解器，反写接口保持不变。

配置项见 [ANCHOR_LAYOUT.md](../guides/ANCHOR_LAYOUT.md)。实现分布在 `visual_scaling.py`、`canvas_bounds.py`、`anchor_layout.py`、`anchor_solver.py`、`scene_clusters.py`、`anchor_validation.py` 和 `interaction_server.py`。

## 验证证据

边界阶段实际结果（2026-10-17）：25 个项目、550 个设备方向、36,740 个缓存稳定场景，共检查 1,688,646 个可解析资源快照，越界为 0。修改前两个代表性目标有 10,952 / 12,508 个组件快照超过视觉占比预算；修改后剩余 65 个，集中于三个主题的动态组合外框，保留逐项复核记录。占比预算的软代理改善不代表全部设计意图已精确恢复。

- `quality-results.json`：完整自动回归。
- `theme/adaptation/canvas-validation.json`：全部设备、全部缓存稳定场景的资源边界审计。
- `theme/adaptation/visual-similarity-before.json`：修改前的视觉占比基线。
- `theme/adaptation/visual-similarity-validation.json`：相同两个代表性目标和全部缓存状态的修改后占比。
- `theme/adaptation/media-validation.json`：后续媒体帧沿用固定变换的采样检查。
- `theme/adaptation/screen-relations-validation.json`：推断锚点误差与公共组件冻结检查。
- `theme/adaptation/batch-summary.json`：候选包反写、打包、交互及原包哈希检查。

仅验证解释器已解析的资源和已探索的交互状态。缺失素材、不可解释表达式、未探索状态、动画全过程边界及原生设备表现不属于此次通过范围；图片检查使用矩形几何，不是像素或审美等价验收。

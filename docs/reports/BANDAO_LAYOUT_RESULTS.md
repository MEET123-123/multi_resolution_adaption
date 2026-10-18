# 半岛日记改：时间纵向重叠修复

本报告记录上一轮时间修复。后续补充屏外面板、动画解释及后出现按钮绑定，并重新探索交互；当前结果见 [面板交互修复报告](BANDAO_DRAWER_RESULTS.md)。

## 原因

四张小时、分钟图片带有 PositionAnimation，原有组件推断把它们归为 decoration。装饰元素绕过布局求解器和组件间防重叠检查，分别按画布比例定位；图片自身等比缩放后，上下两行的距离不足，下方问候语区域也会受到挤压。

## 修改

- 新增 clock_semantics.py：识别图片内容表达式中的小时、分钟、日期变量及其间接引用。纯时间组件优先作为布局内容，带入场动画也不会被当成装饰。
- 在新建项目及缓存项目重新布局时自动更新组件语义；保留手动布局设置。
- 四张数字图片参与现有上下间距、共享对齐线、画布边界及导出校验。原始动画、交互和素材保持原脚本结构。

## 验证

- 重新求解 22 个设备方向、96 个缓存状态，共 2,112 个布局。
- 时间与问候语、日期的专项位置关系检查：11,000 次，违规从 2,210 次降到 0。
- 组件间横向关系 14,996 次、纵向关系 36,924 次：间距违规 0，导出错误 0。
- 稳定场景资源边界检查 47,044 次：越界 0。
- 187 项回归测试通过。
- 两份代表设备 HWT 重新打包，采样几何检查 140 次、包内条目检查 1,596 次：错误 0。
- 媒体跨时刻采样检查 376 次：错误 0。原始 HWT 哈希未变。

现有探索受 96 状态上限约束，仍有未探索分支；以上为缓存状态和离线解释器检查，未进行真机验收。已有素材内部的重叠不属于本轮组件间检查。

## 输出

- 当前预览：[打开对比](http://127.0.0.1:8766/theme-44cf4a6ae4/scenes/preview.html)
- 全部设备 XML：theme/adaptation/theme-44cf4a6ae4/scenes/scripts/
- 代表设备 HWT：theme/adaptation/theme-44cf4a6ae4/scenes/packages/
- 原结果备份：theme/adaptation/theme-44cf4a6ae4/scenes-before-vertical-fix/
- 专项证据：theme/adaptation/theme-44cf4a6ae4/clock-overlap-validation.json

![修复后平板横屏对比](../../theme/adaptation/theme-44cf4a6ae4/vertical-after.png)

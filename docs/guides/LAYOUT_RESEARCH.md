# 布局复用、两列重排与资源处理

## 参考的开源项目

| 项目与一手资料 | 提供的能力 | 本项目采用的思路 |
|---|---|---|
| [Meta Yoga](https://github.com/facebook/yoga)，[官方介绍](https://www.yogalayout.dev/docs/about-yoga)，[Gap](https://www.yogalayout.dev/docs/styling/gap)，[排列方向](https://www.yogalayout.dev/docs/styling/flex-direction) | 可嵌入的 Flexbox 布局引擎，计算盒子的尺寸/位置，支持行列间距 | 按稳定顺序排列组件；明确区分外边距、行间距、列间距 |
| [Taffy](https://github.com/DioxusLabs/taffy) | Rust 实现的 Block/Flexbox/Grid 布局；提供树节点与布局计算接口，仓库也列出 Python 绑定 | 组件树与绘制分离；固定两列、保留输入顺序 |
| [Kiwi](https://github.com/nucleic/kiwi) | Cassowary 约束求解器，带 Python 绑定 | 后续可用于更复杂的锚点/弱约束；本次采用明确的几何规则即可完成 |
| [adjustText](https://github.com/Phlya/adjustText) | 通过迭代调整文本位置减少标签与对象的重叠 | 时间/日期作为整体避让占用区域，先处理碰撞再讨论美学偏好 |
| [WebCola](https://github.com/tgdwyer/WebCola) | 带约束的图布局与自动排列 | 将不可碰撞、安全区等硬约束与对齐、平衡、稳定性等偏好分开 |

这些库解决通用盒子布局。本项目还需处理 HWT 的变量表达式、原子图层、按钮配对、源/目标逻辑坐标以及交互状态。当前把顺序与间距模型实现为 Python 的 `ordered_layout.py`，通过现有 `layout_scene` 接口接入；运行不新增 Yoga/Taffy/Kiwi 依赖，也没有复制其实现代码。

## 已实现的规则

1. 比例变化 `max(Rt/Rs, Rs/Rt)-1` 不超过可配置阈值时，使用同一交互状态在源画布上的布局，做等比缩放、安全区平移及显式锚点调整。默认阈值 18% 是工程选择。
2. 比例变化较大时，将原子组件按源画布顺序分组，逐行排两列。组件内部的相对位置不拆散；所有普通组件块使用同一个缩放值，背景和全屏手势层分别处理。
3. 计算两列的可用宽度，以及各行所需最大高度；统一缩放值同时满足两者。间距不通过拉伸图像实现。
4. 后续状态优先在已有布局上调整。相同几何复用；局部变化保持缩放并叠加源坐标差；位置冲突或新增独立组件时再整体重排。可见弹窗沿用叠加规则。
5. `adaptationPlan` 记录生成过程；`adaptationReview` 记录过小、越界、新重叠。自动分组是启发式，不能证明复杂原生主题的设计意图。
6. 横屏时间日期进行留白候选搜索。用可见元素矩形建立占用区，通过条带扫描避免细碎装饰把大留白切散，再评分左/上偏好、对齐、视觉平衡和跨状态稳定性。实现见 `aesthetic_layout.py`；没有引入 adjustText/WebCola 依赖或复制其代码。

详细配置、命令与导出范围见 [ADAPTATION_WORKFLOW.md](ADAPTATION_WORKFLOW.md)。

自动规则不理想时可用 [离线模型 JSON 与参考图匹配](LAYOUT_REFINEMENT.md) 生成修改建议，校验后写回。参考图路线使用蒙版模板匹配，适合现有素材的位置/尺度变化；完全重绘的图像或含义不同的新场景仍需人工或其他视觉模型。商用方向与验收缺口见 [SAAS_READINESS.md](SAAS_READINESS.md)。

## 资源需要拉伸或补画时，通常无需训练模型

布局输出应先决定元素的目标矩形，再选择适合的资源处理方式：

| 资源 | 通常适合的处理 | 是否需要生成模型 |
|---|---|---|
| 角色、图标、文字 | 等比缩放；文字重新排版；尽量取得更高分辨率原图或矢量源 | 通常不需要 |
| 卡片、边框、气泡、规则阴影 | 九宫格切片：保留四角，延展边缘和中心 | 不需要 |
| 平铺纹理、简单渐变/几何背景 | 重复纹理或程序生成 | 不需要 |
| 有明确主体的背景 | 等比覆盖、按主体位置裁切 | 不需要 |
| 横向新增的插画场景、需要修补的背景 | 使用现有模型扩图或蒙版修补，保留原主体 | 可以使用现成模型，无需先自行训练 |
| 大批量特定风格仍无法稳定生成 | 有数据、评测和明确目标后，再考虑微调 | 按实际效果决定 |

[Android 的 NinePatch 资料](https://developer.android.com/develop/ui/views/graphics/drawables#nine-patch) 展示了可伸缩区域与固定区域的做法；它是资源处理思路参考，不代表 HWT 自动支持 Android `.9.png`。导出到鸿蒙仍需使用该主题引擎支持的表达方式，或在构建时生成最终尺寸的普通资源。

[Diffusers 官方修补指南](https://huggingface.co/docs/diffusers/using-diffusers/inpaint) 展示了使用现有管线和蒙版修补图片的方法。新增背景应单独验证接缝、主体比例、透明边缘和风格一致性；生成好的资源再交给布局算法使用。

可以把资源策略另存为 `assetPolicy`（例如 `contain / cover / tile / nine-slice / generate`），并对生成结果记录源资源、目标尺寸、蒙版、模型版本和产物哈希。**这是后续资源处理接口的建议，本次尚未实现九宫格资源编译或自动模型调用。** 当前实现继续复用现有资源：普通组件等比缩放、背景等比覆盖；布局算法不训练模型，也不在导出过程中自动生成图像。

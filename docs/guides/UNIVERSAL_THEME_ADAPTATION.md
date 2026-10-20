# 全主题批处理与通用参考图适配

## 两个可独立运行的入口

约束布局基线：

```powershell
.\.venv\Scripts\python.exe -X utf8 multires.py tools corpus-adapt `
  --themes theme --projects theme/adaptation `
  --out artifacts/corpus-adaptation/my-run --audit-states 6
```

默认处理 `theme` 顶层的所有 HWT，生成平板横屏与竖屏候选。复用已提取的脚本、素材和交互图；新增主题、缺少目标设备或锁屏变体时自动提取。源脚本与缓存签名不一致会拒绝复用，需重新分析。普通锁屏、UCD 锁屏分别布局，合并进同一个输出包并分别回读。

扩展设备和算法：

```powershell
.\.venv\Scripts\python.exe -X utf8 multires.py tools corpus-adapt `
  --out artifacts/corpus-adaptation/fold-run `
  --targets dual-fold-mock-inner-portrait pura-x-inner-landscape `
  --algorithm anchor
```

`--algorithm my_layout.py:MyLayout` 继续使用 `layout_scene(...)` 插件协议。设备参数从 `configs/device-profiles.example.json` 获取；模拟分辨率不等同于真机画布验证。`--only "主题名"` 选择单个或多个主题，名称只用于筛选文件，不进入匹配和求解算法。

参考图批处理：

```powershell
.\.venv\Scripts\python.exe -X utf8 multires.py tools reference-workflow batch `
  my-reference-plan.json --out artifacts/my-reference-batch
```

计划格式（路径相对计划文件，亦可使用绝对路径）：

```json
{
  "schema": "corpus-reference-plan/1",
  "jobs": [
    {
      "project": "theme/adaptation/<项目>/scenes/project.json",
      "target": "tablet-mock-16-10-landscape",
      "state": "state-000",
      "referenceImage": "references/target.png",
      "provider": "external-reference"
    }
  ]
}
```

任意主题、目标方向、实际交互状态都可以成为一个任务。可选 `sourceImage` 接入真实手机截图。省略 `referenceImage` 时生成原图、生图提示词和组件标签，状态为 `awaiting-reference`；指定 `--generator my_provider:generate` 后调用可信本地插件，返回 `{"image":"绝对图片路径","provider":"服务名","metadata":{}}`，自动继续匹配和反写。桌面内置生图工具由助手调用，不会被伪装为服务端可调用的生成器。

批处理使用项目、图片和实现指纹保存检查点。输入不变会复用结果；`--retry` 创建新的尝试。各任务独立，单个失败不会终止其他主题。

## 通用规则

1. **脚本决定身份与可达状态。** 保留控件 UID、触发器、可见条件、变量和原 XML 层级。抽样选择初始场景，再覆盖新增组件、不同手势及原生条件分支，执行完整的原始交互路径，不手动修改变量制造成功场景。
2. **先解决分组粒度。** 独立子 Group 可以拆分。平面容器内，若不同按钮的实际范围完整覆盖各自图像或字形，可分成独立组件；所有可见叶子必须有对应关系。无法解释的资源、含义不明的交叉绑定或明确人工分组会阻止拆分。
3. **稳定状态与媒体首帧分开处理。** 视频和序列帧以首帧确定布局，后续帧沿用同一个变换。静态界面的入场位移使用已观察到的稳定姿态，不把部分后代的零时刻位置混进容器尺寸。原有动画和隐藏条件继续执行。
4. **先保证居中、边界和内部结构。** 近似居中的组继续居中；若某条推断的子组件对齐线与两个组的独立居中必然冲突，仅将该推断线变为强偏好并留下证据，明确写出的约束保留。
5. **公共资源尺寸需要整体可行。** 位图字库、面板与时钟跨状态共用尺寸。当组合后的时钟、弹窗和底部控件不能同时放下，尝试有界的公共缩放重算，保留最小尺寸与明确约束，记录 `automaticScaleBackoff`；不通过就停止导出。
6. **热区保留原有分区。** 全局手势层保留原始顶部空隙、边界外延伸和方向分工，避免把多个不同区域都铺成同一个全屏热区。交互坐标按同一变换映射回脚本。
7. **参考图位置是新的布局目标。** SIFT/RANSAC、透明轮廓模板、稠密尺度搜索和一对一分配产生候选；随后进行约束求解。截图的背景不充当组件证据；纯渐变等缺少可区分特征的资源不猜位置。求解后严重偏离观察框的自动匹配会撤回并重算。
8. **背景与前景分别适配。** 不透明背景使用 cover；已经分离的纯视觉前景组合执行自己的可见范围约束，避免把装饰性前景随背景一起裁掉。背景裁剪不等于界面控件丢失，隐藏/出现条件仍需一致。

## 保留原交互分支

`corpus-adapt` 默认 `--interaction-space source`：脚本里依赖原始屏幕宽高的条件继续读取原始画布，布局变换再投射到新画布，防止 `gt(#h,1920)` 等判断把原本可用的设置按钮隐藏。切换策略后，会重新回放已记录路径、重算各目标的快照，不能直接沿用另一套输入条件下的几何。

`--interaction-space target` 使用目标尺寸触发脚本自己的响应式分支；`cached` 保留缓存设置。工作台上传界面的“交互分支”也提供 source/target 两项，默认保持原始交互分支。已有版本不会被静默重写，重新分析后生效。

## 输出与验收

每套主题的产物位于：

```text
<out>/theme-<编号>/attempt-<编号>/
  manifest-0/scenes/project.json
  manifest-0/scenes/preview.html
  manifest-0/scenes/scripts/<设备>.xml
  manifest-0/scenes/audit/<设备>/native-audit.json
  packages/<设备>.hwt
```

入口是 `<out>/report.html`；`results.json` 包含完整路径，`badcases.jsonl` 保留失败、低尺寸和布局复核原因。图片表达式暂不支持时，渲染报告明确记录未渲染项，不使用假色替代，也不会让单个颜色表达式中断整个批次。

状态含义：

| 状态 | 含义 |
| --- | --- |
| `checks-passed` | 抽样脚本坐标及交互回读通过，未发现已有规则的复核项；不是视觉或真机验收 |
| `needs-review` | 有候选包，但存在缩放、重叠、渲染支持或匹配覆盖等复核项 |
| `failed` | 求解、导出、几何回读或完整交互路径失败；不能作为可发布结果 |
| `awaiting-reference` | 已准备生图请求，还没有参考图 |

原生脚本回读会重新解析 HWT，分别初始化各条原始路径，检查手势可执行性、位图字形与稳定节点坐标。抽样数量、未穷举状态、未支持效果均有记录。框 IoU 只衡量求解框与自动定位框的拟合，不能用来代替人工标注精度或像素视觉一致性。

一张已经合成多个按钮/文案的位图，不能仅靠脚本坐标把其中的内容任意重排；参考图新增、复制控件也需要额外资源/实例建模。当前流程会保留未匹配和不支持的证据，不将部分匹配计作完整适配。

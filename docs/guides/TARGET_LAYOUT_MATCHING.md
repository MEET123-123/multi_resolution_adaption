# 目标布局图匹配（无需训练）

输入：已转换主题的 `project.json`、一个目标设备/交互状态、对应的完整预期布局图。预期图可以来自 AI，也可以来自设计稿。

输出：组件组对应、约束求解后的布局建议 JSON、原素材重渲染对比、诊断。审查应用后走现有 XML / HWT 反写。此功能没有训练步骤，默认不加载任何 LoRA。

## 预览器使用

```powershell
.\.venv\Scripts\python.exe multires.py serve artifacts/learning/real-two-20261020/projects --port 8783
```

打开一个主题，选择设备、方向、交互状态，找到“目标布局图匹配 · 无需训练”：

1. 上传对应状态的 PNG / JPEG / WebP。需要完整画布，不含设备外框；宽高比与目标画布一致（容差 2.5%），10MB / 2000 万像素以内。
2. 选择匹配方式：
   - **自动视觉匹配 · CPU**（工作台默认）：SIFT/RANSAC、多尺度/宽高比搜索、透明字形负空间与颜色校验、双向边缘距离、一对一全局分配；无需训练、模型下载或人工坐标。复杂重绘和目标缺失会返回未匹配。
   - **本地 Qwen3-VL**：预训练模型读取带编号的原组件图集和目标图，按语义、外观寻找位置。适合 AI 重绘或外观略有变化的目标图。
   - **模板匹配**：带透明蒙版的多尺度图像相关匹配。适合原素材直接移动、缩放后的图片；纯色或重复图案可能无法匹配。
3. 工作台默认使用“参考图优先 · 组件组重排”：重新以脚本原始姿态构建刚性组，旧位置仅作为 weak 偏好，目标位置为 strong 偏好。同组等比、热区绑定、画布边界、等待场景仍校验。切换“保留原布局关系”可使用旧的居中/贴边保留策略。
4. 点击“匹配目标布局”。对比目标图与原素材重渲染结果，查看哪些对应被接受、哪些组保持原位。视觉模型置信度是模型自报值，未经标定。
5. “应用到当前预览”修改会话布局；“导出 HWT 与交互记录”保存新的项目和候选包。原项目不会被会话覆盖。也可下载建议 JSON，使用 CLI 持久写回原项目。

服务会从已转换的项目数据生成最新预览 UI，因此不用重新提取主题脚本。已经运行的旧服务需要重启才能加载新 Python 代码。

## 本地模型设置

当前工作区已有 `.venv-ml` 和本地 `artifacts/models/Qwen3-VL-2B-Instruct-sharded/`。业务服务通过独立进程调用该环境，模型使用 4bit 推理；不加载 mock 微调权重，不进行训练，不上传图像。

可在启动服务或 CLI 前设置：

```powershell
$env:MULTIRES_TARGET_PYTHON = 'F:\multi-res\.venv-ml\Scripts\python.exe'
$env:MULTIRES_TARGET_MODEL = 'F:\multi-res\artifacts\models\Qwen3-VL-2B-Instruct-sharded'
$env:MULTIRES_TARGET_TIMEOUT = '600'
$env:MULTIRES_TARGET_MAX_PIXELS = '200704'
```

Linux 服务器可将前两个路径替换为 GPU 环境和更大 Qwen3-VL 模型的绝对路径。模型加载复用 `learning/qwen_runner.py:load` 的推理分支。其他模型可设置可信代码入口 `MULTIRES_TARGET_LOADER=your_module:load`，接口为 `load(args, training=False) -> (model, processor)`，processor 需支持现有多图 chat template。需要不同协议时使用下面的自定义 matcher 接口。

模型默认只读本地权重并设置 `HF_HUB_OFFLINE=1`。单独 Qwen 模式缺环境、缺权重、CUDA 错误或超时会明确报错。“视觉模型 + 模板细化”模式先做语义定位，再在局部使用 NCC 细化；模型失败时显式记录 modelError，并回退模板匹配。报告区分模型结果、模板回退和人工/视觉校正结果。每次任务加载一次模型，同一服务进程内 GPU 推理串行，模型每 5 个组一页。2B 模型对小图标或复杂重绘可能匹配失败；空结果也是合法结果。

## CLI

### 匹配并生成可审查建议

```powershell
.\.venv\Scripts\python.exe multires.py tools target-layout match `
  artifacts/learning/real-two-20261020/projects/diefeng/scenes/project.json `
  --target tablet-portrait --state state-000 --image expected-tablet.png `
  --strategy qwen --out artifacts/target-layout/diefeng-proposal.json
```

不使用视觉模型时，替换为 `--strategy template`。可选参数：

- `--observations reviewed-group-boxes.json`：使用已校正的定位 JSON 跳过模型；仍执行完整求解和写回校验，并记录来源。
- `--layout-mode reference-first`：适合大幅重排；CLI 默认仍为 `preserve`，兼容原有调用。
- `--minimum-confidence 0.78`
- `--free-placement`：取消自动保留居中、贴边；组内布局仍整体等比变换。
- `--apply-scope state`：仅当前状态；默认 `matching-components`。参考图优先模式使用 compatible-layout 原子传播：全部待调整成员几何兼容才整套应用，避免时间/日期、卡片/文字只移动其中一部分；不兼容状态列入 skippedStates。
- `--apply`：生成建议后立即经安全校验持久写回；默认只产生建议和对比文件。

每次匹配在 `project.json` 同级的 `target-matching/<任务ID>/` 保存：

| 文件 | 含义 |
|---|---|
| `catalog.json` | 当前组件组与原脚本 ID、语义、约束、逻辑几何 |
| `atlas-*.png` | 带组别名的原素材图集 |
| `expected.png` | 输入的完整目标图 |
| `matches.json` | 匹配器原始结果 |
| `proposal.json` | 可直接导入现有微调流程的最终建议 |
| `before.png` / `after.png` | 匹配前 / 求解后的原素材渲染 |
| `report.html` | 三图对比、目标框偏差和未匹配原因 |
| `vlm-*` / `vlm.log` | 仅模型模式：请求、原始回复与本地运行日志 |

这些产物用于追溯，不会自动删除；批量使用时由调用方按任务 ID 管理保留周期。

### 持久写回

```powershell
.\.venv\Scripts\python.exe multires.py tools target-layout apply `
  artifacts/learning/real-two-20261020/projects/diefeng/scenes/project.json `
  --proposal artifacts/target-layout/diefeng-proposal.json
```

复用现有事务式写回：摘要过期拒绝应用，保存 `project.layout-backup-*.json`，生成 `scripts/<设备ID>.xml`、补丁/资源清单和新预览。需要 HWT 时再运行 `multires.py export ... --target ... --out ...hwt`。原生导出中无法表达的状态条件仍由现有导出器报错。

## 可插拔接口

`src/multires_app/preview/target_layout.py` 定义 `TargetMatcher`、`register_matcher`、`resolve_matches`。匹配器和几何求解分离，替换模型不需要重新提取脚本。

### 自定义 Python 匹配器

```python
def match(context):
    # context['catalog']: 组件组与别名 g0/g1/...
    # context['reference']: PIL RGB 目标图
    # context['templates']: alias -> PIL RGBA 原组件组图像
    # context['job_directory']: 本次任务目录
    return {
        'schema': 'target-layout-matches/1',
        'matches': [{'id': 'g0', 'bbox': [100, 200, 300, 100], 'confidence': 0.9}],
        'unmatched': [{'id': 'g1', 'reason': '目标图中未找到'}],
        'provider': 'my-matcher',
    }
```

`bbox` 为**目标图整个画布归一化到 0..1000 的 `[左, 上, 宽, 高]`**，包围组件组的可见画面。它不是原脚本组件的热区范围，不是 `[x1,y1,x2,y2]`，也不是图集里的卡片范围。示例别名和数字必须替换为真实目录中的值。模型不返回 XML、资源路径或脚本。

```powershell
.\.venv\Scripts\python.exe multires.py tools target-layout match project.json `
  --target tablet-portrait --image expected.png --strategy custom `
  --matcher my_package.matcher:match --out proposal.json
```

自定义入口仅由本地可信操作员通过 CLI 或 Python 注册。网页参数无法导入任意模块。

### 外部服务或人工标框

```powershell
.\.venv\Scripts\python.exe multires.py tools target-layout catalog project.json `
  --target tablet-portrait --out artifacts/target-catalog
# 将 catalog 与 atlas 交给自己的匹配服务，得到 matches.json 后：
.\.venv\Scripts\python.exe multires.py tools target-layout resolve project.json `
  --target tablet-portrait --catalog artifacts/target-catalog/catalog.json `
  --matches matches.json --out proposal.json
```

`resolve` 独立进行协议、身份、摘要、比例、安全区和跨状态校验；不再调用视觉模型。未返回、低置信度或约束不相容的组保持原位。全部失败时返回空建议与原因。

## 求解逻辑与边界

1. 从当前布局计划和显式 `layout.group` 合并组，建立组别名到原组件/脚本节点的对应。数字、冒号、日期等已有组内相对关系整体保留。
2. 只用当前可见相位及媒体首帧参考生成图集。背景、全屏手势层、当前屏外或裁切组件，以及资源不完整的组冻结。显式 `required` 自定义约束、`required` 锚点或固定尺寸引用的组在此 MVP 中整体冻结，保留其已有解，不静默放松约束。不会把交互后才出现的资源提前显现。
3. 匹配器提出可见画面框；求解器使用每组的平移、统一缩放，将内部图片、文本、关联按钮热区同步变换。原长宽比与目标框不一致时等比容纳，报告实际框。
4. Kiwi 约束求解将目标位置作为软目标，安全区和无新增重叠作为硬条件，可选保留现有锚点计划中的居中/贴边关系。已有有意重叠保留；不会把组内部自动改成另一种排列。
5. 对原本不重叠的不同组成员建立分离约束；期望框能分离时采用期望方向，否则采用原布局方向。此 MVP 不穷举所有碰撞方向。若无解或传播状态校验失败，按置信度从低到高冻结候选组再求解，逐项记录退回原因。
6. 记录期望与实际中心偏移、框 IoU；偏差大时提示检查目标图和约束。固定像素、原生图形、缺失文件和复杂特效可能无法离线重现，渲染报告明确标记；动画时间线、交互变量和资源身份沿用原脚本。

目标图可以表达原资源的重新布局。它新增的组件、完全重绘后的未知形状、组内重新排列、缺失的真实资源和未探索交互状态不能从一张图可靠反推出脚本；这类情况返回未匹配或保留原布局，需要补充该状态或人工修改组关系。


## 组件组校正与大幅重排

工作台步骤 4 提供“校正组件组位置 / 导入已有定位”。选择目标设备和**同一交互阶段**，上传参考图，点击“载入组件组”；选择组后在画布拖出目标框，或导入 `target-layout-matches/1` JSON，再点击“按校正位置求解”。结果经过同一校验、预览、脚本/HWT 生成链路。导出的定位包含 target、state、baseDigest，便于复核和后续更换模型；组别名只在对应目录中有效。

重排目录按脚本显式组件、嵌套容器、唯一交互绑定组织；不再把自动推断的整屏流式容器当成一个不可拆大组。阴影 alpha 过低的画布留白不参与主体定位；构图遮罩只用于判断主体可见部分，原遮罩和资源仍保留。空文字属于正常透明状态。背景和全屏手势保持独立。

求解器尝试不同分离方向，允许未匹配可见组让位，限制为了避让而过度缩小。参考图优先的传播结果会在导出时重新检查几何、等待状态和有限变换；旧居中、贴边、公共锁、统一资源尺寸不再覆盖已经审查的重排。显式 authored 组仍共用同一仿射，运行脚本条件和变量不由图片修改。

参考图含有设备边框时，建议先裁到有效屏幕。AI 重绘可能改变组件宽高比、增加新卡片或重复同一个时钟。当前协议一对一映射已有组，并保持组内等比；不会复制新实例或生成不存在的控件。报告中的 targetOverlapIoU、centerDisplacement 衡量定位框拟合，不等于美学评分或真机验证。

### 早期调试记录（2026-10-20，人工标框不作为自动成功）

- 比邻熊：原目录 8 个可定位组，原模板仅找到 1 组。修正目录、阴影/遮罩和弱约束后，无模型模板能找到更多组；对用户参考图做的本次 Codex 视觉定位以 `reviewed-group-boxes` 记录，10 组 / 11 组件通过求解，应用到 8 个兼容场景，XML、HWT 写回成功。
- 本次本地 Qwen 尝试在 PyTorch 分配内存时 OOM；没有把视觉校正标注算作 Qwen 自动匹配成功。本地仅 8GB 系统内存，自动模型质量需在资源充足环境继续验证。
- 参考图中重复的时钟、天气和开关没有在源包中创建副本；源包额外装饰也不自动删除。交付是已有组件的重排候选，不能当作整张 AI 图的逐像素复刻。

## 区域切页手势

`interaction/regional_gestures.py` 将较大、单轴、影响多个元素的切页/滚动热区从装饰组件中拆出，按原始屏幕区域比例适配。普通图标点击和小滑块不按此规则扩展。方向协商同时用于浏览器和后端：横向切页与纵向滚动/解锁竞争时按轴路由；由明确短距离条件限定的点击按钮可以让出滑动。`interceptTouch=true` 和 strict-z-order 仍优先。

线条小狗夏日出逃原脚本需要先双击进入组件模式（bs=1），再在右侧区域左滑切页，右滑返回；初始 bs=0 模式没有这条左滑分支。回归检查覆盖横屏、竖屏各三轮左滑/右滑，并核对原始机和目标机页变量同步；没有通过直接改页变量伪造切换。

## 原始渲染 → 生图 → 自动匹配 → 脚本反写

新增 `reference-workflow` 工具，复用项目缓存，不重新提取脚本。生成器与匹配器、求解器、原生编译器分别可替换。当前 CPU 自动匹配依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[vision]"
.\.venv\Scripts\python.exe multires.py tools reference-workflow prepare `
  artifacts/workbench/projects/<项目版本>/scenes/project.json `
  --target tablet-landscape --state state-004 --out artifacts/reference/my-theme
```

`prepare` 输出 `source-phone.png`、`groups-*.png`、`catalog.json`、`request.json`、`prompt.txt`。请求绑定项目摘要和原图摘要，提供实际目标分辨率、组件身份和生成器协议。遇到粗粒度多卡片容器，按原脚本子容器分割，在隔离目录保存新组件缓存，复制已有变换；不改变 XML 层次、可见性、动画或触发器。局部物理锚点已经存在时，来自相邻子容器的弱推断锚点不再强行合并，诊断保留被否决的推断。

工作台步骤 4 新增“生成原始图与生图请求”：下载当前所选场景的原始直板机 PNG，以及包含提示词、目标分辨率与组件目录的 `reference-generation-bundle/1` JSON。把它们交给生成器，生成的图仍在同一步上传、自动匹配和生成脚本。网页导出的是可交给任意生图服务的素材包；CLI 的 `reference-generation-request/1` 另外绑定本地项目摘要，用于自动编译闭环。

每轮项目只保留本次目标设备的数据，原多设备项目不变；原始姿态的碰撞边界跨求解尝试缓存，避免在低内存机器上反复复制、渲染无关设备。

将原图与提示词交给生成器，得到目标 PNG 后：

```powershell
.\.venv\Scripts\python.exe multires.py tools reference-workflow complete `
  artifacts/reference/my-theme/request.json --image expected-tablet.png `
  --provider my-image-generator --generation-metadata generation.json
```

`generation.json` 可保存实际提示词、模型名称和生成参数。每轮都创建独立 `runs/<ID>/`，保存目标图、自动候选/分配、求解建议、原素材前后图、XML/补丁/资源清单、HWT 和重新解析诊断。失败不覆盖原项目；`latest.json` 指向最新结果。`compiled` 表示该轮完整编译且没有覆盖/位置告警；`needs-review` 表示可以导出但存在未匹配或低拟合组；`failed` 保留具体阻断原因。框 IoU 是求解框相对自动观察框的重合度，不能作为人工标注精度、像素相似度或真机验证分数。

`reference-workflow report --root artifacts/generated-reference` 生成三图总览和汇总 JSON，同一原图/目标/状态只显示最新一轮，历史文件保留。CV 置信分是启发式评分，未经概率标定。CPU 默认搜索预算为 180 秒，每组最多约 30 秒；优先时钟、主要内容，高质量 SIFT 结果可跳过全图密集搜索。预算不足的组明确返回未定位，不补造目标框。

HWT 回读会重新解析原生 XML，重放原交互路径，比较原脚本节点对应的最终矩形；热区和自主运动装饰单独统计。报告包含稳定节点数、缺失节点、最大坐标误差与最差节点。离线解释器回读通过仍需另做真机验证。共用位置动画包装器的组件采用共同缩放；冻结组使用当前变换，不再错误地用原始坐标中的单位缩放。

当前导出器对每个 bitmap `Time` / `ImageSeries` 节点生成一个缩放后的字库族，因此参考图求解需要遵守其他场景共用的字形缩放；这是当前导出能力的约束，不会假设改 `size` 就能缩放位图数字。若受此限制无法贴合目标尺寸，报告记录 `nativeScaleConstraints` 与实际目标偏差。字形回读逐个比较同一节点的多个数字，保留资源整数像素取整带来的亚像素误差。工作台“应用匹配并生成 HWT”也会执行坐标回读；不一致时不发布新版本。

参考图优先模式区分**完整图片画布的边界约束**和**渲染主体的碰撞约束**：阴影/透明留白可以相交，主体不新增碰撞，完整组件仍在安全区内。传播同时比较组件身份、几何、资源身份和未移动障碍物的位置；不同弹窗、不同装饰布局不会只因时钟相同而继承结果。

### 生成器插件

```python
def generate(request):
    # request['sourceImage'], request['prompt'], request['resolution']
    # 调用自己的本地模型或服务器；返回已保存的图像文件。
    return {'image': '/absolute/path/expected.png',
            'provider': 'my-generator',
            'metadata': {'prompt': request['prompt'], 'model': 'my-model'}}
```

```powershell
.\.venv\Scripts\python.exe multires.py tools reference-workflow generate `
  artifacts/reference/my-theme/request.json --generator my_module:generate
```

这个入口仅允许可信本地代码。桌面内置 imagegen 在本次实验中实际生成了两张参考图，通过文件交接运行上述完整流程；项目没有把桌面工具伪装成可独立部署的生图 API。后续更换本地/服务器模型只需实现相同生成器接口。

### 复用自动定位证据迭代求解

`target-layout match` 和 `reference-workflow complete` 支持 `--replay-job <已有 target-matching/ID>`。只接受 `automatic=true` 的代码定位结果，并检查原图像素、目标/状态、布局模式与摘要；重新运行当前版本的约束与编译，不重复做 CV。人工校正不能冒充自动缓存。

## 真实前后截图批量回归

```powershell
.\.venv\Scripts\python.exe multires.py tools reference-pairs `
  --before theme/pre_view --after theme/after_view --themes theme `
  --out artifacts/reference-pairs
.\.venv\Scripts\python.exe multires.py tools reference-pairs `
  --index artifacts/reference-pairs/pairs.json --projects theme/adaptation `
  --out artifacts/reference-pairs-regression
# 修改算法后重跑失败项，历史尝试保留：
.\.venv\Scripts\python.exe multires.py tools reference-pairs `
  --index artifacts/reference-pairs/pairs.json --projects theme/adaptation `
  --out artifacts/reference-pairs-regression --retry-failed --reuse-evidence
.\.venv\Scripts\python.exe multires.py tools reference-pairs `
  --out artifacts/reference-pairs-regression --summarize
```

配对和主题检索按图像内容执行 SIFT 检索与 Hungarian 分配，不使用图片文件名标签或人工坐标映射。报告保留分数、配对间隔、主题候选与场景候选；存在歧义或源截图与缓存场景证据不足则停止该项。真实截图与原缓存手机高度不同，按截图宽高比重新执行原脚本，得到正确的底部锚点再裁取组模板；不直接拉伸缓存坐标。每个不同原始姿态的渲染只缓存一次，结果逐项落盘，失败项可恢复。

`--summarize` 汇总已保存检查点，生成简明 HTML、`summary.json` 和结构化 `badcases.jsonl`。反馈类别包括源场景未确定、目标比例不符、共同动画缩放冲突、视觉证据不足、原生坐标偏差、目标拟合不足和覆盖不足。低检索分不能证明该脚本状态不存在，因此将其留给后续主题检索、状态探索与模型定位共同判断。保留对应输入图、状态、重现请求、候选目录，可作为后续修复与算法回归的输入。

本次生图原图、实际完整提示词位于 `artifacts/generated-reference/`；20 组真实截图配对报告位于 `artifacts/reference-pairs-v2/report.html`。自动配对成功不等于自动适配成功，回归报告单独列出未探索状态、低覆盖、约束冲突和原生编译失败。

### 真实回归记录（2026-10-21）

- 内置 imagegen 实际生成两张平板横屏参考图。比邻熊接受 9/11 个可定位组，求解框相对自动观察框平均 IoU 0.975；小狗接受 7/10 组，平均 IoU 0.961。二者均为 `needs-review`，未匹配组没有被算作成功。
- 用户提供的比邻熊目标图：代码自动接受 10 组，最终工作台 r4 平均投影 IoU 约 0.983，生成后自动重放并回读坐标通过；原图中的重复卡片没有复制新实例。
- 20 对截图按内容全部配对；完整回归得到 14 份候选 HWT，均通过稳定节点坐标回读。仍有覆盖或拟合问题，保留为需复核候选。另 6 项因源场景未确定或视觉证据不足停止，未伪造位置。
- 相关 66 项单元/回归测试通过。小狗新候选 HWT 的双击入场、三轮左滑/右滑回归通过。没有进行真机验收。

报告入口：`artifacts/generated-reference/report.html`、`artifacts/reference-pairs-regression/report.html`；完整提示词在对应运行目录的 `generation.json`。数据与报告放在忽略目录，代码和接口不包含主题专用坐标。

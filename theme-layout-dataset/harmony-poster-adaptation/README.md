# 鸿蒙锁屏跨画幅布局微调

本目录复用仓库的鸿蒙锁屏解析、状态采样、求解、渲染和 XML 写回。训练和主题数据保存在本机。底座是已有的 Qwen3‑VL‑2B‑Instruct 4 bit，初始权重为上轮同家族划分训练出的数值头与 LoRA。运行目录在 `runs/v1`。

## 本轮实现

- `prepare.py` 按包 SHA‑256 绑定真实训练源，校验封存的样本、截图与证据摘要，保存逐 HWT 清单。24 包全部匹配，13 个家族共 219 条通过现有离线筛选的场景。按原家族划分保留 140 条训练、24 条验证、55 条测试。目标来自求解器，范围覆盖竖源转模拟平板横屏、平板竖屏和双折叠内屏竖屏。
- `policy.py` 根据宽高比对数差、组件占用与叠加场景，将适配分为 resize、local-reflow 和 recompose。沿用真实组件 ID 和源侧角色，每组预测锚点偏移与一个物理等比缩放。背景、混入交互控件的背景组与固定组由原求解器处理。模型用户输入只有源图、源布局、目标画布条件。
- `train.py` 执行三阶段训练。扰动监督对目标中心和等比尺度采样。几何阶段按同一输入的多候选 IoU 和可见组新碰撞、越界计算相对奖励，再加监督损失与参考模型惩罚。偏好阶段用本地高低分对做冻结参考下的 DPO 式数值偏好训练。三阶段训练目标借鉴 [PosterCopilot 论文](https://arxiv.org/abs/2512.04082)，使用连续数值头、小模型与离线渲染偏好。原项目开放权重与推理接口见 [PosterCopilot 官方代码](https://github.com/JiazheWei/PosterCopilot)。
- `render_preferences.py` 通过移动一个可调整组并重渲染原主题图层生成合成图。64 对、共 128 张。首选/拒绝由真实像素可见率、裁切率和新增碰撞、越界代理指标决定。评分衡量局部组的可见性与空间冲突。可打开 `preferences/v1/gallery.html` 逐对审查。训练不使用目标渲染图作为模型输入。
- `predict.py` 可以从具有 RecordingAnchor 元数据的现有工程提取新样本，生成按 requestKey 索引的模型提案。在发现源侧原本互不重合的组出现新碰撞或越界时，尝试按组回退，最多三次。 `apply.py` 在现有解释器与求解器上回放，剔除新增错误或渲染不完整的提案，然后从缓存工程重新生成 XML 与资源目录。
- 字符串、图层素材、可见条件、动作和动画由原引擎执行和写回。预览所见时钟与状态随源脚本计算，V2 将短值和长值注入原脚本重算场景。文本宽度可测时用预览器字体的字形 advance，位图时钟使用实测的运行时字形框。

从 HWT 输入到交互状态、重排判断和 XML 输出见 [多场景适配](SCENES.md)。

## 本地运行

先按仓库的 [CUDA 环境脚本](../../scripts/setup_qwen.ps1) 和 [ML 依赖](../../requirements-ml.txt) 准备好 Python 环境。准备本地主题包、与包摘要匹配的训练缓存以及 Qwen3‑VL‑2B‑Instruct 权重。缓存构建见 [自动构建与服务器使用](../05_自动构建与服务器使用.md)。命令从本仓库根目录开始，每次运行使用新的输出目录。

```powershell
$env:PYTHONUTF8 = '1'
$env:HF_HUB_OFFLINE = '1'
$ml = (Resolve-Path '.\.venv-ml\Scripts\python.exe').Path
$core = (Resolve-Path '.\.venv\Scripts\python.exe').Path
$themes = '<本地主题包目录>'
$theme = Join-Path $themes '<主题文件名>.hwt'
Set-Location '.\theme-layout-dataset\harmony-poster-adaptation'

& $core -B prepare.py --source $themes --out data/rebuild
& $ml -B render_preferences.py --bundle data/rebuild --out preferences/rebuild
& $ml -B train.py --bundle data/rebuild --preferences preferences/rebuild/pairs.jsonl --run runs/rebuild
```

`prepare.py` 对已有缓存做查证和筛选。新加入但还未在旧缓存中建立样本的 HWT 会出现在包清单中，须先用引擎生成源/目标场景或提供真实标注。用 `--cache` 可指定不同的缓存，用 `--model` 可指定底座模型目录。`train.py` 默认从仓库已有的数值头适配器继续训练，可用 `--warm-start` 指定兼容适配器或用 `--from-base` 从底座开始。源画布与安全区按具体工程设置。[示例配置](examples/) 中的两种目标均为模拟画布。

```powershell
& $core -B theme_workspace.py build --source $theme `
  --source-profile examples/source-profile.json --profiles examples/targets.json `
  --algorithm multires_app.learning.algorithm:RecordingAnchor --max-states 16 --max-depth 3 --out demo/new-baseline
& $ml -B predict.py capture --projects demo/new-baseline/projects.json --out demo/new-requests
& $ml -B predict.py infer --bundle demo/new-requests --adapter runs/dynamic-new/adapter --dynamic --out demo/new-proposals.json
& $core -B apply.py --projects demo/new-baseline/projects.json --predictions demo/new-proposals.json --out demo/new-delivery
```

`runs/rebuild/adapter` 是验证选中的模型，`runs/rebuild/checkpoints/preference` 保存三个阶段完成后的模型。推理示例的动态适配器由下文动态续训生成。两者都同时使用 `adapter_model.safetensors` 和 `numeric-head.pt`。LoRA 与任务的数值头配套，不能把它单独当聊天微调权重。

## 换成自己的真实标注

1. 打开 `data/review-train.jsonl` 与每条记录指向的源图及参考目标图。也可重新执行下面的 export。只把完成审阅的记录保存为 `data/my-reviewed.jsonl`，一行一条。
2. 在 `answer.groups` 编辑目标框，保持组 ID 及固定组，坐标为相对目标安全区归一化到 1000 的 `[x,y,w,h]`。记录 `reviewer`、带时区的 `reviewedAt`、`reason` 和可追溯的 `reviewEvidence`。例如由设计师调整，用目标设备的导出稿或审阅单作为证据。导入会覆盖对应样本的监督，保留弱标签和原家族划分。训练记录中剩余弱标签仍参与训练。
3. 使用真实标注版训练，传入原封存偏好数据继续试验。评估仍沿用预先固定的家族划分。真实标签与弱标签混合的验证 MAE按混合标签解释。现有渲染偏好基于原弱教师变换，已人工改框的例子需要新证据时，应单独复核对应偏好对。

```powershell
& $core -B review.py export --bundle data/rebuild --split train --out data/review-fresh.jsonl
& $core -B review.py ingest --bundle data/rebuild --review data/my-reviewed.jsonl --out data/reviewed-v1
& $ml -B train.py --bundle data/reviewed-v1 --preferences preferences/rebuild/pairs.jsonl `
  --warm-start runs/rebuild/adapter --run runs/real-label-v1
```

原 HWT、素材缓存和先前适配器均以只读方式使用。布局数据、模型输出和生成 XML 位于当前项目目录。动态行为和资源完整性以选定真机和引擎版本的实测为准。


## 复杂叠层与动态变量

`predict.py` 对 60 个可见图层起、或超过 12 个根组的场景启用分层路径。原脚本层级、可见图层顺序、视觉关系分别保留。只有同原生父容器、同类角色且几何证据足够的根组允许合并成宏组，交互面板和边缘约束优先交回原引擎。跨多个区域的大型视觉层会记录关联组并保持在原引擎规则中。宏组模型输入最多 12 组，未进入模型的根组仍保留在目标方案中。刚性组采用统一物理缩放和平移，对明确独立、有内容流证据的组件才使用顺序流式排布。带动态槽位的组合保留内部锚定和原字号比例。

每次推理生成现成的 `multires-layout/1` 提案。复杂场景另存 `*.hierarchy.json`，包含 `authoredTree`、`visualRelations`、`layerStack`、宏组锚点和操作计划。增量操作作用于所属组，绘制顺序来自原素材顺序。通过检查后的 `layer-layouts/` 包含每个已采样目标状态的完整可编辑图层框和原始节点 ID。 `apply.py` 在最后写回前比较受保护文字、时钟、日期等图层的实际 alpha 覆盖与裁切，源设计已有遮挡是对照条件。

原始照片、底板、阴影与装饰由原资源引擎执行。存在跨区的装饰路径时会列入 `multiAnchorGroups`，沿用原脚本变换。自动 Bézier 重建、背景扩图和原生文字自动换行没有纳入当前实现。若另有生图构图，可人工把主要区域整理为可选 `--composition-prior` JSON。它仅影响宏组中心的软偏好。格式为按请求键、再按面板 ID 索引的 `[x,y,w,h]`，相对目标安全区 `0..1`。

V2 从 V1 的三阶段完成权重继续训练。短值和长值分别注入时、分、秒、月、日、星期、电量，独立重跑源和目标脚本、重新求解、再渲染。变量实际值和文本仍由运行时负责。模型接收动态槽位类型、源字形范围和真实字体 advance 等可用尺寸信息。数值头训练对动态槽位尺寸不足额外施加单边损失。同一主题的所有变体继续沿用原家族划分。预览字体由现有离线渲染器提供，原生字体的实际效果须用设备确认。

```powershell
& $core -B dynamic.py --bundle data/rebuild --work data/dynamic-work-new --out data/dynamic-new `
  --themes '<训练主题前缀>' '<验证主题前缀>' '<测试主题前缀>' --states 5 --jobs 2
& $ml -B dynamic_finetune.py --bundle data/dynamic-new `
  --adapter runs/rebuild/checkpoints/preference --run runs/dynamic-new
& $ml -B hierarchy_examples.py --bundle data/dynamic-new --out data/layered-example-new
```

`data/layered-task-examples` 里有分层分组、弱目标组框和注入越界后局部平移的结构化记录，供下一阶段设计师标注。布局树构建、组内变换和遮挡检查已经接入推理。当前 LoRA 已用组框监督、渲染偏好和动态框训练；没有另外训练自由拆组、合组或装饰路径的模型任务。

## 接到现有算法

工程的接点是 `AnchorThemeLayout.propose_group_boxes(ir, desired, source, target, context)`。 `anchor.py` 的 `PosterAnchor` 继承 `LearnedAnchor`，按引擎记录的原始 `requestKey` 查找模型提案，将模型的安全区组框变成目标逻辑坐标，仍由原求解器统一执行资源等比缩放、安全区、硬约束、状态及动画。每个提案要列出完整原始组 ID。受保护、没命中或超过宏组规划预算的组从 `desired` 取引擎基线。迁移到其他算法时，保留这四层边界即可：

1. 由现有引擎先生成基线布局及请求键，抓取源渲染和当前 XML 组件证据。
2. 用独立本地进程推理并计算分层操作。带变量策略的模型执行 `predict.py infer --dynamic`。对同一请求存在多个变体的训练样本集，用 `--preset` 选定短或长，或分别生成文件。
3. 用 `apply.py` 运行检查并导出。若已有受控的运行环境，通过 `MULTIRES_LAYOUT_PREDICTIONS` 指向已验收的提案文件，再以 `<脚本绝对路径>/anchor.py:PosterAnchor` 作为 `--algorithm`。
4. 安装后的 XML 按原 Kit 机制响应变量变化。可用 `audit_dynamic_xml.py` 重读导出文件并切换短值和长值，检查原格式串/字形依赖是否仍产生新结果。

本目录收录训练、推理和交付代码及示例配置。实验产物路径以 [本机验证报告](RESULTS.md) 为准。

短长值应在构建时覆盖关键输入组合。时间每秒刷新由 XML 自己解释，无需触发模型全局重排。当前采用离线状态复算、软组框和局部回退；容器内部的原生文本排版依据原脚本声明。

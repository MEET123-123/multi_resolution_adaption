# 从主题包到多场景布局

`scene_pipeline.py run` 使用主题脚本和原素材先构建实际可达的交互图，再在每个目标画布上用 Anchor 引擎确定每个场景的布局策略。配置适配器时，只将需要新布局且源图可渲染的场景发给模型。引擎继续约束、检查并写出 XML 和素材目录。布局分类在模型推理前完成，并在最终求解结果上记录实际策略。

## 场景怎样提取

1. 分别加载普通锁屏和 UCD manifest，保留各自的节点、业务变量和交互路径。
2. 从初始化状态出发，读取可见、可命中的控件，尝试点击、双击、长按和脚本支持的滑动。动作在解释器执行后，记录实际触发器、坐标、变量变化和后继状态。返回、切换和循环保留为图上的边。
3. 以可见节点集合和会影响后续行为的控制变量合并重复场景。纯读数、时钟走秒、连续动画采样各自保留原脚本语义，同样的锚点和组件关系可共用布局。
4. 对由电量、时间或用户输入控制的场景，可用 `--environments` 分别探索。各输入组合保留独立的图和输出。
5. 读取报告的 `boundedTransitions`、`unexpandedStates` 和 `unsampledTriggers`。先提升 `--max-states` 和 `--max-depth`。若 `gestureBudgetExceeded` 为真，再提升 `--max-gestures`。已知难以自动走到的关键路径可写入 `--inputs` 的 `interaction.requiredTraces`，使用实际控件 owner、gesture、sample 和步骤。先从已发现的 `canonicalTrace` 复制可执行手势，再补关键分支。引擎会对这些手势重新命中检查。设备外部应用、未支持控件与未提供的系统输入仍可能留有覆盖缺口。

`discover` 只做源脚本状态探索。尚未确定目标屏幕，或目标布局无法通过导出检查时，也可以独立取得这些证据。

## 怎样判断重排范围

| 结果 | 证据与执行方式 |
| --- | --- |
| 复用 `reuse` | 与参考场景的已求值布局一致，或找到唯一的同角色同形状映射。沿用参考变换。仅凭形状对应的情况会标为待复核。 |
| 部分重排 `local-reflow` | 现有策略将参考场景稳定的实质组件比例至少为 55% 作为初始阈值，冻结稳定组件并只改变化的组件。若新内容挡住原有流向，求解器可同时纳入挡住它的组件。 |
| 叠加层 `overlay` | 保留底页，底页没有组件被移除，稳定组件至少 80%，新增区域足够紧凑，并且有弹层标签或遮盖比例、绘制顺序证据。执行时作为部分重排的子类。 |
| 全部重排 `full-reflow` | 初始页需要建立目标布局模板，或结构变化超出部分重排条件。在原 XML 的硬约束内重新规划整个场景。 |

判定针对目标画布。引擎还会根据稳定帧中真正进入可视区的内容、目标安全区、边缘组件与空间流向提升重排范围。报告记录 `referenceState`、`fixedComponents`、`affectedComponents`、`evidence` 与最终 `engineKind`。

## 执行

从本目录运行，Windows 示例的 Python 路径指向仓库已安装的环境。

```powershell
$env:PYTHONUTF8 = '1'
$core = '../../.venv/Scripts/python.exe'
$ml = '../../.venv-ml/Scripts/python.exe'
$theme = '<本地主题包>.hwt'

& $core -B scene_pipeline.py discover --source $theme --environments examples/environments.json --max-states 160 --max-depth 8 --max-gestures 12000 --out demo/discovery
& $core -B scene_pipeline.py run --source $theme --profiles examples/targets.json --max-states 96 --max-depth 6 --out demo/adapted
& $ml -B scene_pipeline.py run --source $theme --profiles examples/targets.json --adapter runs/dynamic-new/adapter --out demo/adapted-with-model
& $core -B scene_benchmark.py --split all --out demo/dataset-replay
```

HWT 优先读取脚本画布参数，缺失高度时尝试锁屏预览比例，再回退到示例比例。已知设备尺寸时传入 `--source-profile`。带环境矩阵的运行在各环境目录分别交付。输出的 `summary.json` 指向场景和交付清单，`scenes/variant-*.json` 将手势路径和各目标布局决策放在一起。也可用 `scene_pipeline.py report --projects ... --out ...` 阅读已存在的工程。

数据集复测会重跑原工作目录中封存的语义组件和场景采样范围。`summary.json`、`cases.json`、`issues.json` 分别记录分布、逐条结果和检查问题。219 条登记样本是原引擎产生的弱标签。训练样本以外的状态也会执行检查，以便看见语料过滤后遗漏的问题。


## 覆盖率反馈

给 `discover` 或 `run` 增加 `--coverage-rounds 3`。探索上限仍由 `--max-states`、`--max-depth`、`--max-gestures` 控制。第一轮使用较小预算，后续轮优先补录已命中、但没有登记后继状态的真实路径。候选优先考虑此前缺少完整后继的触发器、会写入资源选择变量的动作，以及较短路径。每条补录手势会在解释器中重新做命中和状态回放。使用 `run` 时，选中的路径会按 manifest 送进目标布局生成，普通锁屏和 UCD 各用各的路径。

`coverage-feedback.json` 分别记录已执行触发器、已登记后继的触发器、运行时解析到的素材路径、动态节点观测覆盖与仍未观测的节点。资源观测来自已发现状态的实际快照。对动态节点的覆盖表示至少发现一个实际资源，其他条件分支仍可继续补测。静态审计的范围是包内引用。对于 `srcid`，检查脚本实际选择的编号资源族；对于 `srcExp`，优先审计最终运行时引用。这两项指标需要一起查看。

```powershell
& $core -B scene_pipeline.py run --source $theme --profiles examples/targets.json --coverage-rounds 3 --max-states 96 --max-depth 6 --max-gestures 4096 --out demo/adapted-with-coverage
```

反馈能自动扩展路径和减少审计误报。若仍有未采样触发器、缺失文件、外部资源或不支持的原生事件，报告保留具体条目供继续修复。

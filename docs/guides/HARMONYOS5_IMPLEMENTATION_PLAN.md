# HarmonyOS 5+：逐文件改造清单与实施文档

日期：2026-10-19。对照源码：`9c7cfa2`。

配套阅读：[规范与迁移方案](HARMONYOS5_MIGRATION.md)、[官方来源记录](../reference/HARMONYOS5_SPEC_SOURCES.md)。

**文档状态：待实现设计。**以下新增模块、接口、配置和验收用例均为建议；这次提交没有实现目标引擎转换器，也没有增加或执行运行时测试。

## 一、可以继续复用的部分

| 已有能力 | 保留方式 |
|---|---|
| `interaction/animation_semantics.py` | 保留人工动画与自动播放的依赖分析，由目标适配器提供准确的命令和属性语义 |
| `interaction/interaction_history.py` | 保留连续操作、撤销、分支和样式选择；补足生命周期与形态切换事件 |
| `pipeline/scene_clusters.py` | 保留复用、局部重排、弹窗与整体重排；同一原生状态条件仍只能使用一致的布局变换 |
| `layout/anchor_ir.py`、`anchor_solver.py` | 保留九宫格、分组、容器、硬约束和软偏好 |
| `layout/spatial_flow.py`、`spatial_alignment.py` | 保留原始非重叠关系、上下间距与对齐关系 |
| `layout/aesthetic_optimizer.py` | 在目标安全区、语义和原生支持检查通过后优化布局 |
| `media/media_layout.py` | 保留参考帧与人工状态分离、等待组件保护和首帧变换复用 |
| `export/layout_writeback.py`、`interaction_preservation.py` | 保留原文补丁和行为保持审计；跨引擎转换另加专门审计 |

设备和系统版本的判断不放进用户的布局算法。自定义算法继续实现 `layout_scene(components, source_profile, target_profile, context)`。

## 二、P0：先建立版本、模板和兼容性基础

### M01：源规范与目标规范分离

**现有位置**

- `src/multires_app/cli/multires.py::infer_engine()`
- `src/multires_app/pipeline/scene_pipeline.py::create_project()`、`create_model_project()`
- `src/multires_app/interaction/interaction_ir.py::compile_interactions()`

**当前差距**

`infer_engine()` 将 `base/` 目录等线索用于弱推断；`--engine` 作用于分析。场景管线没有贯通的目标引擎选择、证据与冲突处理。IR 中的 `sourceEngine` 主要是来源字符串。

**修改建议**

1. 新增 `engine/engine_profiles.py`，定义源规范、目标规范、根场景、工具版本和识别证据。
2. 自动识别给出候选和置信依据；无证据时使用 `unknown`，不能将目录名作为确定结论。
3. 显式选择与包内证据不一致时报告冲突。
4. CLI、任务保存、缓存键、预览会话及导出均携带同一配置。系统规范变化后旧缓存需要失效。

**验收条件**：目录含 `base` 的旧样例不会被直接确认为 5+；源未知时不会生成“目标兼容已通过”的结论；旧命令的默认原样适配行为可继续使用。

### M02：从标签提示升级为字段级规则

**现有位置**：`engine/engine_capabilities.py::capability_report()`、`pipeline/scene_analysis.py::attach_analysis()`。

**当前差距**：固定目标枚举和标签集合只能提供粗粒度提示。相同标签的新增属性、事件、根场景和硬件条件没有独立规则。

**修改建议**

- 新增可维护的能力目录，记录 `tag/attribute/event/reference`、起始规范版本、场景和设备范围、来源及核对日期。
- 分别输出语法可识别、参考执行可模拟、目标原生可用、真实设备已验收四个维度。
- 同时扫描属性、触发事件和系统引用；不能只数 XML 标签。
- 对关键未知项停止自动语义转换，并生成具体待处理记录。
- 首批规则覆盖 S03、S04、S06、S10 中已确认的差异；6.1 等增量在资料和样例补齐后登记。

**验收条件**：5.0 目标不会接收 6.0 才出现的属性；6.0 上缺少硬件能力仍报告不可用或待核实；`CORE` 中的标签也接受属性语义检查。

### M03：模块发现与官方模板样例

**现有位置**

- `engine/scene_engine.py::load_model()`、`extract_assets()`、`resolve_asset()`
- `engine/archive_safety.py`
- `tools/batch_convert.py::convert_source()`

**当前差距**：主要选择唯一的 `lockscreen/manifest.xml`，UCD 分支单独处理；资源解析基于单一脚本上下文。尚无已核实的 5+ 官方工程路由模型。

**修改建议**

1. 新增 `engine/package_resolver.py`：发现模块与候选入口，以官方模板和根标签确认用途；保留明确的入口选择机制。
2. 一个工程可包含多个屏幕表面、方向、语言及锁屏分支。它们不是同一锁屏中的交互页面。
3. 每个节点保存原文件、原路径、目标模块及规范化 ID 的对应。
4. 每个模块单独解析资源上下文和跨模块引用，继续执行现有归档路径及体积检查。
5. 取得可使用的 5.0、6.0、6.1 官方最小工程；可公开的精简样例放入仓库，真实素材留在本地。未核实的目录和文件名不填默认值。

**验收条件**：多入口包不会任意取第一个 XML；不支持的模块完整列入清单；脚本与资源不会因使用相同文件名而串到另一屏幕。

## 三、P1：锁屏参考执行与行为迁移

### M04：将目标语义从旧版参考解释器中分离

**现有位置**：`engine/scene_engine.py::Runtime`、`Evaluator`、`VISUAL`，以及 `preview/paint_visibility.py`、`preview/scene_viewer.js`。

**修改建议**

- 新增 `engine/adapters/`，先实现旧格式适配器与 5.0 锁屏适配器，向公共布局模型输出相同结构。
- 单独登记标签、默认值、几何、文字度量、动画时间轴与可见性策略。
- **已确认的具体差距**：S03 定义按钮 `visibility <= 0` 不可见、`> 0` 可见；当前 `logical_snapshot()` 与 `snapshot()` 使用 Python 真值判断，会将负数当真。应通过版本适配器统一逻辑可见、渲染可见及命中判断；条件表达式的非零语义不要一并改成正数语义。
- `VISUAL` 和快照遍历目前不能完整覆盖规范目录中列出的 `ImageNumber`、`CountDownTime`、`SourceImage`、`Swiper` 等。逐项增加支持或明确不支持，避免整个子树被静默跳过。
- `SourceAnimation` 等规范名称需要按对应语法验证，建立显式转换；不能全局替换与旧标签相似的名称。

**验收条件**：目标版本的显示、命中和状态探索结果一致；不支持的可见节点不会被误当成“场景为空”。

### M05：类型化系统输入和语言资源

**现有位置**：`Runtime.__init__()`、`lookup()`、`Evaluator`、`interaction/interaction_ir.py`、`engine/scene_engine.py::resolve_asset()`。

**当前差距**：系统值主要是固定 mock；缺失引用回退为 `0`；`REF` 对 `#` 与 `@` 抽取相同的名字。普通变量赋值不能完整表达系统命令、数据提供器和持久化行为。

**修改建议**

- 新增 `engine/system_data.py`：保存类型、只读／控制角色、有效性、更新时间和数据来源。
- 保留数字引用与字符串引用的种类，按引擎表达式规范决定转换规则；不同类型不能依赖 Python 隐式比较偶然得到正确结果。
- S10 中的状态值和控制变量建立不同通道：预览记录模拟副作用；原生输出保留目标控制协议。
- 增加天气、媒体、健康、无数据、语言和深浅模式的场景输入配置；模拟值不写成原生固定常量。
- S05 的语言 JSON 参与资源依赖和路径解析。缓存键包含语言和资源内容摘要。

**验收条件**：缺失天气与晴天不被合并；字符串状态不会变为数字零；切换语言后资源正确；自动旋转开关不会被误用为屏幕方向。

### M06：连续交互、生命周期及命令容器

**现有位置**：`interaction/interaction_semantics.py`、`interaction_ir.py::InteractionMachine`、`interaction_history.py`、`engine/scene_engine.py::execute()`。

**当前差距**：已经支持连续导航、历史和部分延时队列；参考 `Runtime` 的延时、命令容器及亮熄屏生命周期仍有能力限制。两套执行路径需要一致的目标语义。

**修改建议**

1. 复用已有事务与队列，定义初始化、亮屏、熄屏、页面切换及设备形态切换的事件。
2. 按目标规范处理点击、双击、长按及新增事件；保留捕获目标、触摸起点、方向和阈值。
3. 核对 `const`、`persist`、`globalPersist` 和状态作用域；明确哪些值跨亮熄屏、解锁或模块保存。
4. `GroupCommands` / `CycleCommand` 保留有序树结构，按规范处理延时、条件与取消。现有不支持标记保留到相关执行器完成，不先拍平成若干即时赋值。
5. 对自动循环设置探索预算，不用离散动画时刻无限扩展布局；关键操作用既有 `requiredTraces` 保护。

**验收条件**：“打开设置 → 样式页 → 修改 → 返回 → 关闭 → 重开”保留选择；熄屏中断动画和延时后不会提前显示面板；未知原生副作用明确出现在结果中。

### M07：原生应用动作与独立迁移审计

**现有位置**：`engine/engine_capabilities.py`、`interaction/interaction_ir.py`、`export/interaction_preservation.py`、`export/layout_writeback.py`。

**修改建议**

- 新增 `engine/native_actions.py`，以语义动作、原命令与条件、目标映射和官方来源组织转换。
- 仍按主题 XML 的 `package` / `class` 协议生成命令；未知第三方应用、未安装应用和解锁限制列入验收。
- 新增 `export/semantic_migration.py`：只允许有规则的特定变更，记录原节点、目标节点、参数与理由。
- 两段审计链：**源脚本 → 已审计的语义迁移脚本 → 几何反写脚本**。第一段允许明确的引擎转换；第二段继续使用现有严格的行为保持审计。

**验收条件**：相机、天气实际打开正确目标；不靠修改几何审计白名单来放行任意命令改写；主题内部页面跳转与系统应用跳转可区分。

## 四、P2：设备形态、安全区与布局上下文

### M08：拆分物理尺寸、设计画布和原生入口

**现有位置**：`configs/device-profiles.example.json`、`pipeline/scene_pipeline.py::target_profiles()`、`engine/coordinate_space.py`、`preview/interaction_server.py`。

**修改建议**

- 保留 `resolutionPx` 的既有含义，新增设计画布和原生屏幕角色字段，分别记录来源。
- Mock 的两倍宽、方向交换规则作为研究配置；正式目标要求经过核实的形态表。
- Pura X 外屏新增静态产物路径；Pura X Max 建立三态布局与路由。额外研究预览与必做原生形态分开标记。
- 形态变化时保留用户选择，重新提供系统输入与视口；不要通过冻结全部 `screen_width/height` 等输入掩盖原生分支变化。
- 未确认的原生入口为空并报告缺项，不能拿现有 `scripts/<profile>.xml` 文件名充当引擎资源选择机制。

**验收条件**：实际工程中选中正确屏幕资源；横竖屏和折叠切换保留样式；物理像素、逻辑像素及发布素材不会被重复缩放。

### M09：求解交互禁入区

**现有位置**：`layout/adaptive_layout.py::safe_region()`、`anchor_ir.py`、`anchor_solver.py`、`anchor_validation.py`、`preview/interaction_server.py`、`interaction/interaction_semantics.py::hit_test()`。

**修改建议**

1. 保留四边缩进，新增带坐标单位、来源和生效条件的矩形／多边形占用区。
2. 图像与文字的视觉区域、按钮热区和手势轨迹分别建模；必要时使用通道不同的约束。
3. 复用现有受限方向搜索，表达“在禁入区左／右／上／下”的候选，交由硬约束验证；不能将不相交关系误写成所有方向同时成立。
4. 非凸多区域先拆为可检查的区域，不用一个包围矩形永久排除全部中间空间。
5. 背景与原作有意裁剪、触摸反馈、入场和稳定内容保持已有角色区别；系统功能的交互优先级单独处理。
6. 对空间不足给出约束冲突，按既有缩放、分组与局部重排策略迭代；安全约束失败时不以更高美学分数覆盖。

**验收条件**：视觉合规不代表热区合规；点击区边缘和手势覆盖均受检查；居中、底角锚点与上下间距在目标可用区域内保留。

### M10：媒体与复用范围

**现有位置**：`media/media_layout.py`、`media/resource_scaling.py`、`pipeline/scene_clusters.py`、`preview/scene_viewer.js`。

**修改建议**

- 目标适配器提供参考姿态、原生尺寸、裁剪与遮罩范围；同一场景保留一致变换。
- 只有播放相位变化时直接复用；人选择样式、系统数据改变布局或形态改变时按依赖重新分类。
- 循环装饰的首帧裁剪、稳定资源边界、待出现资源与触摸反馈沿用既有规则并新增目标引擎样例。
- 媒体编码、文件约束和浏览器可解码性分别报告，不能把浏览器支持当成原生支持。

**验收条件**：逐帧播放不引入新的布局变换；切换场景不会重置人工姿态；资源与组件同步缩放；原生和预览的裁剪方式可对照。

## 五、P3：官方工程交付与验收

### M11：原包候选导出与官方工程输出分离

**现有位置**：`pipeline/scene_pipeline.py::export_script()`、`export_package()`，`tools/batch_convert.py`，`export/layout_writeback.py`。

**当前差距**：`export_package()` 将目标脚本替换到原 ZIP/HWT 的原路径。它没有实现 5+ 官方工程生成、设备形态原生路由及工具出包。

**修改建议**

- 保留现有候选导出方式，新增基于已核实模板的官方工程交付器。
- 交付器接收模块、脚本、资源映射和屏幕路由清单；未知入口、必需素材或不支持能力明确报错。
- 官方工具接口未知时先交付可导入工程和人工操作步骤；得到可验证接口后再自动化，不发明命令行参数或私有签名流程。
- 工具导出后记录工具版本、包摘要与设备验收结果；最终文件变动使相应证据失效。
- “完整主题发布”检查还需覆盖 AOD、桌面、图标、其他必做模块和预览材料。锁屏阶段完成只更新锁屏状态。

**验收条件**：官方工具可导入、可校验、可出包；目标设备实际加载正确资源。工程补丁输出和正式发布产物的状态不可混用。

### M12：将证据接入报告、界面和服务

**现有位置**：`pipeline/scene_analysis.py`、`tools/release_readiness.py`、`preview/scene_viewer.html`、`service/saas_jobs.py`、`service/saas_api.py`。

**修改建议**

- 在报告和预览中显示目标规范、设备表面、模拟输入、未知能力与原生验收状态。
- 缓存／任务键包含引擎规则摘要、模板、设备安全区、语言和系统输入配置；切换规则不复用旧结论。
- 服务端保存经验证的目标配置；客户端不能靠修改结果 JSON 中的状态字段宣称兼容。
- 发布就绪判断增加工具产物和版本／设备验收证据，并保留已有隔离、资源限制与回滚机制。

**验收条件**：离线测试通过、可预览、原生可执行和可发布是不同状态；未测试的版本或设备不会继承其他目标的认证结果。

## 六、接口与数据结构草案

以下是设计示意，**当前配置校验器和 CLI 尚不支持这些新增字段**。

```json
{
  "sourceEngine": {"family": "huawei-theme-xml", "specVersion": "unknown"},
  "targetEngine": {
    "family": "huawei-theme-xml",
    "specVersion": "5.0",
    "rootScene": "Lockscreen"
  },
  "surface": {
    "id": "pura-x-max-inner-landscape",
    "displayRole": "inner",
    "posture": "unfolded",
    "orientation": "landscape",
    "physicalResolutionPx": {"width": 2584, "height": 1828},
    "designCanvasPx": {"width": 2600, "height": 1840},
    "nativeEntry": null,
    "routingVerified": false
  },
  "systemInputs": {"provider": "mock", "unknownPolicy": "report"},
  "safeAreas": {"coordinateSpace": "design-canvas", "verified": false, "regions": []},
  "delivery": {"mode": "official-project", "templateId": null}
}
```

物理尺寸只示意既有配置与设计尺寸应分开保存，正式使用前需再次核实设备来源。空安全区和未核实入口不能自动通过正式目标检查。

建议适配器协议如下，名称为拟议接口：

```python
class ThemeEngineAdapter:
    def discover_modules(self, package, verified_template): ...
    def parse_module(self, module, engine_profile): ...
    def assess_capabilities(self, model, target_profile): ...
    def create_runtime(self, model, system_provider): ...
    def emit_project(self, model, layout_result, delivery_plan): ...
```

给现有 `layout_scene(...)` 的 `context` 增加可选的 `engineProfile`、`displaySurface`、`safeAreas`、`systemSnapshot`、`referencePose`。旧算法缺少这些扩展时保持原契约；关键原生语义和最终安全检查仍由公共管线执行。

## 七、验收用例清单

这些是后续开发需要新增或扩展的测试，未在本次文档提交中执行。

| 层次 | 必须验证的用例 |
|---|---|
| 识别 | 未知源规范、目录误判、多模块、多入口、显式配置冲突 |
| 能力 | 5.0 与 6.0 属性差异、7.0 事件误用、根场景不支持、硬件能力缺失 |
| 表达式 | 数字／字符串、语言 JSON、负数 visibility、条件的非零语义、未知系统值 |
| 状态机 | 连续导航、样式选择、返回重开、亮熄屏、延时取消、有限循环与预算 |
| 原生动作 | 相机、天气、第三方应用未安装、解锁状态、命令顺序和条件 |
| 布局 | 居中、四边锚点、上下间距、原始不重叠、热区与图像绑定 |
| 安全区 | 内部禁入区、动态系统区域、热区扩展、完整滑动路径、无可行解 |
| 媒体 | 首帧隐藏、循环装饰、帧尺寸变化、视频、遮罩、系统数据触发的新布局 |
| 设备 | 平板双方向、已核实折叠形态、Pura X 外屏静态分支、Pura X Max 三态 |
| 导出 | 未授权命令变动被拒绝、两段审计、资源摘要、模块路由、官方工具导入 |
| 真机 | 对应 ROM 安装、锁屏／解锁、冷启动、亮熄屏、旋转／折叠、语言、重载后持久化及实际性能 |

实际交付记录至少包含：主题摘要、适配器与规则版本、目标 ROM、设备型号／形态、官方工具版本、最终包摘要、操作路径、期望与实际结果、截图或视频及未覆盖项。

## 八、阶段完成条件与建议顺序

| 阶段 | 完成条件 | 依赖 |
|---|---|---|
| P0 | 有可用官方最小工程、字段级兼容性报告和明确模块清单 | 来源与模板核实 |
| P1 | 5.0 锁屏最小样例的参考执行与原生行为可对照，语义迁移可审计 | P0 |
| P2 | 所声明的设备形态都有安全区、正确路由和完整交互布局证据 | P1 |
| P3 | 官方工具最终出包与目标 ROM 验收通过 | P2 |
| 增量版本 | 在已通过的基线上加入 6.0、6.1 等已核实差异并分别验收 | 对应资料和设备 |

建议先选一个官方 5.0 最小主题完成全链，再使用现有真实主题覆盖时间、菜单、多步样式、媒体、天气和系统动作。当前批量结果可作为设计回归样本，但不能替代各目标原生版本的验收。

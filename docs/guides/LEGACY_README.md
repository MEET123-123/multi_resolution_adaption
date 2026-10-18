# 旧版项目总览（历史记录）

此文保留完整历史说明，目录和启动命令以 [当前项目总览](../../README.md) 和 [快速使用](../QUICKSTART.md) 为准。

# 鸿蒙锁屏主题多分辨率适配

半岛日记改补充修复顶部隐藏面板提前露出、动画角度表达式及后出现按钮漏绑定的问题；22 个方向的实际打开、关闭验证通过。见 [面板交互修复报告](../reports/BANDAO_DRAWER_RESULTS.md)。

半岛日记改已修复动画时间图片被误判为装饰导致的纵向重叠；22 个设备方向重新生成，专项关系检查违规为 0。见 [半岛日记修复报告](../reports/BANDAO_LAYOUT_RESULTS.md)。

波蓝主题已修复多层样式及入场动画下的左右角落定位，并将弹窗、时间与快捷按钮纳入同一资源缩放尺度，保留图片、文字和点击区域的对应关系。结果见 [波蓝适配修复报告](../reports/BOLAN_LAYOUT_RESULTS.md)。

底部独立快捷按钮现按左下角、右下角分别定位，图片与点击区域同步，避免整组居中后向中间收拢；上方时间保持独立的横向位置。结果见 [CORNER_ANCHORS_RESULTS.md](../reports/CORNER_ANCHORS_RESULTS.md)。

原始可见内容中共用的边线与中心线现会跨组件组统一求解，自动行列保留原有对齐方式，公共组件锁定、美学调整及导出都会复核对齐。实现规则见 [LAYOUT_RELATIONS.md](LAYOUT_RELATIONS.md)，最新验证见 [ALIGNMENT_RESULTS.md](../reports/ALIGNMENT_RESULTS.md)。

居中、贴边和上下间距现按可见内容与原始关系推断：每个轴优先保留居中，再判断边缘；原本分离的组件保留先后次序和适配间距，提示箭头跟随对应按钮，背景独立铺满并保留分层构图。规则见 [LAYOUT_RELATIONS.md](LAYOUT_RELATIONS.md)，上一轮居中与间距验证见 [LAYOUT_RELATIONS_RESULTS.md](../reports/LAYOUT_RELATIONS_RESULTS.md)。

序列帧与视频已接入“首帧画布求解、后续帧沿用变换”：保留播放和原动画，支持透明/零尺寸入场、暂停及 Source 序列。实时预览可点击“采样媒体动画”对照两侧时间轴，不增加交互场景。规则与边界见 [首帧布局说明](ANCHOR_LAYOUT.md#序列帧与视频首帧布局)，真实包跨时刻检查见 `theme/adaptation/media-validation.json`。

最新场景默认算法为 `anchor`：九宫格父/自身锚点、嵌套分组容器、Kiwi 软硬约束、公共组件冻结与有限避让搜索。预览支持锚点/组轮廓和规则修改，XML 反写提供精确补丁审计。项目分析、规则、配置、边界及真实包验证入口见 [ANCHOR_LAYOUT.md](ANCHOR_LAYOUT.md)。下文 `adaptive`/两列说明保留为旧算法背景；显式 `--algorithm adaptive` 仍可使用。

产品方向已确定为面向客户的在线适配服务。新增客户工作台、密钥与租户隔离、异步转换、受限预览、不可变版本及回退。当前是受控内测基础，启动方法、API、配额与商用验收项见 [SAAS_READINESS.md](SAAS_READINESS.md)。

横屏时间/日期支持留白候选搜索与对齐、平衡、稳定性评分。布局可离线导出给大模型、导回 JSON 审查写回，或由预期渲染图生成 bbox 建议。说明见 [LAYOUT_REFINEMENT.md](LAYOUT_REFINEMENT.md)。

交互存档已与场景采样分离：完整运行状态、有序命令变化、周期快照和分支游标，用于连续操作、页面返回与调试撤销。设计、代码入口、示例和边界见 [INTERACTION_STATE_MODEL.md](INTERACTION_STATE_MODEL.md)。

两款原生平板包现提供交互增强 v2：轻触反馈、滑动换装、长按简洁模式、动画节奏、电量联动及设置恢复。构建默认另存到 `theme/tablet-2560x1600/v2/`。说明见[交互与验证说明](../../theme/tablet-2560x1600/v2/交互与验证说明.md)；独立包验证入口为 `validate_tablet_theme.py`，手势编译与重放逻辑为 `tablet_interactions.py`。

旧版场景算法 `adaptive` 在宽高比变化较小时保留原构图，变化较大时按组件分组重排；它作为新算法的比较与回退候选保留。两款主题的人工平板构图已抽为可编辑 JSON 模板，统一运行入口为 `theme_adapter.py`。旧策略、模板和配置见 [ADAPTATION_WORKFLOW.md](ADAPTATION_WORKFLOW.md)。

`adaptive` 的两列策略按原视觉顺序排列，所有普通组件共用等比缩放，行列间距可配置。后续交互状态优先沿用已有位置。实现为 `ordered_layout.py`；新默认 `anchor` 会比较候选几何质量，自动推断劣于旧候选时记录回退。开源方案与资源说明见 [LAYOUT_RESEARCH.md](LAYOUT_RESEARCH.md)。

新增 `scenes` / `export` 流程：真实素材场景、交互状态图、组件重排、状态条件 XML 回写与 HWT 导出。用法、算法接口和当前边界见 [SCENE_WORKFLOW.md](SCENE_WORKFLOW.md)。真实主题的新预览位于 `theme/adaptation/<theme>/scenes/preview.html`。

这是一个聚焦锁屏脚本的适配工具与在线服务内测基础：从 XML 中提取控件、坐标、资源引用、变量、触发器和命令，按交互关系归纳场景，并为每个元素给出待人工确认的布局分类。它不把推断当成最终设计决定。

## 当前处理流程

1. **读取脚本**：支持主题目录、单个 XML、ZIP/HWT 压缩包；压缩包只读，不会解包覆盖原件。
2. **提取交互**：归集 `Button` / `Trigger` / `Var` 和其命令，形成触发场景；若没有显式交互则列为被动显示场景。
3. **场景与布局分层**：输出变量、控件、几何字段、资源引用、交互关系；初步分类为 `layout-required`、`shared-component-candidate`、`direct-layout`、`manual-review`，并带原因、置信度和复核标记。
4. **布局算法适配**：把控件、场景、源画布和目标 profile 交给可插拔算法，产生按设备态区分的几何结果。
5. **明确回写**：把审核后的 XML 属性 patch 写入新脚本文件，原始主题包不变。

## 使用

```powershell
python .\multires.py analyze .\theme.hwt --engine auto --out .\out\analysis.json
python .\multires.py analyze .\theme-folder --engine harmonyos-5+ --out .\out\analysis.json
python .\multires.py layout .\out\analysis.json --source-profile .\source-profile.json --profiles .\configs/device-profiles.example.json --algorithm demo --out .\out\layout-plan.json
python .\multires.py preview .\out\layout-plan.json --out .\out\preview.html
python .\multires.py apply .\unlock\lockscreen\manifest.xml --patches .\out\layout-plan.json --target pura-x-inner-landscape --out .\out\manifest.xml
```

`auto` 只根据目录名和 XML 内容作弱推断。建议在拿到主题包后显式指定规范版本，避免 HarmonyOS 4.x 及以下与 HarmonyOS 5+ 的引擎差异被误认为统一语法。

## Patch 格式

选择器应能唯一定位一个节点。可以使用主题脚本中唯一的 `id` / `name`，或分析结果中的 `path`。`set` 中的值原样写回 XML，支持引擎表达式字符串。

```json
{
  "target": "tablet-inner",
  "patches": [
    {
      "selector": { "id": "clock", "tag": "Time" },
      "set": { "x": "#screen_width/2", "y": "#screen_height*0.22" }
    },
    {
      "selector": { "path": "/Lockscreen[1]/Button[1]" },
      "set": { "w": "#screen_width", "h": "#screen_height" }
    }
  ]
}
```

旧 `apply` 命令支持对**单个 XML 文件**的显式属性替换。真实主题使用 `scenes/export` 流程，已支持状态条件反写、精确补丁审计和整个 HWT 重打包；原件不覆盖。

`configs/device-profiles.example.json` 收录了 Pura X、Pura X Max、Mate XT 和 Pura X View profile；另有平板与双折叠内外屏 mock profile。布局命令为每个 profile 生成 portrait 和 landscape 两个方向，共 22 个目标布局。双折叠展开态 mock 画布宽度等于源直板画布宽度的两倍、高度不变；平板/双折叠示意尺寸标记为 `mockResolution`，应替换成目标机型尺寸。官网公布的像素规格也不代表主题引擎坐标单位、有效可视区、安全区或锁屏脚本画布尺寸，这些仍需实机/主题引擎样本确认。

## 布局算法接口

实现一个 Python 类，提供 `layout(elements, source_profile, target_profile, context)` 方法，并返回 `{element_key: {"x": value, "y": value, "w": value, "h": value}}`。`context` 含完整分析数据和场景列表。算法可返回 XML 支持的数值或表达式；预览器只会绘制可解析为数值的坐标。

```python
class MyLayout:
    name = "my-layout-v1"

    def layout(self, elements, source_profile, target_profile, context):
        # Return geometry attributes keyed by element["key"].
        return {element["key"]: {"x": 0, "y": 100, "w": 80, "h": 80}
                for element in elements if element["tag"] == "Image"}
```

旧 `layout` 命令通过 `--algorithm path/to/my_layout.py:MyLayout` 加载本地算法；内置 `demo` 只做 X/Y 独立比例缩放，其预览用占位图验证数据通路。真实主题请使用 `scenes` 的 `layout_scene()` 插件接口和双画布预览，支持主题素材、部分视频与完整手势事务；它仍是部分引擎语义的离线解释器，不能代替原生实机渲染。

## 模拟数据端到端演练

```powershell
python .\multires.py analyze .\samples\mock-theme --engine harmonyos-5+ --out .\samples\mock-output\analysis.json
python .\multires.py layout .\samples\mock-output\analysis.json --source-profile .\samples\mock-source-profile.json --profiles .\configs/device-profiles.example.json --algorithm demo --out .\samples\mock-output\layout-plan.json
python .\multires.py preview .\samples\mock-output\layout-plan.json --out .\samples\mock-output\preview.html
python .\multires.py apply .\samples\mock-theme\unlock\lockscreen\manifest.xml --patches .\samples\mock-output\layout-plan.json --target pura-x-inner --out .\samples\mock-output\pura-x-inner.xml
```

模拟包包括时钟、日期、装饰图、状态变量、上滑解锁、相机和电话动作。页面中的点击只演示交互关联，不会真正执行解锁或启动系统应用。目标布局 ID 由 profile ID 与方向组成，例如 `dual-fold-mock-inner-portrait`、`dual-fold-mock-inner-landscape`。

## 真实主题的交互、聚类和批量验证

完整操作模拟、四类布局聚类、算法插件接口与反写说明见 [SCENE_WORKFLOW.md](SCENE_WORKFLOW.md)。

```powershell
python multires.py serve theme/adaptation/wufu/scenes/project.json --port 8765
python .\multires.py tools batch-convert
```

本地实时预览支持连续点击、拖动、长按、双击和会话导出；直接打开 HTML 为离线回放。批量产物入口为 `theme/adaptation/index.html`，每包包含 22 份设备/方向脚本、两个代表性 HWT 和明确标注检查范围的报告。`--only 名称片段` 可以选择个别包迭代；`--resume` 复用源 SHA256 相同的已生成场景，重新反写及检查。修改交互分析逻辑后应重新生成场景。

## 输出重点

贴边组件和底部均分关系的推断、组件拆分及验证说明见 [SCREEN_RELATIONS_RESULTS.md](../reports/SCREEN_RELATIONS_RESULTS.md)。配置 `anchorLayout.inferScreenRelations=false` 可关闭关系自动推断；显式锚点和人工分组保留优先级。

默认使用平衡视觉占比的等比缩放，并对稳定场景的资源与按钮热区施加画布边界约束；入场、离场动画保留。配置及插件扩展见 [ANCHOR_LAYOUT.md](ANCHOR_LAYOUT.md)，最新实包验证见 [CANVAS_BOUNDS_RESULTS.md](../reports/CANVAS_BOUNDS_RESULTS.md)。

新增十维美学代理评价与受约束迭代：评价、候选搜索、Kiwi 投影、复评及反写。预览可对比优化前后，并查看逐项评分。研究依据、运行方式和适用边界见 [AESTHETICS.md](AESTHETICS.md)。

- `files[].scenes`：由触发事件归纳的交互场景及对应命令。
- `files[].variables`、`variableUses`：状态变量及其使用控件。
- `files[].elements`：布局元素、父容器、位置/尺寸、图片引用和变量引用，以及待复核的布局建议。
- `repeatedStructures`：标签和属性结构重复的节点，供公共组件候选判断。
- `parseWarnings`：未能解析的 XML。

## 规范依据与适用边界

- 华为 HarmonyOS 5+ 主题引擎规范将主题能力按锁屏、桌面、一镜到底等分类；当前系统数据开放规范明确列出直板机、折叠屏、平板支持范围。参见[系统数据开放规范](https://developer.huawei.com/consumer/cn/doc/content/themes-engine-next-base-sysdata-0000002471395102)。
- 华为 HarmonyOS 4.x 及以下主题引擎有独立规范；例如新版引擎增加了 `Time.space`、`Var.const` 等属性，旧主题包迁移需保留版本差异。参见[引擎更新指导](https://developer.huawei.com/consumer/cn/doc/distribution/content/themes-engine-update-0000001055348462)。
- 官方全局变量规范定义了 `#screen_width` / `#screen_height` 等屏幕尺寸变量，可作为表达式适配的候选基础；实际主题脚本是否支持某种写法仍需按引擎版本确认。参见[全局变量规范](https://developer.huawei.com/consumer/en/doc/distribution/content/globavariant-0000001074475028)。
- 华为面向消费者的说明提到不同设备品类的主题资源包存在分辨率适配差异，手机购买的资源可能不能直接在平板或其他品类使用。参见[主题资源机型适配说明](https://consumer.huawei.com/cn/support/content/zh-cn15805698/)。
- 华为产品规格页可用于设备 profile 的初始面板数据：[Pura X](https://consumer.huawei.com/cn/phones/pura-x/specs/)、[Pura X Max](https://consumer.huawei.com/cn/phones/pura-x-max/specs/)、[Mate XT](https://consumer.huawei.com/cn/phones/mate-xt-ultimate-design/specs/)、[Pura X View](https://consumer.huawei.com/cn/phones/pura-x-view/specs/)。这些规格不能替代锁屏画布和安全区测量。

公开规范没有在上述资料中给出三折叠、Pura X / Pura X Max 的可直接套用锁屏布局模板。因此，本原型将这些作为可配置目标形态；拿到设备信息和主题样包后再建立设备 profile、折叠状态映射与布局规则。


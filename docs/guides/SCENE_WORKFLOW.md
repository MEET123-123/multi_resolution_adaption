# 交互场景、组件重排与主题回写

场景流程现在默认使用 `anchor`，加入九宫格、分组容器与 Kiwi 求解，规则和配置见 [ANCHOR_LAYOUT.md](ANCHOR_LAYOUT.md)。显式 `--algorithm adaptive` 使用旧版两列策略，`--algorithm reflow` 使用原示例策略。两款主题专用模板与统一运行入口见 [ADAPTATION_WORKFLOW.md](ADAPTATION_WORKFLOW.md)。

场景流程从真实锁屏 XML 构建元素对应、变量依赖、事件命令和状态图。它只读取 `unlock/lockscreen/manifest.xml`，不会把桌面小组件的 manifest 混入锁屏。

## 运行

```powershell
python multires.py scenes 'theme/五福临门 3D动态.hwt' `
  --source-profile theme/adaptation/wufu/source-profile.json `
  --profiles configs/device-profiles.example.json `
  --max-states 96 --max-depth 6 `
  --out theme/adaptation/wufu/scenes

python multires.py export theme/adaptation/wufu/scenes/project.json `
  --target tablet-mock-16-10-landscape `
  --out theme/adaptation/wufu/scenes/packages/tablet-landscape.hwt

python multires.py relayout theme/adaptation/wufu/scenes/project.json `
  --algorithm samples/custom_scene_layout.py:MySceneLayout
```

输出内容：

| 文件 | 用途 |
|---|---|
| `project.json` | 稳定元素 ID、原始节点、组件对应、状态图、所有设备场景布局及诊断 |
| `components.json` | 自动推断的组件与成员，可修改后通过 `--components` 重新运行 |
| `scene-graph.json` | 每个状态的变量、交互路径、转移结果、系统动作及探索边界 |
| `preview.html`、`assets/` | 使用真实素材的离线交互预览；需保持目录结构 |
| `scripts/<target>.xml` | 每个目标设备的状态相关布局已反写到 XML |
| `report.json` | 场景数量、交互覆盖、各设备回写数量和未解决项 |
| `packages/*.hwt` | 通过 export 生成的新主题包，仅替换锁屏 manifest，其他条目保留 |

## 交互与场景

状态图保留 down、move、up、tap 等动作以及命令的顺序和条件。按文件路径与 XML 位置生成唯一 ID，重复 name 不会覆盖元素。命令引入的隐式数值变量初始为 0；未提供的系统值也暂按 0，并写入诊断。可使用 `--inputs inputs.json` 提供 `{"system":{"hour24":14},"variables":{"panel":1}}`。

探索对按钮中心、方向拖动和线性触摸条件划分出的子区域采样。每个目标设备会在自身逻辑屏幕环境下重放已发现的交互路径，并重新计算可见元素、素材、坐标和状态。状态去重，保留同状态的自循环。按下后隐藏的按钮仍可完成捕获中的抬起事件。

状态数、深度和触摸采样都有上限，未探索分支会标为 `bounded`，不会伪造结果。源设备探索出的路径不保证覆盖目标设备上额外出现的条件分支。初始和交互后的画面按 2400ms 稳定时间采样；变量动画支持基本帧插值及 play/stop 采样，缓动、完整时间游标、取消延迟和连续手势仍需扩展。

预览器可切换设备、状态、重排开关、组件范围与交互热区，也可直接点击交互列表。系统应用、媒体和解锁命令记录在操作日志中。ImageSeries 根据字符映射与真实字形素材预览，其原生缩放语义列入诊断。

## 布局算法接口

新接口独立于旧版 `layout()`，通过 `--algorithm path/to/algorithm.py:Class` 加载：

```python
class MyAlgorithm:
    name = 'my-algorithm'
    def layout_scene(self, components, source_profile, target_profile, context):
        return {
            component['id']: {'sx': 1.0, 'sy': 1.0, 'tx': 0.0, 'ty': 0.0}
            for component in components if component['visible']
        }
```

坐标单位为锁屏引擎的逻辑坐标。项目保留原 `screenWidth`，用 `logicalHeight = screenWidth × 目标像素高 / 目标像素宽` 计算视口，防止重复缩放。`context` 包含当前目标状态、可见元素、状态图、模型、素材尺寸和逻辑视口。返回正数缩放、有限数平移；缺失的组件保持原位。示例见 `samples/custom_scene_layout.py`。

内置重排算法是可替换的示例策略：竖屏为上/中/下区域，宽屏为两列。顶层 XML 组保持整体，独立按钮根据空间包含关系与视觉组件配对。组件角色、成员和交互配对属于推断，需要在预览中检查；可通过 `components.json` 显式覆盖。布局函数可以读取变量状态，为不同场景返回不同排列。

`relayout` 从缓存的设备场景重新调用算法，更新预览和所有 XML，不必重新解包和探索交互。组件 `roots` 支持独立显隐的嵌套组；位置动画祖先下的节点避免拆分。更换算法后需重新执行 `export` 更新 HWT 候选包。

## 完整人工操作与布局聚类

探索以一次完整点击、滑动、长按或双击为单位，按顺序执行其 down/move/up/long/double 处理器。显示文本用的计数保留在实时运行状态中，不为每次数字变化创建布局；会影响后续条件、显隐的变量仍参与状态去重。连续操作通过具体变量状态执行，场景探索有数量、深度和坐标采样上限，并不证明全部可达状态已穷尽。每包默认最多采样 4096 个手势，可用 inputs 的 `interaction.maxExplorationGestures` 调整；报告记录实际采样数、是否触及预算及未展开状态。

`layout-clusters.json` 记录组件对应、几何证据与四种策略：`full-reflow` 整页重排、`local-reflow` 固定公共组件后局部重排、`overlay` 保持底层并布置弹窗、`reuse` 直接复用。复用不调用布局算法；局部/弹窗仅提交受影响组件。插件 `context.layoutRequest` 提供策略和对应关系，`fixedTransforms` 提供不可修改的公共组件变换，`baseTransforms`、`baseDescriptors` 与 `allComponentDescriptors` 提供上下文。内置算法找不到局部空间时提升为整页重排并记录原因。

全屏手势层默认让小范围按钮优先接收指针，这是脚本结构推断的预览策略，尚未由原生引擎证实。可在 `--inputs` JSON 中设置 `interaction.hitPolicy="strict-z-order"` 使用严格绘制顺序。显式 `interceptTouch="true"` 保留拦截。

实时预览命令：

```powershell
python multires.py serve theme/adaptation/wufu/scenes/project.json --port 8765
python multires.py serve theme/adaptation --port 8765
```

访问命令打印的本地网址，可连续点击、拖动、长按、双击、撤销操作以及导出当前会话 HWT 和交互记录 JSON。当前手势在松开后执行并渲染结束状态；不连续渲染手指按住期间的动画。直接打开 `preview.html` 是离线场景回放。

`python .\multires.py tools batch-convert` 先处理五福、北海，再处理 `theme` 根目录下的其他源 HWT。每包生成全部目标 XML 和两个代表性 HWT；`theme/adaptation/index.html` 汇总结果，`validation.json` 记录采样检查、跳过项和错误，`batch-summary.json` 记录源文件 SHA256 与未改变检查。其余设备可通过 `export` 单独生成 HWT。

## 原始直板机对比与素材迭代

预览默认并排显示原始直板机和所选适配设备。原始侧使用源分辨率、原脚本与无布局变换的独立运行状态；同一点击、拖动、长按或双击分别在两侧执行。两侧条件分支不同、按钮不可达或被遮挡时显示差异，不强制复制另一侧的结果。

一次手势结束后可用“播放过程”或进度条查看操作前、down、move、up、long、double 与结束状态。该功能记录脚本事件后的采样画面，并非原生引擎连续动画录像。撤销同时恢复两侧变量与时间；主题中的页面返回继续按实际脚本执行。完整历史、快照和分支说明见 [INTERACTION_STATE_MODEL.md](INTERACTION_STATE_MODEL.md)。

已支持包内 MP4 资源、视频分段请求与图片字形时钟。资源 URL 带内容版本，图片节点与视频节点缓存，避免逐帧回放时反复下载；原脚本引用的系统图标、用户相册、外部视频仍需提供资源，加载问题面板显示引用与源节点。浏览器不支持的视频编码也会单独报错。

```powershell
python .\multires.py tools refresh-previews --workers 3
python .\multires.py tools refresh-previews --only 五福 波蓝
python .\multires.py tools refresh-previews --ui-only
```

完整迭代重放已缓存操作路径，更新原始/目标画面与布局，反写脚本、候选 HWT，并检查几何和双侧手势恢复。`media-iteration-report.json` 与每包 `media-audit.json` 记录恢复的视频、图片时钟和未解决引用。修改交互可达性规则时应使用 `batch_convert.py` 重新探索。

## 反写方式与边界

批处理默认使用 3 个进程处理独立主题，可用 `--workers 1` 串行执行。五福、北海优先完成。含 `lockscreen` 和 `ucdlockscreen` 的包分别生成场景项目，并将两个适配脚本共同写入候选 HWT；汇总页可分别打开变体预览。对时间等派生变量引起的显隐差异，反写条件会保留对应派生变量，避免相同控制变量下选错布局。

反写将组件变换编译为带状态条件的引擎表达式，通过共享 Var 复用变换与场景判断。顶层节点加平移，子节点缩放相对坐标，已支持的位置/尺寸动画帧同步缩放。表达式继续引用原变量，原命令和条件保留。手势使用按下时捕获的组件变换映回原坐标，使按钮画面、热区和条件区域一起适配。

这不是完整的鸿蒙主题引擎。视频、系统控件、Mask、复杂字符串/属性表达式、完整动画时序等仍可能只占位或诊断；ImageSeries 的字形缩放和未解析的动态素材尺寸尤其需要原生引擎检查。新包是工程候选产物，尚未经过鸿蒙设备安装、主题引擎校验、签名或发布审核。导出不会覆盖源 HWT。

验证使用 `python -m unittest discover -s tests -v`，检查状态切换、重复元素对应、表达式白名单、动画采样、各采样状态的重排/反写坐标一致性、手势反映射和归档中其他文件保持一致。

# 交互状态与变量变化的数据模型

不要用“画面 A、画面 B、画面 C”列表代表交互。画面是运行状态的渲染结果；同一画面可能对应不同点击次数、手势阶段和后续行为。实现采用 **当前完整状态 + 事件处理规则 + 有序历史**，布局复用另行处理。

## 三层数据

| 层 | 保存什么 | 用途 |
|---|---|---|
| 模型定义 | 变量初始表达式、触发器、条件、命令、元素 | 规定收到事件后如何改变状态；源自实际 XML |
| 具体运行状态 | 变量覆盖值、系统输入、逻辑时间、动画控制数据、指针捕获者/起点、页面上下文 | 确定此刻以及下一步的行为 |
| 事件历史 | 输入事件、父节点、逐条命令变化、整体差量、外部动作记录、周期快照 | 保存路径、撤销、恢复和分支比较 |

`interaction_semantics.py` 的有界探索图仍用于自动采样和适配检查。它会合并部分状态、只保留代表路径，也受深度和预算限制，不能作为完整交互存档。新增的 `interaction_history.py` 为每次实际事件建立独立节点，不按画面相似度合并，不再截断为最近 50 步。实时会话仍保留原有 500 次手势的资源预算；保存文件保留该会话的全部已记录事件。

布局聚类可以复用同一套位置/缩放，但不能覆盖具体运行状态。例如前两次点击画面相同，`tapCount=1` 与 `tapCount=2` 仍是两个历史节点；第三次是否进入详情，由当前值和规则决定。不能只用可见元素列表判断行为等价。

## 状态的作用域

一个业务状态可以这样组织，实例见 `samples/interaction_state_demo.py`：

```json
{
  "variables": {
    "persistent": {"motion": 0},
    "session": {"tapCount": 3}
  },
  "navigation": [
    {"route": "home", "local": {"scrollY": 120}},
    {"route": "detail", "local": {"scrollY": 0}},
    {"route": "settings", "local": {"tab": "motion"}}
  ],
  "gesture": {"phase": "idle", "owner": null},
  "clockMs": 500
}
```

- `persistent` 是服装、配色、动效偏好等长期数据；原生主题对应声明了 `persist/globalPersist` 的变量。
- `session` 是本次会话的数据，例如连续点击次数、游戏进度。
- `navigation` 是页面栈，每个栈帧保留自己的滚动位置、选中标签等局部数据；新页面使用新帧。
- 手势期间还需要捕获目标、按下起点、移动阈值、按下时间等信息。原生 XML 执行器已保存捕获目标和起点，脚本自身的 `press_*` 等变量也全部保存。
- 通过表达式推导出的值，保留公式和依赖输入，不作为另一份可独立修改的事实。快照中的 `variables` 是运行时覆盖值，需配套模型定义恢复。

页面栈示例是可执行的通用业务模型。现有 HWT 用 `focus`、`look` 等脚本变量表达界面切换，记录器保存这些真实变量，不会猜测或自动把它们改写成页面栈。

## 事件与变量变化

输入不是只有“点击”：还包括 `down/move/up`、生命周期、系统输入更新，以及未来执行器支持的计时器到期和异步结果。事件应包含稳定目标 ID、输入参数和逻辑时间。事件归约函数读取当前状态、检查条件，按顺序执行命令，产生新状态：

```python
next_state, command_trace = reduce_event(current_state, event)
history.commit(event, next_state, commands=command_trace)
```

以下是单个历史节点的结构，省略了其他实际字段：

```json
{
  "id": "event-000005",
  "parent": "event-000004",
  "event": {"type": "set-motion", "value": 0, "timeMs": 500},
  "changes": [{
    "path": ["variables", "persistent", "motion"],
    "before": {"exists": true, "value": 1},
    "after": {"exists": true, "value": 0}
  }],
  "commands": [],
  "effects": []
}
```

差量包含前值、后值以及是否存在，能区分“没有变量”与“变量值为 null”，也能记录删除。路径采用数组，变量名中出现 `/`、`~` 等字符不需要额外转义。列表作为整体替换。

**整体差量与逐条命令记录不能互相替代。** 一次手势可能包含 `pressed: 0→1→0`，最终没有净变化，但中间影响命令条件与视觉反馈。`Runtime.command_log` 按命令执行顺序保存实际修改；`valueChange` 保存命令目标变量的求值前后值，`changes` 保存原始覆盖数据的变化。原始覆盖不存在时，求值仍可能来自变量初始表达式，所以这两类值有所区别。

原始直板机和适配设备分别保存状态与命令流，命令附带 `surface`；顺序在各自画布内有效，两个执行器没有共同的跨设备命令顺序。

## 连续操作、返回和撤销

示例的实际路径如下，每一步动态生成一个历史节点：

```text
home(count=0)
  → 点击 → home(count=1)
  → 点击 → home(count=2)
  → 点击 → detail(count=3)
  → 打开设置 → settings
  → motion=0
  → 页面返回 → detail(count=3, motion=0)
  → 页面返回 → home(count=3, motion=0)
```

页面返回是业务事件：弹出页面栈，保留已修改的偏好和会话数据。需要取消编辑时，可为页面保存草稿并规定退出时丢弃，而不是统一回滚全局变量。若需恢复父页局部状态，保存在父页栈帧中。

撤销是调试操作：移动历史游标，恢复之前的变量、输入、逻辑时间和手势上下文。因此撤销最后一次页面返回，会回到详情页，`motion` 仍为 0；撤销修改动效那一步，才会恢复之前的动效值。预览器按钮已改名为“撤销操作”，主题内的返回控件继续执行自身脚本。

撤销后继续操作时，新节点指向当前节点，保留旧后继，形成分支。点击两次再选择不同服装、或进入设置后分别选择不同档位，都不必预先列举所有组合。`children()` 获取当前节点的分支，`checkout(event_id)` 精确恢复某一条路径；外部动作只作为记录读取，恢复不会再次发出解锁命令。

## 保存、恢复与接入

默认每 16 层保存一次完整快照，其余事件保存差量。恢复某节点时，沿父链找到最近快照，再顺序应用差量。事件不按画面合并；检查点只优化恢复成本。

```python
import json
from multires_app.interaction.interaction_history import InteractionHistory

# 存档（JSON 包含初始快照、全部节点、检查点和当前游标）
document = history.to_dict()
path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

# 恢复游标所在的具体状态，无须重新执行外部命令
restored = InteractionHistory.from_dict(json.loads(path.read_text(encoding="utf-8")))
current = restored.current
restored.undo()
restored.checkout("event-000005")
```

`interaction_server.InteractiveSession` 已默认接入。导出会生成 `adapted.hwt`、`project.json` 和 `interaction-history.json`，网页同时提供 HWT 与记录的下载链接。恢复时配套使用保存的项目，模型哈希和设备视口必须一致：

```python
from multires_app.preview.interaction_server import InteractiveSession
project = json.loads(project_path.read_text(encoding="utf-8"))
session = InteractiveSession(project, "tablet-landscape")  # 使用实际 profile ID
session.load_history(json.loads(history_path.read_text(encoding="utf-8")))
```

本地接口新增 `POST /api/history`（读取记录）、`POST /api/checkout`（指定 `event` 恢复分支）；沿用原有 token 和 session 校验。

两款交互增强主题的 XML 验证执行器也可开启逐指针事件记录：

```python
from multires_app.engine.scene_engine import load_model
from multires_app.interaction.tablet_interactions import InteractionSession

model = load_model(package_path, manifest="unlock/lockscreen/1600_2560_manifest.xml")
runner = InteractionSession(model, 2560, 1600, record_history=True)
runner.gesture("character-bear")  # 小狗使用 character-white / character-brown
runner.gesture("character-bear", dx=-200)
document = runner.journal.to_dict()
runner.checkout("event-000000")
```

运行示例：

```powershell
python samples/interaction_state_demo.py
python samples/record_tablet_interactions.py
python -m unittest discover -s tests -p "test_*.py" -v
```

输出 `samples/interaction-state-demo/summary.json` 和 `interaction-history.json`，包括连续点击、设置内交互、逐层返回、撤销及分支。

第二条命令从两款实际 v2 HWT 的横屏脚本记录连续轻触、滑动换装、长按简洁模式及退出，输出到 `theme/tablet-2560x1600/v2/interaction-history/`，并校验 JSON 保存再读取后状态一致。这两款原生包的轻触仍是原有反馈；“第三次进入详情”的规则属于上面的通用示例，没有擅自改写原生包行为。

## 能力边界

这套数据结构保存已执行路径，不承诺穷尽所有交互。自动验证应围绕条件分支、阈值前后、重复操作、返回与取消建立场景，并报告探索预算。

当前 XML 解释器仍是离线近似：延迟命令按到期顺序执行为稳定快照，尚无可中途取消的真实调度队列；动画播放控制也有已注明的近似。新增记录不能弥补执行器尚未模拟的行为。未来支持真实定时器/异步任务时，应把待执行任务（含所属页面、到期时间、取消标记）和随机数状态纳入快照，并把到期/结果作为显式事件，才可准确恢复。

存档属于适配工具，不写入 HWT 作为新的鸿蒙脚本语法；HWT 仍由原生变量、触发器、条件和命令运行。设备重启只恢复脚本声明的持久变量，与调试器恢复完整会话是两个不同过程。原生指针仲裁、跨方向持久化及安装效果仍需平板实机验证。

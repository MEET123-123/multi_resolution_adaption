# 实时预览：导入主题包、手势与动画

一个本地工具：把 `.hwt` 丢进去，直接在浏览器里看锁屏画面，点击/长按/双击/滑动都会执行主题自己的 `Trigger` 与命令，脚本里的位移动画、透明度动画、逐帧动画会实时播放。

```powershell
# 直接带主题启动
.\.venv\Scripts\python.exe .\multires.py live "theme\半岛日记改.hwt" --port 8767

# 空启动，之后在页面里拖入主题包
.\.venv\Scripts\python.exe .\multires.py live --port 8767
```

打开打印出来的地址即可。默认只监听 `127.0.0.1`，接口带一次性 token，导入上限 300MB。

## 一、规范来源

| 来源 | 用途 |
| --- | --- |
| `F:\theme_studio\ThemeStudio\resources\resources\dynamic-template\manifest.xsd` | 控件、动画、Trigger 的字段与类型定义（Theme Studio 11.0.20.310 随包发布） |
| Theme Studio `system-template/*/unlock-dynamic/lockscreen/manifest.xml` | 官方示例：全屏 Button + `action="up"` + `#touch_begin_y-#touch_y` 判距解锁 |
| 华为开发者文档《控件：按钮 Button》（HarmonyOS 5.0+ 主题引擎规范） | `Trigger action` 取值：`down` / `up` / `double` / `resume` / `pause` / `long`(500ms) / `longClick`(7.0+) / `click` |

实现严格按上述定义取值，没有自定义扩展字段。`longClick`（7.0 起）暂未实现，遇到时在诊断面板提示。

## 二、架构：为什么不是"每帧算一次"

```
浏览器                                    Python
  │  pointerdown ──► /api/press ──────► 命中测试（Button / Slider / Unlocker）
  │  pointermove ──► /api/drag move ──► 更新 #touch_*、move_x/move_y/progress → 重算快照
  │  pointerup ────► /api/drag end ───► 派发 up Trigger、判定终点/解锁/回弹
  │  （非拖动）────► /api/gesture ────► perform_action → Trigger/命令 → settle
  │  ◄── 新 plan（revision 变化时含动画轨道）
  │
  ├─ requestAnimationFrame：本地插值位置/透明度/尺寸/逐帧素材   ← 60fps，不经过 Python
  └─ 每秒 /api/plan：刷新时钟文本、位图字形、变量快照
```

关键取舍：

- **运行时只有一个**。`LiveSession` 常驻一个 `Runtime`，手势直接执行脚本里的命令，不是回放预计算快照。
- **动画在客户端插值**。服务端把 `PositionAnimation` / `AlphaAnimation` / `SizeAnimation` / `SourcesAnimation` 解析成关键帧轨道，浏览器按同一套公式采样（含 `delay`、`loop`、`repeat` 语义）。这样 60fps 不需要每次走 HTTP 和 Python 求值。
- **基线增量而非绝对坐标**。`snapshot()` 已经按当前 `time_ms` 采样过一次，客户端只叠加"当前值与基线值之差"，避免两套几何算法打架。
- **手势一次性提交**。按下记录起点，抬起时按位移与时长判定 `tap` / `long-press` / `double-tap` / `swipe`，再交给 `perform_action` 按官方事件序列（`down → move×4 → up → tap → click`）执行。
- **拖动是连续事件流**。手指位移超过 8px 才升级为拖动（避免抢走点击），之后 `pointermove` 以「最多一个请求在途」的方式节流提交，服务端每次都重算快照，因此主题里 `#touch_*` 驱动的表达式（页面跟手、卡片翻页）会真实跟手，而不是等到抬手才跳变。

## 三、能力对照

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 导入 `.hwt` / `.zip` | 支持 | 命令行参数或页面拖入；自动解包素材到 `preview-live/` |
| 真实素材渲染 | 支持 | 图片、位图字形（`ImageSeries`/`srcid`）、文本、视频（`<video>` 自动播放） |
| 点击 / 长按 / 双击 / 滑动 | 支持 | 按 `Button` 绘制顺序命中，遵循 `specific-controls-before-global-gestures` 策略 |
| 位移 / 透明度 / 尺寸动画 | 支持 | 关键帧、`delay`、`loop`、`repeat` |
| 逐帧动画（`SourcesAnimation`） | 支持 | 按时间切换素材 |
| 视频 | 支持 | 浏览器原生播放，循环与静音 |
| 变量动画（`VariableAnimation`） | 每秒采样 | 需要重新求值表达式，跟随 1Hz 刷新 |
| `Slider` / `Unlocker` 连续拖动 | 支持 | 按下命中、跟手进度、终点判定、回弹、`#name.move_x/move_y/progress` 注入 |
| 拖动中的页面跟手 | 支持 | 注入 `#touch_*`，主题自己的表达式驱动位移，无需额外适配 |
| 松手后交给主题判定 | 支持 | `EndPoint` 的 `up` Trigger 与叠加 Button 的 `up` 一起派发 |
| 缓动（`easeType`） | 未模拟 | 与项目现有解释器一致，按线性插值 |
| 外部命令（相机、天气、音乐） | 只记录 | 诊断面板可见，不调用系统能力 |
| 农历 / 天气 / 步数 | 占位 | 缺系统数据源时显示 `N` 或占位图，不伪造 |

### 两类 Slider

主题里的滑动控件写法差异很大，预览按几何形状分成两类处理：

| 类型 | 判定依据 | 处理方式 |
| --- | --- | --- |
| `track` 固定轨道 | `StartPoint` / `EndPoint` 用常量或屏幕尺寸表达 | 按 `Path` 或 `EndPoint` 盒子算出方向与行程，注入 `#name.move_x` / `move_y` / `progress`，命中 `EndPoint` 即 `reached` |
| `touch` 跟手轨道 | `StartPoint` / `EndPoint` 里出现 `#touch_*` | 不伪造行程，只把真实触点写进 `#touch_begin_x/y`、`#touch_x/y`，松手时派发 `EndPoint` 的 `up`，由主题自己的条件决定结果 |

`touch` 类型没有静态轨道，所以进度条显示「跟随手指」，`reached` 返回 `null`，界面只用「主题是否真的改了状态」来判断。

### 叠加 Button 的派发

多个主题把一次上滑拆给好几层全屏 `Button`：一层在 `down` 时给滑动「上膛」，另一层在 `up` 时比较 `#touch_begin_y - #touch_y` 决定是否解锁。只做最顶层命中会漏掉真正的解锁分支，所以拖动路径会把**触点覆盖到的所有可见 Button** 的 `down` / `up` 一起派发，与平台对重叠 Button 的处理一致。

> 已知简化：手指按下后滑出某个 Button 再松手，预览仍会派发它的 `up`；实机在部分场景下不会。

## 四、补齐的系统变量

主题脚本普遍使用 `#system.time.hour1` 这类变量，原解释器只提供 `#hour` / `#minute`，导致位图时钟显示为 0。实时预览在每帧推进时注入：

```
system.time.hour1 / hour2 / min1 / min2 / sec1 / sec2
```

同时把 `hour`、`hour24`、`minute`、`second`、`year`、`month`、`date`、`day_of_week` 对齐到真实时间，所以时钟会跟着系统走，而不是固定在 9:41。

> 这些变量目前只在实时预览里注入，没有改 `Runtime` 的默认值，避免影响批量转换与已有回归结果。

## 五、界面说明

- 左：设备框，`80%` 缩放可调；底部显示画布尺寸与元素数。拖动滑动控件时，屏幕下沿出现进度条，到达终点会变绿。
- 右「手势」：可交互控件数、触发器数、上次手势、操作记录；动画开关、播放速度、缩放。
- 右「变量」：当前运行时变量快照（最多 40 个），可看到点击前后数值变化。
- 右「诊断」：解释器不支持的表达式、缺失素材、外部命令等，全部显式列出。

## 六、排错

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 画面全黑 | 主题没有 `Lockscreen` 根节点，或背景依赖系统壁纸 | 诊断面板看 `asset` 告警 |
| 点击没反应 | 该点没有 Button，或 Button 被上层全屏 Button 遮挡 | 用「诊断」确认触发器数量；先点可见控件 |
| 时钟显示 0 或 N | 依赖未提供的系统数据 | 见第四节；农历/天气本就不提供 |
| 动画不动 | 轨道关键帧时长为 0，或 `delay` 大于当前时间 | 调「速度」滑杆，或等 `delay` 到期 |
| 元素尺寸为 0 | 脚本用变量乘宽高（如 `302*#kptan2`），初值为 0 | 触发对应手势后才会展开，属正常 |
| 拖动没反应 | 起点不在 `StartPoint` 盒子里，或该主题没有滑动控件 | 从手柄或滑动区域起手；`touch` 类控件整屏可拖 |
| 拖到头了但没解锁 | 解锁条件在叠加 Button 的 `up` 上，条件未满足（位移不够） | 看「变量」里 `#touch_begin_y - #touch_y` 的实际值 |
| 松手后进度条卡住 | 服务端 `end` 未送达（页面被切走） | 点「重置状态」 |

## 七、测试

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_live_preview -v
```

覆盖：计划结构、点击执行触发器、落点无控件时不改状态、重置、非法手势拒绝，以及
`press` / `drag start|move|end` 的进度、夹取、终点判定、回弹、解锁、变量清理，和一条
真实 HTTP 往返。`tests/drive_live_drag.cjs` 是配合 Chrome DevTools 协议做端到端拖动的脚本，
不是 unittest 的一部分。

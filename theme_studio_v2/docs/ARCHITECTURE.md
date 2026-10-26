# 引擎设计

> 本文保留早期手工约束/线性表达式分支的设计记录。自动重构的当前实现以 [响应式重构架构](RESPONSIVE_ARCHITECTURE.md) 为准；不能把这里的“线性坐标”推广到内容度量、分区、障碍或全部响应式求解。

## 分层

```
Doc (JSON)  ──►  resolveDoc(doc, device)  ──►  ResolvedScene  ──►  renderer / validator
                        │
                        └──►  exportLockscreenXml(doc, mode)  ──►  manifest.xml
```

- **Doc** 是唯一事实来源，纯 JSON，可版本管理。
- **resolveDoc** 是纯函数：同一份 Doc + 同一台设备，永远得到同一个 ResolvedScene。
- **渲染器与导出器都消费同一套几何**，所以画布上看到的就是脚本里算出来的（除文字度量误差）。

## 单位

所有几何量在 Doc 里都是**设计画布像素**（默认 1080×2340），与主题脚本的 `x/y/w/h` 一致，导出无损。

响应式来自 `constraints`，不来自数字本身。这一点是刻意的：设计师仍然按像素摆，但每个数字的含义被约束重新定义了。

## 解算顺序

`resolveNode` 对每个节点：

1. **合并变体**：`breakpoint` → `breakpoint-shape`，浅合并。
2. **算宽度**：`fixed` 用 `frame.w * sx`；`fill` 时按锚点拉伸；`hug` 且横向自动布局时由子元素求和。
3. **算高度**：`fixed` / `fill` / `hug`（自动布局求和或文字行高），然后 dp 钳制。
4. **aspect 覆盖**：`h = w * aspect`；如果钳制把 h 压住，反推 w，保证形状不变形。
5. **算 x / y**：按锚点，见下表。
6. **安全区推挤**。
7. **递归子节点**：自动布局父级先摆位置，非自动布局父级保留画布绝对坐标。

### 锚点公式

设 `sx = W/W0`，`sy = H/H0`，`rg = W0 - x0 - w0`（右边距），`bg = H0 - y0 - h0`（底边距）。

| 锚点 | 结果 |
|---|---|
| `left` / `scale` | `x = x0 * sx` |
| `right` | `x = W - rg*sx - w` |
| `center` | `x = W/2 + (cx0 - W0/2)*sx - w/2` |
| `leftRight` + fill | `w = W - x0*sx - rg*sx` |
| `top` | `y = y0 * sy` |
| `bottom` | `y = H - bg*sy - h` |
| `center` | `y = H/2 + (cy0 - H0/2)*sy - h/2` |
| `scale` | `y = y0 * sx` |
| `topBottom` + fill | `h = H - y0*sy - bg*sy` |

`sx` 管尺寸、`sy` 管垂直位置，是这套引擎与旧版最根本的区别：尺寸跟着宽度走（构图比例不变），位置跟着高度走（底部贴底、居中仍居中）。

## 自动布局

父级 `autoLayout` 时，子节点不再看自己的 `x/y`，而是：

1. 沿主轴累加子尺寸 + `gap`。
2. 按 `main` 计算起始游标（start / center / end / spaceBetween）。
3. 按 `cross` 决定交叉轴位置（start / center / end / stretch）。
4. `fill` 的子节点吃满交叉轴内宽。

父级 `sizing.h === 'hug'` 时，高度 = `padT + Σ子高 + gap*(n-1) + padB`，在摆位置之前先测一遍。

## 变体级联

```ts
keys = shape === 'portrait' ? [bp] : [bp, `${bp}-${shape}`]
```

按顺序浅合并，后面的覆盖前面的。形状判定：

```ts
ratio = width / height
ratio > 1.15   → landscape
ratio >= 0.87  → square
otherwise      → portrait
```

折叠外屏（980×980）落在 `square`，这是宽度断点无法区分它的原因——它和手机一样窄，但几乎没有垂直空间。

## 质量门禁

`validateScene` 输出结构化问题，UI 与 CLI 共用：

| code | 级别 | 判据 |
|---|---|---|
| `out-of-canvas` | error | 矩形越出画布 |
| `text-overflow` | error | 自动缩字后仍放不下 |
| `control-overlap` | error | 解锁区/按钮点击区互相重叠（会抢触摸） |
| `text-collision` | warn | 两块文字重叠 > 12% 较小面积 |
| `text-too-small` | warn | 字号 < 11dp |
| `touch-too-small` | warn | 点击区高度 < 44dp |

`control-overlap` 是实机最容易翻车的点：解锁热区覆盖整条底部时，相机按钮永远点不到。

## 导出：线性形式

引擎表达式支持四则运算与 `#screen_width` / `#screen_height` / `#var` 引用（真实主题里就有 `max(min(#mjui_move_x+...,1080),-0)` 这类写法）。由于

```
#screen_width  === #ts_sx * designWidth
#screen_height === #ts_sy * designHeight
```

**任何坐标都是 `sx` 与 `sy` 的线性组合**。所以导出器内部用

```ts
interface Lin { sx: number; sy: number; c: number }   // sx*a + sy*b + c
```

表示几何量，加减乘都是闭运算。好处：

1. 自动布局可以符号化求和，不需要先烘焙成数字。
2. 相对坐标（子减父）直接相减即可。
3. 烘焙模式把 `sx`/`sy` 置 0、`c` 填真实像素，同一套代码两种输出。

`expr()` 会把系数为 0 的项丢掉、把 `1` 写成裸变量、并让正项排前面，所以生成结果可读：

```
y="#ts_sy*2152-#ts_sx*148"
```

而不是 `-1*#ts_sx*148+1*#ts_sy*2152+0`。

### 相对坐标

运行时把子元素坐标累加到父级原点上（`scene_engine.py` 里 `walk(c, px+x, py+y)` 就是证据）。所以导出器对每个节点都减去父级绝对坐标，嵌套多少层都不会偏移。

### 组背景

`Group` 的 `fill` 在脚本里没有对应属性，导出器会补一个首个子元素 `<Rectangle name="{组名}-bg" x="0" y="0" .../>`，坐标相对组原点。

同时，组带填充时不再给 `<Group>` 写 `alpha`——透明度属于背景，写在组上会把文字也一起淡掉（叠加两次）。

## 为什么不做「运行时 min/max 钳制」

`min/max` 在引擎里可用，但 dp→px 需要设备密度，而密度 = `屏幕宽px / 逻辑宽dp`，后者在脚本运行时不可得。所以 dp 钳制只能在设计期按机型解算，也就是烘焙模式。响应式单文件会跳过它们，并在文件头注明跳过了几个。

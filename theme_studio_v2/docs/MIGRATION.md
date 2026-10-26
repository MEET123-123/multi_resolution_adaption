# 旧版迁移对照

> 2026-10-24 更新：下文是已有的旧版迁移笔记，不代表本轮重新核验旧二进制。v2 当前已增加设计关系推断、可替换规划器、设备姿态和逐目标完整交付，见 [当前架构](RESPONSIVE_ARCHITECTURE.md)。内置参数是示例配置，并非“8 台真实机型”的官方参数；`multiResolution` 不能当作已经核实的系统适配开关，当前生成器不再写入该字段。

本文记录 `F:\multi-res\theme_studio`（ThemeStudio 20.310.0 / EMUI 11）里与多分辨率相关的实现，以及 v2 的替代方案。所有结论都来自对 `resources/app.asar` 的解包（`main.js` 1.32 MB、`renderer.js` 2.02 MB）。

## 旧版做了什么

### 1. 设计画布写死 18:9

```js
class theme_generator_ThemeGenerator { constructor(e,t){ this._screenWidth=e; this._screenHeight=t } ... }
// 调用点
(1080, 2160)
```

`generateTheme` 里再按 `FHD / WUXGA / WQHD` 三档决定 `screenWidth` 属性：FHD→1080，WUXGA→2560/1600，WQHD→2480/2200/1160。

**问题**：画布高度 2160 与设备真实高度无关。2340、2400、2232 这些主流尺寸全部落在「按 2160 计算，然后被拉伸」。

### 2. 一行 `_parseY` 冒充底部锚定

```js
_parseY(e){ return e > 1080 ? ` y="#screen_height-2160+${e}" ` : ` y="${e}" ` }
```

另一处：

```js
_parseY(e){ return e<1080 ? e : 2160 - e }
```

**问题**：把「y 大于 1080」当作「这个元素想贴底」的隐式约定。于是

- 设计稿上 y=1200 的装饰，在 2400 高的屏幕上会被算成 `2400-2160+1200 = 1440`，而不是按比例的 1333；
- 2160 与真实高度差得越多，偏得越远；
- 无法表达「距底 200px」这种真正的意图，只能靠猜。

### 3. 设备白名单

`FHD / WUXGA / WQHD` 三档，`unlock/theme.xml` 里还有 `<item maxHeight="2160"/>`。

**问题**：折叠屏（980×980、2120×1320）、平板（2560×1600、1600×2560）、宽屏直板（1320×2232）没有对应档位，只能落进最近的档位再拉伸。

### 4. 字号与缩放只有一位小数

```js
sizeRate: (u.width/1080).toFixed(1)
```

**问题**：1.0/1.1 的量化误差在 1320 宽的屏幕上就是 130px 的偏差。

### 5. 默认布局硬编码

```js
{ id:'time', x:0, y:0, w:1080, h:2160, clock:{ hourHigh:{ x:180, y:400, w:100, h:200 }, ... } }
```

**问题**：连时钟数字的位置都是常量，换机型必然错位。

## v2 的替代

| 旧版 | v2 | 效果 |
|---|---|---|
| `_parseY` 的 1080 阈值猜测 | `constraints.v = 'bottom'` 显式声明 | 意图可表达、可校验，不再依赖数值巧合 |
| `#screen_height-2160+y` | `y = H - bg*sy - h`，导出成 `#ts_sy*2152-#ts_sx*148` | 任意高度都贴底 |
| 1080×2160 画布 | 1080×2340 画布 + `sx`/`sy` 双比例 | 尺寸跟宽度、位置跟高度 |
| FHD/WUXGA/WQHD 枚举 | 8 台真实机型 + 断点 + 形状分类 | 折叠屏/平板有专属布局 |
| `toFixed(1)` 的 sizeRate | 线性形式精确到 4 位小数 | 无量化误差 |
| 硬编码时钟坐标 | 自动布局组 + 约束 | 时钟长高日期自动跟随 |
| `maxHeight="2160"` | `<item maxHeight="{设计画布高}" multiResolution="true"/>` | 不再把设备高度钉死 |
| 无可视化校验 | 6 类质量门禁，UI/CLI 共用 | 越界/溢出/抢触摸在导出前暴露 |

## 迁移步骤（把旧工程搬进 v2）

旧版工程目录（`.hwt` 里的 `unlock/lockscreen/manifest.xml`）是「已编译」的脚本，不是设计稿。要迁移：

1. 打开旧脚本，把每个可见元素的 `x/y/w/h` 抄进 v2 的 `frame`（单位就是设计像素，通常是 1080×2160 坐标系）。
2. 按意图补约束：
   - 底部的东西（解锁条、相机/电话）→ `v: 'bottom'`
   - 居中元素（时钟、日期、签名）→ `h: 'center'`
   - 通栏背景 → `h: 'scale', v: 'scale'` + `sizing: { w: 'fill', h: 'fill' }`
3. 把画布高度从 2160 改成 2340（或目标机型），重新摆一遍顶部区域——旧稿在 2160 上挤，在 2340 上会松，这是正常的。
4. 跑 `verify`，按报错逐个修；再切到平板/折叠屏缩略图，补 `medium` / `expanded` / `*-square` 变体。
5. 导出 `bundle`，用 `devices/<机型>/` 下的烘焙脚本做真机验收。

第 3 步无法自动化：旧稿的纵向构图是按 18:9 排的，换画布必然要人重新看一眼。

## 保留了什么

- 主题脚本格式（`<Lockscreen>` / `<Var>` / `<Image>` / `<Text>` / `<Button>` / `<UnlockerCommand>`）。
- 包结构（`description.xml` / `unlock/theme.xml` / `unlock/lockscreen/manifest.xml`）。
- 引擎表达式能力（四则运算、`#screen_width`、`min/max`）。
- 旧版目录原样不动，作为对照与格式参考。

# HarmonyOS 5.0 及以上主题规范：来源与核对记录

核对日期：2026-10-19。项目基线：`9c7cfa2`。

本文记录这次实际阅读的官方资料及其适用范围。正文解释写在 [迁移说明](../guides/HARMONYOS5_MIGRATION.md)，具体任务写在 [代码改造清单](../guides/HARMONYOS5_IMPLEMENTATION_PLAN.md)。

## 一、已核实的官方资料

| 编号 | 官方页面 | 本次核实内容 | 取证方式 |
|---|---|---|---|
| S01 | [HarmonyOS 5.0 主题设计指导及规范](https://developer.huawei.com/consumer/cn/doc/content/themes-specification-next-0000002371431700) | 动态锁屏天气与天气跳转；安全区域；兜底图；深浅模式；官方工具出包要求 | 浏览器读取正文，页面标示更新时间 2026-10-15 |
| S02 | [锁屏：Lockscreen](https://developer.huawei.com/consumer/cn/doc/content/themes-engine-next-lock-0000002244659534) | 5+ 仍使用锁屏 XML；`screenWidth` 为虚拟画布宽度；`frameRate` 的含义 | 浏览器读取正文 |
| S03 | [按钮：Button](https://developer.huawei.com/consumer/cn/doc/content/themes-engine-next-base-button-0000002471395018) | 可见性取值规则；事件种类；根标签、设备和最低版本范围 | 官方页面索引正文 |
| S04 | [Intent 命令：IntentCommand](https://developer.huawei.com/consumer/cn/doc/content/themes-engine-next-base-intentcommand-0000002471235064) | 5+ 仍使用 `package` / `class` 参数；原生应用标识；属性的版本差异 | 浏览器读取正文 |
| S05 | [图片多语言：PicMultiLanguage](https://developer.huawei.com/consumer/cn/doc/content/themes-engine-next-base-picmulti-0000002504354947) | 语言 JSON、字符串引用、图片路径随语言变化；从 5.0 起的支持范围 | 官方正文 |
| S06 | [情绪感知：EmotionCommand](https://developer.huawei.com/consumer/cn/doc/content/emotioncommand-0000002551079172) | 从 6.0 起且仅部分设备支持；锁屏范围与默认交互设计要求 | 官方页面索引正文 |
| S07 | [手机／折叠屏／平板主题规范区别：5.0 及以上](https://developer.huawei.com/consumer/cn/doc/content/differences-themes-next-0000002505040489) | 官方将两折叠、平板、Pura X、Pura X Max 分别列为专项设计规范 | 浏览器读取目录正文；此页主要提供专项入口 |
| S08 | [Pura X 主题设计指导及规范](https://developer.huawei.com/consumer/cn/doc/content/purax-themes-next-0000002505115171) | 内外屏职责不同；外屏静态锁屏与方形素材；不能把内屏交互方案直接当作外屏发布方案 | 浏览器读取正文 |
| S09 | [Pura X Max 主题设计指导及规范](https://developer.huawei.com/consumer/cn/doc/content/pura_x_max-0000002581312329) | 折叠态、展开横屏、展开竖屏三套样式；设计尺寸与安全区范围 | 浏览器读取正文 |
| S10 | [系统数据开放](https://developer.huawei.com/consumer/cn/doc/content/themes-engine-next-base-sysdata-0000002471395102) | 系统版本、手电筒、自动旋转等数据类型；系统控制变量与普通用户变量的区别 | 浏览器读取正文 |

以上资料属于华为**主题服务**文档。ArkUI 应用布局、OpenHarmony 应用 SDK、Android 主题教程不用于证明主题引擎的语法或发布规则。

## 二、关键核对结论

### 1. 规范版本需要检查到属性和事件

S03 将 `longClick` 标为 7.0 起的事件；S04 将 `uri` / `type` 标为 6.0 起、部分其他属性标为 7.0 起；S06 的能力还受设备限制。因此，一个位于“5.0 及以上”目录下的标签，不能直接判为所有 5.x 设备都支持。

这也是本项目需要把 `targetEngine` 字符串升级为“规范版本 + 根场景 + 设备能力 + 属性／事件规则”的依据。该数据结构属于项目设计建议，不是官方主题包字段。

### 2. 应用图标标识与应用拉起标识应分别核对

S04 的相机示例使用 `com.huawei.hmos.camera` 与 `com.huawei.hmos.camera.MainAbility`，天气对应 `com.huawei.hmsapp.totemweather` 与其页面标识。实际迁移时应以命令规范及目标 ROM 验证为依据。

不能仅凭图标目录中的包名推导应用页面，也不能将 XML 属性直接替换为应用 SDK 的 `bundleName` / `abilityName`。

### 3. 安全区需要分设备、分形态

S01 包含状态栏、屏内指纹、通知胶囊等区域；S09 另列锁头、解锁提示、锁屏小工具及左右边栏等区域。这里限制的是交互占用与可识别性，并不等同于把所有画面缩进一个矩形。

文档示意图中的像素值只属于相应示例画布。设备配置应保存示例或实测的来源与坐标单位，再转换到逻辑画布。

### 4. 官方工具是最终发布链的一部分

S01 的制作注意事项要求联盟上传资源经官方主题工具制作且不作二次修改。项目当前的 XML / HWT 反写适合作为适配工程和候选结果；最终发布流程应接入官方工具导入、校验、出包和对应 ROM 自测。

官方工具导出后的文件若再次被本项目改写，应重新回到工具流程，不能沿用前一次出包证据。

## 三、已找到入口、仍需在实现阶段补齐的资料

这些入口已在官方主题文档目录中确认；下列细节未在本次形成完整可执行规则，不能据此硬编码。

| 官方入口 | 实现前需要补齐的证据 |
|---|---|
| [5+ 引擎规范目录](https://developer.huawei.com/consumer/cn/doc/content/themes-engine-next-0000002242508358)中的全局变量、天气、运动健康等专项页面 | 全局变量与数据绑定器完整表，特别是当前方向、折叠状态、数据不可用与刷新语义；不能把自动旋转开关当作当前方向 |
| [引擎介绍](https://developer.huawei.com/consumer/cn/doc/content/themes-engine-next-introduction-0000002279818389) | 官方工程模板、模块发现方式及目标工具版本 |
| [两折叠主题规范](https://developer.huawei.com/consumer/cn/doc/content/unfolded-themes-next-0000002472955166) | 形态覆盖、资源路由、安全区示意与官方样例 |
| [平板主题规范](https://developer.huawei.com/consumer/cn/doc/content/pad-themes-next-0000002472635166) | 横竖屏完整模板、入口和安全区数据 |
| [5.0 升级 6.0 指导](https://developer.huawei.com/consumer/cn/doc/content/themes-specification-next6-0000002531251229) | 增量必做模块、既有主题升级要求 |
| [6.0 升级 6.1 指导](https://developer.huawei.com/consumer/cn/doc/content/harmonyos_6_1-0000002550712266) | 6.1 增量要求及工具匹配关系 |
| [命令组](https://developer.huawei.com/consumer/cn/doc/content/themes-engine-next-base-groupcommand-0000002471235074)、[周期命令](https://developer.huawei.com/consumer/cn/doc/content/themes-engine-next-base-cyclecommand-0000002504354935) | 执行顺序、取消、延时与生命周期样例 |
| [主题测试审核规范](https://developer.huawei.com/consumer/cn/doc/content/harmonyos5-theme-test-0000002318301165) | 当前上架自检项与设备验收材料 |

三折叠的完整原生主题路由和安全区，也必须通过对应官方模板及设备核实。当前项目中的三折叠预览尺寸不构成这项证据。

## 四、发现的文档歧义及处理原则

1. S02 对“虚拟高度按屏幕宽高比计算”的文字定义与其中某个数值例子不一致。实现使用明确的坐标换算模型，并以官方工具和实测画布核对，不能照抄该例子中的高度。
2. S09 表格列出的横竖屏尺寸，与其后安全区示例段落中的方向名称存在对调现象。规范画布暂按表格登记，安全区图的方向和矩形仍需对照模板验证。
3. 设计画布、设备面板像素、封面图、详情图、桌面视频、锁屏视频是不同用途。某一栏的尺寸和编码限制不能移用到其他栏。
4. 这次未取得官方工程导入接口、命令行批量出包接口或签名字段规范的完整证据。改造方案将其列为工具集成任务，不预设 HAP 格式、签名算法或未经核实的目录名称。

## 五、维护方法

每条机器可执行规则应记录：来源链接、核对日期、起始版本、根场景、设备范围、字段粒度和验证样例。页面更新或实测与文档不一致时，将该规则标为待核实，保留原证据，避免自动覆盖已验收的适配器。

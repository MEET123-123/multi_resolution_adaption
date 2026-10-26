# Theme Studio v2：从设计到多设备锁屏脚本

这是 `F:\multi-res\theme_studio` 的独立替代编辑器。旧目录是本地桌面程序；v2 在本目录维护可读源码。既有 HWT 的交互探索、动画解释和精确反写继续由 [Python 引擎](../src/multires_app/README.md) 负责。

## 先运行

```powershell
cd F:\multi-res\theme_studio_v2
npm ci
npm run dev
```

打开 http://127.0.0.1:5178/ 。默认示例不包含逐机型坐标变体，依靠组件关系推断布局。

1. 导入 v2 设计 JSON，或修改示例的设计约束。
2. 在“布局与设备”选择自动推断、保留构图、上下分布、左右分区、栅格或主视觉加底栏。
3. 在右侧指定语义角色、锚点、绑定组、内容尺寸和自动布局参数。
4. 上传 PNG/JPEG/WebP 素材，并将图片节点的资源路径设为对应文件名。JSON 也可以携带带目录的资源路径与 base64 字节。
5. 添加自定义设备 JSON，预览安全区、横竖屏和折叠姿态。
6. 下载当前设备 XML/HWT 候选，或导出全部设备及其旋转状态。

自动模式中，位置由策略求解；修改构图和设计意图来调整布局。需要自由拖动时切换“手工约束与变体”。不把自动求解的坐标偷偷写成基础设计坐标。

## 代码阅读顺序

```text
src/
├─ App.tsx                    编辑器界面编排
├─ core/
│  ├─ types.ts                设计、节点、设备、资源与响应式策略的数据协议
│  ├─ document.ts             JSON 输入校验与资源路径约束
│  ├─ devices.ts              示例设备、自定义设备合并及旋转
│  ├─ solve.ts                对外唯一布局入口
│  ├─ layout/
│  │  ├─ infer.ts             从设计结构推断组件组、角色、居中和边缘关系
│  │  ├─ planner.ts           可替换的 LayoutPlanner 与内置语义排布
│  │  ├─ adaptive.ts          执行组变换、校验规划结果、处理回退
│  │  ├─ geometry.ts          矩形运算与可用区域划分
│  │  ├─ legacy.ts            显式约束、变体、文字度量与行列自动布局
│  │  └─ types.ts             分组、规划和解释数据
│  └─ validate.ts             越界、重叠、显式尺寸、障碍、文字和触控检查
├─ engine/                    Canvas 绘制与真实素材加载
├─ export/
│  ├─ materialize.ts          把实际求解结果交给编译器，避免第二套布局
│  ├─ xml.ts                  原生坐标锚点和 XML 编译
│  ├─ assets.ts               引用解析与实际素材字节收集
│  ├─ hwt.ts                  包描述、脚本、素材和交付清单
│  ├─ bundle.ts               多设备交付与逐目标失败证据
│  └─ zip.ts                  带 UTF-8 路径和预算限制的 ZIP 写入器
├─ ui/                        画布、图层、检查器、布局面板和下载操作
└─ cli/cli.ts                 无界面的相同流程
```

详见 [响应式架构](docs/RESPONSIVE_ARCHITECTURE.md)、[迁移边界](docs/MIGRATION.md)。原来的 [线性约束设计](docs/ARCHITECTURE.md) 保留为手工模式的历史说明。

## 自动推断如何工作

`Doc + DeviceProfile → 设计参考布局 → 组件组 → 构图策略 → ResolvedScene → 预览 / XML / HWT`

- 顶层设计组作为原子单位。源布局重叠的图片、文字和热区可绑定为同组，避免只移动外框。
- 根据节点类型、父子关系、源边界和显式提示识别角色；不按主题名字写分支。
- 源居中优先保留；边缘组件保持锚点，尺寸与边距使用同一尺度。
- 底部功能区和顶部状态区占用空间后，内容在剩余高度中重排，保留上下间距。
- 原本分离的组件组不能因为重排新增重叠；源叠层、弹层和屏外候场单独处理。
- 显式目标变体优先；新算法必须返回已知组、统一比例且位于可用区域的框。无法满足时保留约束基线并报告，阻止错误 HWT 打包。

这是几何和结构启发式，不是从单张设计图理解所有语义的视觉模型。没有可信语义时可以在检查器补充 `responsive.role/group/position`。

## 自定义设备

在布局面板粘贴，或写入 `Doc.devices`：

```json
{
  "id": "custom-tablet",
  "label": "我的目标设备",
  "short": "2400×1600",
  "formFactor": "custom",
  "width": 2400,
  "height": 1600,
  "widthDp": 960,
  "insets": { "top": 48, "right": 48, "bottom": 48, "left": 48 },
  "posture": "flat",
  "features": []
}
```

`width/height/insets/features.bounds` 是目标像素；`widthDp` 是逻辑宽度。密度由两者计算，不从手机/平板名称推断。双折、三折、宽折都用此协议；内置设备是示例配置，不能当作具体商品的官方参数。

普通柔性折痕不会自动损失像素。只有显式声明 `features[].separating` 或 `occluding` 时，才按中缝/遮挡划分可用区域。

## 命令行

```powershell
npm run cli -- demo --out ..\artifacts\studio-demo-new
npm run cli -- infer design.json --out ..\artifacts\studio-inferred-new
npm run cli -- export design.json --mode bundle --out ..\artifacts\studio-export-new
```

使用新的输出目录，防止混入旧脚本。`infer` 输出自动模式设计和每个设备的推断说明；`export` 和界面共用求解器。

```text
<输出>/
├─ design.json
├─ resolution-report.md
├─ <主题名>-多分辨率.zip
└─ devices/<device-id>/
   ├─ layout.json / issues.json
   ├─ description.xml
   ├─ delivery.json
   └─ unlock/
      ├─ theme.xml
      └─ lockscreen/manifest.xml + 所有引用素材
```

失败目标保留 `needs-review.txt` 与布局、问题清单。浏览器“导出 HWT”生成当前目标包；批量 ZIP 内保存完整的逐设备目录。

## 扩展自己的布局算法

实现 `core/layout/planner.ts` 的 `LayoutPlanner`，通过 `resolveDoc(doc, device, { planner })` 注入。输入是设计、设备和稳定组 ID，输出包含组框、统一缩放、规划原因；`adaptive.ts` 独立验证，插件不能放宽安全区域、删组或覆盖保护组。

后续模型只需要生成此协议，不需要重新解析脚本，也不直接拼接 XML。

## 当前交付边界

- 新编辑器输入是设计 JSON 和静态素材；完整 HWT 导入、动画、连续场景和原脚本保真仍使用 Python 主引擎。没有把二者冒充为同一种已完成的编辑协议。
- 自动构图不能等价压进旧的线性 `sx/sy` 表达式。自动模式交付逐设备结果，停用不等价的“响应式单文件”。手工模式保留该历史入口，其限制会写入注释。
- 文字时钟/日期导出为动态 `DateTime`；位图时钟需要整套字库派生，当前设计打包明确拒绝，避免输出未缩放字库。可用 Python 的媒体与资源族流程处理已有主题。
- 缺少素材、硬性布局错误会阻止 HWT 打包。XML 是候选脚本，`delivery.json` 标记 `nativeDeviceVerified: false`。
- 鸿蒙 5+ 引擎协议迁移、系统行为和原生字体仍需官方工具与设备验收。此处借鉴响应式布局思想，不是把 ArkUI/Android API 当成主题脚本语法。

## 开发约定

```powershell
npm run typecheck
npm run build
npm run format
```

单元测试保留在 `test/`，需要时再运行 `npm test`；本轮没有运行或新增测试用例。

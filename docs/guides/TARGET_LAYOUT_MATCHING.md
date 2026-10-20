# 目标布局图匹配（无需训练）

输入：已转换主题的 `project.json`、一个目标设备/交互状态、对应的完整预期布局图。预期图可以来自 AI，也可以来自设计稿。

输出：组件组对应、约束求解后的布局建议 JSON、原素材重渲染对比、诊断。审查应用后走现有 XML / HWT 反写。此功能没有训练步骤，默认不加载任何 LoRA。

## 预览器使用

```powershell
.\.venv\Scripts\python.exe multires.py serve artifacts/learning/real-two-20261020/projects --port 8783
```

打开一个主题，选择设备、方向、交互状态，找到“目标布局图匹配 · 无需训练”：

1. 上传对应状态的 PNG / JPEG / WebP。需要完整画布，不含设备外框；宽高比与目标画布一致（容差 2.5%），10MB / 2000 万像素以内。
2. 选择匹配方式：
   - **本地 Qwen3-VL**：预训练模型读取带编号的原组件图集和目标图，按语义、外观寻找位置。适合 AI 重绘或外观略有变化的目标图。
   - **模板匹配**：带透明蒙版的多尺度图像相关匹配。适合原素材直接移动、缩放后的图片；纯色或重复图案可能无法匹配。
3. 默认保留居中、边缘关系，并传播到相同几何的组件。目标图有意改变构图时，可以取消“保留居中 / 边缘关系”；等比、安全区、同组变换和无新增重叠仍会检查。
4. 点击“匹配目标布局”。对比目标图与原素材重渲染结果，查看哪些对应被接受、哪些组保持原位。视觉模型置信度是模型自报值，未经标定。
5. “应用到当前预览”修改会话布局；“导出 HWT 与交互记录”保存新的项目和候选包。原项目不会被会话覆盖。也可下载建议 JSON，使用 CLI 持久写回原项目。

服务会从已转换的项目数据生成最新预览 UI，因此不用重新提取主题脚本。已经运行的旧服务需要重启才能加载新 Python 代码。

## 本地模型设置

当前工作区已有 `.venv-ml` 和本地 `artifacts/models/Qwen3-VL-2B-Instruct-sharded/`。业务服务通过独立进程调用该环境，模型使用 4bit 推理；不加载 mock 微调权重，不进行训练，不上传图像。

可在启动服务或 CLI 前设置：

```powershell
$env:MULTIRES_TARGET_PYTHON = 'F:\multi-res\.venv-ml\Scripts\python.exe'
$env:MULTIRES_TARGET_MODEL = 'F:\multi-res\artifacts\models\Qwen3-VL-2B-Instruct-sharded'
$env:MULTIRES_TARGET_TIMEOUT = '600'
$env:MULTIRES_TARGET_MAX_PIXELS = '200704'
```

Linux 服务器可将前两个路径替换为 GPU 环境和更大 Qwen3-VL 模型的绝对路径。模型加载复用 `learning/qwen_runner.py:load` 的推理分支。其他模型可设置可信代码入口 `MULTIRES_TARGET_LOADER=your_module:load`，接口为 `load(args, training=False) -> (model, processor)`，processor 需支持现有多图 chat template。需要不同协议时使用下面的自定义 matcher 接口。

模型默认只读本地权重并设置 `HF_HUB_OFFLINE=1`。缺环境、缺权重、CUDA 错误或超时会明确报错，不会伪造模型匹配或偷偷改用别的结果。每次任务加载一次模型，同一服务进程内 GPU 推理串行，每 9 个组一页。2B 模型对小图标或复杂重绘可能匹配失败；空结果也是合法结果。

## CLI

### 匹配并生成可审查建议

```powershell
.\.venv\Scripts\python.exe multires.py tools target-layout match `
  artifacts/learning/real-two-20261020/projects/diefeng/scenes/project.json `
  --target tablet-portrait --state state-000 --image expected-tablet.png `
  --strategy qwen --out artifacts/target-layout/diefeng-proposal.json
```

不使用视觉模型时，替换为 `--strategy template`。可选参数：

- `--minimum-confidence 0.78`
- `--free-placement`：取消自动保留居中、贴边；组内布局仍整体等比变换。
- `--apply-scope state`：仅当前状态；默认 `matching-components` 检查并传播相同几何组件。
- `--apply`：生成建议后立即经安全校验持久写回；默认只产生建议和对比文件。

每次匹配在 `project.json` 同级的 `target-matching/<任务ID>/` 保存：

| 文件 | 含义 |
|---|---|
| `catalog.json` | 当前组件组与原脚本 ID、语义、约束、逻辑几何 |
| `atlas-*.png` | 带组别名的原素材图集 |
| `expected.png` | 输入的完整目标图 |
| `matches.json` | 匹配器原始结果 |
| `proposal.json` | 可直接导入现有微调流程的最终建议 |
| `before.png` / `after.png` | 匹配前 / 求解后的原素材渲染 |
| `report.html` | 三图对比、目标框偏差和未匹配原因 |
| `vlm-*` / `vlm.log` | 仅模型模式：请求、原始回复与本地运行日志 |

这些产物用于追溯，不会自动删除；批量使用时由调用方按任务 ID 管理保留周期。

### 持久写回

```powershell
.\.venv\Scripts\python.exe multires.py tools target-layout apply `
  artifacts/learning/real-two-20261020/projects/diefeng/scenes/project.json `
  --proposal artifacts/target-layout/diefeng-proposal.json
```

复用现有事务式写回：摘要过期拒绝应用，保存 `project.layout-backup-*.json`，生成 `scripts/<设备ID>.xml`、补丁/资源清单和新预览。需要 HWT 时再运行 `multires.py export ... --target ... --out ...hwt`。原生导出中无法表达的状态条件仍由现有导出器报错。

## 可插拔接口

`src/multires_app/preview/target_layout.py` 定义 `TargetMatcher`、`register_matcher`、`resolve_matches`。匹配器和几何求解分离，替换模型不需要重新提取脚本。

### 自定义 Python 匹配器

```python
def match(context):
    # context['catalog']: 组件组与别名 g0/g1/...
    # context['reference']: PIL RGB 目标图
    # context['templates']: alias -> PIL RGBA 原组件组图像
    # context['job_directory']: 本次任务目录
    return {
        'schema': 'target-layout-matches/1',
        'matches': [{'id': 'g0', 'bbox': [100, 200, 300, 100], 'confidence': 0.9}],
        'unmatched': [{'id': 'g1', 'reason': '目标图中未找到'}],
        'provider': 'my-matcher',
    }
```

`bbox` 为**目标图整个画布归一化到 0..1000 的 `[左, 上, 宽, 高]`**，包围组件组的可见画面。它不是原脚本组件的热区范围，不是 `[x1,y1,x2,y2]`，也不是图集里的卡片范围。示例别名和数字必须替换为真实目录中的值。模型不返回 XML、资源路径或脚本。

```powershell
.\.venv\Scripts\python.exe multires.py tools target-layout match project.json `
  --target tablet-portrait --image expected.png --strategy custom `
  --matcher my_package.matcher:match --out proposal.json
```

自定义入口仅由本地可信操作员通过 CLI 或 Python 注册。网页参数无法导入任意模块。

### 外部服务或人工标框

```powershell
.\.venv\Scripts\python.exe multires.py tools target-layout catalog project.json `
  --target tablet-portrait --out artifacts/target-catalog
# 将 catalog 与 atlas 交给自己的匹配服务，得到 matches.json 后：
.\.venv\Scripts\python.exe multires.py tools target-layout resolve project.json `
  --target tablet-portrait --catalog artifacts/target-catalog/catalog.json `
  --matches matches.json --out proposal.json
```

`resolve` 独立进行协议、身份、摘要、比例、安全区和跨状态校验；不再调用视觉模型。未返回、低置信度或约束不相容的组保持原位。全部失败时返回空建议与原因。

## 求解逻辑与边界

1. 从当前布局计划和显式 `layout.group` 合并组，建立组别名到原组件/脚本节点的对应。数字、冒号、日期等已有组内相对关系整体保留。
2. 只用当前可见相位及媒体首帧参考生成图集。背景、全屏手势层、当前屏外或裁切组件，以及资源不完整的组冻结。显式 `required` 自定义约束、`required` 锚点或固定尺寸引用的组在此 MVP 中整体冻结，保留其已有解，不静默放松约束。不会把交互后才出现的资源提前显现。
3. 匹配器提出可见画面框；求解器使用每组的平移、统一缩放，将内部图片、文本、关联按钮热区同步变换。原长宽比与目标框不一致时等比容纳，报告实际框。
4. Kiwi 约束求解将目标位置作为软目标，安全区和无新增重叠作为硬条件，可选保留现有锚点计划中的居中/贴边关系。已有有意重叠保留；不会把组内部自动改成另一种排列。
5. 对原本不重叠的不同组成员建立分离约束；期望框能分离时采用期望方向，否则采用原布局方向。此 MVP 不穷举所有碰撞方向。若无解或传播状态校验失败，按置信度从低到高冻结候选组再求解，逐项记录退回原因。
6. 记录期望与实际中心偏移、框 IoU；偏差大时提示检查目标图和约束。固定像素、原生图形、缺失文件和复杂特效可能无法离线重现，渲染报告明确标记；动画时间线、交互变量和资源身份沿用原脚本。

目标图可以表达原资源的重新布局。它新增的组件、完全重绘后的未知形状、组内重新排列、缺失的真实资源和未探索交互状态不能从一张图可靠反推出脚本；这类情况返回未匹配或保留原布局，需要补充该状态或人工修改组关系。

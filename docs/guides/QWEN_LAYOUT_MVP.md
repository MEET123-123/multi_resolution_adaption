# Qwen3-VL 布局微调 MVP

## 先看什么

- 标注页面：`artifacts/learning/mock-qwen/dataset/review.html`，批量版本使用下面的 `serve`。
- 输入：源渲染图 + `dataset/layouts/*-input.json`。
- 输出：`{"schema":"multires-layout/1","groups":{"g0":[x,y,w,h]}}`。
- 三层原始标签：`dataset/annotations/*.json`。
- 本机训练和对照结果：见 [实验记录](../reports/QWEN_MVP_RESULTS.md)。
- 本次运行总览：`artifacts/learning/mock-qwen/summary.html`。

框值均为 xywh。输出相对目标安全区域归一化到 1000；源元素相对源画布归一化。模型提案通过原有约束求解器转为组件变换，再执行原有 XML 写回审计。

## 本机环境

项目业务环境仍为 `.venv`，训练使用独立 `.venv-ml`。脚本 `scripts/setup_qwen.ps1` 可重建核心依赖。实际安装版本及显卡检查在 `artifacts/learning/environment.json`。

底座为官方 `Qwen/Qwen3-VL-2B-Instruct`。本机大权重映射遇到 Windows 原生异常，使用 `learning.shard_model` 无损复制为 6 个 safetensors 分片后加载。原始官方快照和哈希清单保留，不改变权重精度。

官方参考：[Qwen 模型卡](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct)、[Qwen 微调框架](https://github.com/QwenLM/Qwen3-VL/tree/main/qwen-vl-finetune)、[bitsandbytes 平台说明](https://huggingface.co/docs/bitsandbytes/main/en/installation)。本项目用轻量 PyTorch/PEFT 训练入口，业务代码无需安装训练依赖。

## 一、生成 mock 数据

在项目根目录运行，输出目录应为新目录：

```powershell
.venv\Scripts\python.exe multires.py tools layout-lab mock --out artifacts/learning/my-mock
.venv\Scripts\python.exe multires.py tools layout-lab export-sft --dataset artifacts/learning/my-mock/dataset --out artifacts/learning/my-mock/sft --include-synthetic
```

默认 6 套模拟主题，包含时间、日期、天气及底部快捷组件，生成平板横竖屏。输入 JSON 带有可追溯的坐标扰动，模型目标为确定性布局参考。原始绘图、人工覆盖、合成参考三者分开。mock 结果不能作为真实主题的投入收益结论。

## 二、运行本机微调

```powershell
$env:PYTHONPATH=(Resolve-Path src).Path
$env:HF_HUB_OFFLINE='1'
.venv-ml\Scripts\python.exe -m multires_app.learning.qwen_runner train --model artifacts/models/Qwen3-VL-2B-Instruct-sharded --sft artifacts/learning/mock-qwen/sft --out artifacts/learning/my-adapter --max-steps 12 --max-pixels 50176 --max-length 8192
```

仅优化语言模型的 LoRA 注意力投影，冻结视觉编码器；4 位加载、梯度检查点、微批次 1。只计算助手输出的损失，前缀及图像 token 不作为监督目标。不截断答案：样本超出 token 预算会明确报错。

训练记录包括 loss、token 数、有效批次、显存峰值和被更新的适配器张量数量。停止或崩溃不会被报告为训练完成。

## 三、推理与布局执行

```powershell
$env:PYTHONPATH=(Resolve-Path src).Path
.venv-ml\Scripts\python.exe -m multires_app.learning.qwen_runner infer --model artifacts/models/Qwen3-VL-2B-Instruct-sharded --adapter artifacts/learning/mock-qwen/adapter --dataset artifacts/learning/mock-qwen/dataset --split test --limit 2 --max-new-tokens 512 --max-pixels 50176 --max-length 8192 --out artifacts/learning/my-predictions.json
.venv\Scripts\python.exe multires.py tools layout-lab benchmark --dataset artifacts/learning/mock-qwen/dataset --predictions artifacts/learning/my-predictions.json --out artifacts/learning/my-experiment --split test --export
```

去掉 `--adapter` 得到未微调底座的同批对照。`benchmark` 保留源/基线/候选图片、原始提案、模型匹配率、约束投影误差、独立评测与写回结果。JSON 不合法或未匹配时回退原算法，并单独计数。

单文件入口也可用：

```text
qwen_runner infer --model <model> --adapter <adapter> --layout-json <sample-input.json> --image <source.png> --out <modified-layout.json>
```

合法时写出修改后的布局 JSON；非法输出只留下诊断文件，不冒充有效布局。外部模型服务可用 `layout-lab predict --endpoint ... --model ...` 接入。更换架构可用 `qwen_runner --loader module:factory`；加载器返回模型和处理器。

## 四、持续标注与批量 API

```powershell
.venv\Scripts\python.exe multires.py tools layout-lab serve --dataset artifacts/learning/mock-qwen/dataset --port 8767
```

浏览器打开 `http://127.0.0.1:8767/`。支持源元素/组件框、语义筛选、节点定位、候选留白、目标组框拖动和多层语义覆盖。拖动标框不会即时重绘素材；实际几何变化在模型/求解器对照中重新渲染。

| 接口 | 内容 |
|---|---|
| `GET /api/samples?offset=0&limit=50&split=train&pending=1` | 分页读取待标注缓存 |
| `GET /api/sample/<id>` | 完整输入、已有标注和 `baseRevision` |
| `POST /api/reviews` | 一次写入 1–500 条，追加日志，检查版本冲突 |

POST 需 `X-Multires-Review: 1`，正文为 `schema`、`reviewer`、`reviews`。每条包含 `id`、`requestKey`、`baseRevision`、`status`、`answer`，可带 `annotationEdits`：`elementSemantics`、`groupSemantics`、`sceneIntent`。未知 ID、过期版本和非法几何不会直接写入。原始属性、资源和状态机不受标注覆盖。

文件标注也可 `import-reviews --dataset ... --reviews layout-reviews.json`，然后重新导出 SFT，无需重新解析脚本。

## 五、真实双形态数据

使用 `layout-lab build` 从已有主题项目建立缓存；配对入口为 `suggest-native-pairs` / `import-native-pairs`。这些操作仅消费缓存。已有截图与目标 JSON 的格式示例见 `configs/layout-pairs.example.json`。

原生两种形态对应关系必须确认。组件拆分/合并或无法匹配时保留映射报告，不用距离相近替代身份。默认导出拒绝未审核数据和不完整的离线图像。

## 六、迁移到双卡 32GB

Linux CUDA 环境安装兼容 GPU 的 PyTorch 及 `requirements-ml.txt`，复制缓存图片与 SFT。使用 `scripts/train_layout_server.sh`，可通过 `MODEL_PATH`、`SFT_PATH`、`ADAPTER_PATH`、`GPU_COUNT` 改模型和路径。

如实际是 V100，使用 `--precision fp16` 或默认 `auto`，不启用 FlashAttention/BF16 假设。双卡使用 DDP 分布式采样与梯度同步，当前 Windows 单卡验证不能代替服务器的多卡验收。更大模型需按每卡显存选择量化、token 和图像预算。

## 下一次投入判断

先导入少量人工确认的双形态配对，固定按主题划分的测试集，再比较原算法、底座、LoRA 和约束后结果。保留 JSON 合法率、目标坐标误差、边界/居中/贴边/公共组件违规数、写回通过率及盲评记录。mock 的 loss 或坐标误差改善不能说明真实设计意图已学会。

## 真实主题运行总览

已按约定目录保存 `inputs.json`、`prepare-result.json`、`dataset/` 和模型实验结果时，可生成逐场景流程页：

```powershell
.venv\Scripts\python.exe multires.py tools layout-lab report-run --run artifacts/learning/real-two-20261020
```

输出为运行目录的 `index.html`，包含原包覆盖范围、三层标签入口、模型输入/输出、原始/基线/模型/约束后图片、审核状态和问题文件。推理尚未结束时标为进行中；全部输出后重新执行即可刷新。标注与交互服务地址可通过 `--annotation-url` 和 `--interaction-url` 指定。

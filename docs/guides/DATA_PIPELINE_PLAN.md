# 数据与模型流水线

## 新增模块

`src/multires_app/learning/`：

| 模块 | 作用 |
|---|---|
| `contract.py` | 唯一模型 JSON 协议、稳定请求指纹、严格输出检查 |
| `dataset.py` | 从已有项目缓存建立样本、分主题切分、标注覆盖、SFT 导出 |
| `annotations.py` / `space.py` | 三层标注、变量依赖语义、脚本/可见框、留白候选 |
| `quality.py` | 缺失原因、A/B/C/D 质量分级和可训练率 |
| `catalog.py` / `annotation_server.py` | JSONL 偏移索引、分页 API、批量标注、版本冲突检查 |
| `review.html` | 元素/组框、语义筛选、定位节点、拖动组框和独立覆盖文件 |
| `pairs.py` | 两个缓存数据集的原生形态对应与配对导入 |
| `algorithm.py` | 记录基线请求；模型组框接入现有求解器 |
| `experiment.py` | 本地服务/预测文件、候选对照、截图和导出门禁 |
| `mock_data.py` | 不读取真实主题的合成数据生产 |
| `qwen_runner.py` | 独立 GPU 环境中的 QLoRA、推理、双卡入口及模型加载扩展 |
| `download_model.py` / `shard_model.py` | 官方快照下载校验、无损权重分片 |

## 缓存结构

```text
artifacts/learning/mock-qwen/
  raw/                       生成的 mock 源文件
  projects/                  现有解析器生成的项目缓存
  dataset/
    samples.jsonl            自动样本，图像/JSON 使用相对路径
    manifest.json            版本、源哈希、构建配置与检查点
    split-manifest.json      固定主题切分
    labels/                  一次缓存的脚本模型和交互信息
    annotations/             元素、语义、组合层原始标注
    layouts/                 可独立用于推理的输入 JSON
    images/                  源图及当前目标图
    references/              后续人工目标参考
    reviews.jsonl            追加式人工覆盖，含作者及版本
    annotation-index.sqlite3 分页索引，可以重建
    quality/                 质量分级和问题列表
  sft/                       train / validation / test + 样本追溯索引
  adapter/                   LoRA 参数与训练记录
```

与实施指南中的 `raw/annotations/paired/quality/sft/manifests` 职责对应；生成数据放在现有忽略的 `artifacts/` 下，避免把大文件加入代码仓库。

## 后续工作无需重新提取脚本

初次 `build` 从已有 `project.json` 读取解析模型并求解需要的设备场景，生成持久缓存。之后分页标注、批量写入、语义覆盖、两种形态配对、质量检查、SFT 导出和更换底座均读取缓存。

训练只需要 SFT 文件和所引用的图片。迁移服务器时复制数据目录，并用 `export-sft --image-root /srv/theme-data/dataset` 重定向图片路径。原始项目绝对路径只是本机复现/写回的来源定位，不是模型训练依赖。

初次批量构建逐样本落盘、逐主题保存检查点；同配置 `build --resume` 会核对已完成项目哈希后跳过。原始内容、切分或构建配置改变，应创建新数据集版本。

## 从 mock 切到真实主题

1. 对原始形态和人工适配形态各建立一次缓存。
2. 用 `suggest-native-pairs` 列出候选场景和源组；人工确认场景及组对应，不能只按坐标匹配。
3. `import-native-pairs` 从目标原生脚本的缓存框获取监督目标，支持多个目标组对应一个源组；歧义和重复对应会拒绝。
4. 已有真实截图及目标 JSON 时，用 `import-pairs` 导入。目标参考图仅用于审核，不会泄漏到模型输入。
5. 默认 SFT 仅接收人工批准的数据。mock 需显式 `--include-synthetic`；未审核算法结果需显式 `--include-weak`，不应作为正式真实标签。
6. 冻结测试主题，分别运行原算法、未微调模型、微调模型和约束后的候选。

模型升级只改模型路径/加载插件及训练参数。布局协议和缓存标注保持稳定。双卡 DDP 每卡持有一份模型，不把两张 32GB 自动当成一张 64GB；更大模型的分片训练可通过加载/训练后端扩展。

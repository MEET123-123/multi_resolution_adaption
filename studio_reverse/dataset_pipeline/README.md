# 10,000 条合成布局配对数据（本地版）

该目录通过 `generate.py` 在本机生成数据，默认输出到 `F:\\multi-res\\artifacts\\learning\\synthetic-10k-v1\\`。

- `themes/`：64 个原创 HWT-like ZIP/XML/PNG 结构样例；非官方真机认证主题。
- `images/`：64 张原创源图。
- `dataset/pairs.jsonl`：10,000 条源布局/目标布局规则配对，7 个语义组，6 个设备目标。
- `sft/{train,validation,test}.jsonl`：可用于现有 Qwen3-VL 训练入口的对话+图像监督格式。
- `dataset/manifest.json`：配额、监督来源、数据集切分统计。

**重要：**这 10,000 条是合成弱监督，不是 10,000 个真实设计师跨设备答案。384 个主题×设备的规则目标，共生成 10,000 条坐标扰动变体。源包素材完全本地生成，未从网络下载第三方主题。

从项目根目录执行：

```powershell
.\\.venv\\Scripts\\python.exe studio_reverse\\dataset_pipeline\\generate.py --out artifacts\\learning\\synthetic-10k-v1 --themes 64 --count 10000
.\\.venv\\Scripts\\python.exe studio_reverse\\dataset_pipeline\\audit.py artifacts\\learning\\synthetic-10k-v1
```

此生成器不会覆盖已有目录。迁移数据集到别的路径后，SFT `images` 字段的绝对路径需要重设。

模型微调入口沿用 `src/multires_app/learning/qwen_runner.py`；此处只负责数据生成与验证。

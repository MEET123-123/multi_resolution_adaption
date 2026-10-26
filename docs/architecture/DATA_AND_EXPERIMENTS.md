# 数据与实验目录：训练标签不是一回事

```text
主题源包 / 授权素材
  → 交互状态图（每个状态有进入路径）
  → 多设备布局和待验收 HWT
  → XML Runtime 回放与浏览器渲染
  → 质量门禁（脚本、组件、资源、布局）
      ├── 通过 → 训练候选 SFT（必须标明来源）
      └── 失败 → Badcase / 回归报告
  → Qwen3-VL 训练 → 保留主题的离线测试
```

| 当前目录 | 作用 | 特别注意 |
|---|---|---|
| `theme/*.hwt` | 真实来源主题 | 保留原包及来源校验值 |
| `theme/adaptation/*/scenes/` | 场景、布局、资源及输出脚本 | 现有引用大量使用旧路径 |
| `new_adaption/` | 其他历史适配结果 | 暂不迁移 |
| `artifacts/learning/` | 数据集、模型、权重和各轮实验输出 | 不等于“全是训练真值” |
| `studio_reverse/dataset_pipeline/` | 程序合成的静态布局配对 | 主要是规则弱监督 |
| `studio_reverse/web_theme_pipeline/` | 公共许可素材、多交互状态生成与回放 | 不同模板和同一背景需区分 |
| `eval/`、`artifacts/evaluation/` | 评分、Badcase、回归证据 | 不自动删除 |
| `preview-live/` | 本地预览会话 | 可能有仍在引用的文件 |

## 新数据集的 manifest 最低字段

`datasetId`、`sourceThemeId`、`sourceLicense`、`stateId`、`interactionTrace`、`targetDevice`、`labelProvenance`、`qaStatus`、`nativeValidated`、`split`。

标签类型区分：

- `human-reviewed`：人工确认的目标布局。
- `rule-generated`：程序自动生成的目标，**弱监督**。
- `model-generated`：模型预测，必须另行评分。
- `runtime-verified`：在项目 XML 解释器验证过，并非华为真机认证。
- `native-verified`：经过原生引擎/真机验收。

按**来源主题或交互结构家族**划分 train/val/test，避免同一模板的近重复状态跨集合泄漏。新实验应独立命名输出目录和保留 manifest，不覆盖历史 SFT。

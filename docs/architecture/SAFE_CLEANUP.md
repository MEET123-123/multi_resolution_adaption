# 安全整理计划与回滚

## 已有整理不要重复做

项目过去已经把 65 个根目录 Python 模块迁移到 `src/multires_app/`；参见 `docs/ORGANIZATION.md` 和 `artifacts/organization/migration-manifest.json`。现在的主要混乱来自后来新增的浏览器实验、交互生成、数据与评测产物，**不是要再次大规模改包结构**。

## 分阶段实施

**P0：当前阶段（低风险）**

- 增加架构导航、开发命令、实验目录说明。
- 新增只读清单脚本，报告静态绝对导入关系与重点目录一级清单。
- 保留原有 `README.md` 的未提交修改和所有项目文件。
- 运行现有 CLI/核心测试，确认没有破坏原有命令。

**P1：产物规范**

- 对新数据集要求独立 run-id、manifest、输入来源、生成参数、训练/测试划分和评测证据。
- 对 `studio_reverse/` 的三个实验模块明确负责人、输入、输出和原生引擎限制。
- 不迁移旧 HWT 或模型权重，以防 XML 或训练 JSONL 内引用失效。

**P2：模块整理（将来验证后）**

- 逐项识别复制实现、弃用脚本、相对路径和重复概念。
- 一次只移动一组职责明确的文件，维护兼容入口，跑对应测试和真实主题 smoke。
- 迁移清单记录旧路径、新路径、修改的 import 和回滚方法。

## 不能自动清理

- `theme/`、`new_adaption/`、`artifacts/models/`、`artifacts/learning/`、`eval/`。
- `preview-live/`、`tests/tmp*` 等若有外部引用或当前任务产物的目录。
- `README.md`、`studio_reverse/` 任何用户未提交修改。
- 真实主题、模型权重、已标注数据以及评测基线。

## 回滚

此次 P0 只新增文件并在根 README 添加一条导航链接，不移动或删除数据。可通过 `git diff` 检查更改；撤销时仅撤销本轮新文件和本轮 README 链接，不执行 `git reset --hard` 或 `git clean -fd`。

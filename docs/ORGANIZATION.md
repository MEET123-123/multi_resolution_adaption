# 目录整理说明

## 本次整理

- 65 个根目录 Python 模块移动到 `src/multires_app/`，按职责分组，并更新项目内导入。
- 页面 JS/HTML 与对应的预览、服务模块放在一起。
- 设备示例配置放到 `configs/`。
- 设计和使用文档放到 `docs/guides/`，修复验证报告放到 `docs/reports/`，Word 原稿与提取文本放到 `docs/reference/`。
- 历史日志放到 `artifacts/logs/history/`，测试摘要放到 `artifacts/reports/`，ZIP/RAR 快照放到 `archives/`。
- 根目录保留 `multires.py`；维护命令统一通过 `multires.py tools` 调用。
- 新增 `pyproject.toml`，支持可编辑安装。

## 保留位置

`theme/` 中的原始 HWT、适配结果、项目缓存、资源、备份和预览链接保留原路径。`samples/`、`tests/`、`third_party/` 和虚拟环境也保留目录位置。

生成项目记录了源包和资源绝对路径。本次不批量改写这些大型数据，也不把旧验证证据标成新测试结果。历史报告中的代码位置可结合迁移记录查找。

## 查找旧文件

完整一对一记录见 [迁移记录](../artifacts/organization/migration-manifest.json)：

- `moves`：旧文件到新文件的路径。
- `moduleAliases`：旧模块名到新模块名。
- `preservedDirectories`：保留目录。

整理前文本源码、测试、配置、文档和工作流的快照在 `artifacts/organization/before-organization.zip`，不包含主题素材或整个虚拟环境。

## 后续文件放哪里

| 新增内容 | 位置 |
|---|---|
| 布局规则与求解逻辑 | `src/multires_app/layout/` |
| 脚本、命令与交互解释 | `engine/`、`interaction/` |
| 新设备配置 | `configs/` |
| 自定义算法试验 | `samples/` |
| 主题原包和转换结果 | `theme/`，结果放在 `theme/adaptation/` |
| 运行日志 | `artifacts/logs/` |
| 测试摘要与整理证据 | `artifacts/reports/`、`artifacts/organization/` |
| 使用说明与技术设计 | `docs/guides/` |
| 修复结论与验证报告 | `docs/reports/` |

本次检查导入、入口、静态路径与文档链接，不代表重新执行原有测试套件或真机验证。

# 快速使用

本文所有命令均在 `F:\multi-res` 根目录执行。

## 环境准备

当前 `.venv` 已完成本项目的可编辑安装。换一台机器时：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[service]"
```

只使用本地转换可安装 `-e .`。已有锁定依赖清单保留在根目录的 `requirements-core.txt` 和 `requirements-service.txt`。

## 查看已有结果

```powershell
.\.venv\Scripts\python.exe .\multires.py serve .\theme\adaptation --port 8766
```

打开 <http://127.0.0.1:8766/>。进入主题后选择设备、方向和场景，两侧分别运行原始与适配后的交互。

| 每个主题中的文件或目录 | 作用 |
|---|---|
| `scenes/preview.html` | 对比预览，实时交互请通过本地服务打开 |
| `scenes/project.json` | 项目模型、场景、各设备布局与变换 |
| `scenes/scene-graph.json` | 已探索的交互状态与转换，部分旧缓存没有单独文件 |
| `scenes/anchor-layout.json` | 锚点、分组和求解依据 |
| `scenes/scripts/` | 各设备 XML、资源映射及反写审计 |
| `scenes/packages/` | 已生成的 HWT；现有批量流程通常打包代表设备 |
| `scenes/assets/` | 提取出的预览资源 |
| `scenes/report.json` | 转换摘要、覆盖范围和导出记录 |
| `scenes/*validation.json` | 专项验证证据，不等于真机认证 |

## 转换一个主题

以已有源画布配置为例，输出到独立的新目录：

```powershell
.\.venv\Scripts\python.exe .\multires.py scenes ".\theme\半岛日记改.hwt" `
  --source-profile .\theme\adaptation\theme-44cf4a6ae4\source-profile.json `
  --profiles .\configs\device-profiles.example.json `
  --algorithm anchor `
  --max-states 96 --max-depth 8 `
  --out .\theme\adaptation\my-new-theme\scenes
```

源画布配置应与自己的主题匹配。部分平板、双折叠配置是 mock，需确认真实设备尺寸和有效画布。归档安全检查可能拒绝过大的主题包，需先分析拒绝原因。

`scenes` 生成预览及各设备脚本；生成 HWT 使用 `export` 命令，参数可通过以下入口查看：

```powershell
.\.venv\Scripts\python.exe .\multires.py export --help
```

## 维护已有项目

```powershell
# 重新求解指定主题，不重新探索交互
.\.venv\Scripts\python.exe .\multires.py tools iterate-anchor-layout `
  --only theme-44cf4a6ae4 --layout-only --skip-repack

# 重新打包指定主题
.\.venv\Scripts\python.exe .\multires.py tools repack-layouts `
  --only theme-44cf4a6ae4 --workers 1

# 批量转换参数
.\.venv\Scripts\python.exe .\multires.py tools batch-convert --help
```

维护工具保留原流程和参数。`--layout-only` 不会重新探索交互；修改解释器或交互语义后，可用 `python multires.py tools refresh-interactions --only <项目目录名>` 重新探索并生成布局。重建保留旧输出，完整步骤见 [交互动画规则](guides/INTERACTION_ANIMATION.md)。

## HarmonyOS 5.0 及以上主题

先阅读 [规范与迁移方案](guides/HARMONYOS5_MIGRATION.md) 和 [逐文件改造清单](guides/HARMONYOS5_IMPLEMENTATION_PLAN.md)。当前 `analyze --engine harmonyos-5+` 用于标注分析目标，现有 `scenes` / `export` 生成原包语法下的适配候选；它们尚未实现完整的 5+ 引擎转换和官方工具出包。

文档中的新适配器、目标配置和交付模式属于待实现设计。正式目标需要分别核实规范版本、设备屏幕角色、安全区及官方工程模板，已有 Mock 预览配置不能直接作为原生交付配置。

## 在线服务

```powershell
.\.venv\Scripts\python.exe .\multires.py tools saas-store create-client `
  --root .\.saas-data --name "内测客户"

.\.venv\Scripts\python.exe -m uvicorn multires_app.service.saas_api:create_app `
  --factory --host 127.0.0.1 --port 8787
```

完整环境变量、客户密钥和隔离规则见 [服务说明](guides/SAAS_READINESS.md)。

## 验证入口

按需执行；本次目录整理未重新转换真实主题，也未运行测试套件：

```powershell
.\.venv\Scripts\python.exe tests/run_quality_checks.py
```

结果写入 `artifacts/reports/quality-results.json`。历史日志在 `artifacts/logs/history/`；真实主题几何与媒体报告仍保留在 `theme/adaptation/`。

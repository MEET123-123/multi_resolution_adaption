# 鸿蒙锁屏主题多分辨率适配

读取主题脚本，理解交互状态与组件关系，为直板机、平板、折叠屏等设备计算布局，并生成对比预览、XML 和 HWT 候选包。

**先看 [项目结构与模块说明](docs/PROJECT_STRUCTURE.md)，再看 [快速使用](docs/QUICKSTART.md)。**

## 目录怎么找

```text
multi-res/
├─ multires.py           统一启动入口，原来的命令继续可用
├─ pyproject.toml        Python 项目与安装配置
├─ configs/              设备分辨率与适配配置
├─ src/multires_app/     项目源码，按职责分组
│  ├─ cli/              命令行参数与分发
│  ├─ engine/           XML、表达式、动画解释与能力检查
│  ├─ interaction/      手势、状态探索、组件绑定、操作历史
│  ├─ pipeline/         转换流程、场景聚类、数据保存
│  ├─ layout/           锚点、约束求解、缩放、间距、美学评价
│  ├─ media/            序列帧、视频与资源缩放
│  ├─ preview/          原始/适配预览、交互服务与页面资源
│  ├─ export/           XML 精确反写
│  ├─ evaluation/       逐场景评价、证据、Badcase、候选门禁与固定回归
│  ├─ service/          在线服务、客户隔离、任务队列
│  └─ tools/            批量转换、重新布局、打包与维护
├─ theme/               真实主题、已有适配结果与专题样例
├─ samples/             Mock 主题、自定义算法和小型演示
├─ tests/               自动化测试、真实主题专项验证脚本
├─ docs/                使用说明、设计资料和修复报告
├─ artifacts/           日志、验证证据与目录整理记录
├─ archives/            历史 ZIP/RAR 备份
└─ third_party/         已引入的第三方代码
```

## 常用入口

在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe .\multires.py --help
.\.venv\Scripts\python.exe .\multires.py serve .\theme\adaptation --port 8766
.\.venv\Scripts\python.exe .\multires.py tools --help
```

启动服务后打开 [主题预览索引](http://127.0.0.1:8766/)。完整环境准备、单包转换、导出与后台服务命令见 [快速使用](docs/QUICKSTART.md)。

## 你最关心的几个位置

| 要做什么 | 看哪里 |
|---|---|
| 加入自己的布局算法 | [算法接入说明](docs/CUSTOM_LAYOUT.md) |
| 理解交互如何变成布局 | [项目结构与处理流程](docs/PROJECT_STRUCTURE.md) |
| 规划鸿蒙 5.0 及以上主题适配 | [规范与迁移方案](docs/guides/HARMONYOS5_MIGRATION.md)、[逐文件改造清单](docs/guides/HARMONYOS5_IMPLEMENTATION_PLAN.md) |
| 修复动画提前出现、连续跳转和时间样式状态 | [通用交互动画规则](docs/guides/INTERACTION_ANIMATION.md) |
| 修改设备与分辨率 | [设备配置](configs/device-profiles.example.json) |
| 找适配后的 XML 和 HWT | `theme/adaptation/<主题编号>/scenes/scripts/`、`packages/` |
| 看半岛日记最新修复 | [屏外面板与交互修复](docs/reports/BANDAO_DRAWER_RESULTS.md) |
| 看所有设计与使用文档 | [文档导航](docs/README.md) |
| 做批量评测与 badcase 归因 | [批量评测与反向优化](docs/guides/EVAL_AND_BADCASE.md)、`multires.py tools eval-batch` |
| 接通评测、人工反馈和候选回归闭环 | [可插拔反馈引擎](docs/guides/FEEDBACK_LOOP.md)、`multires.py tools feedback-loop` |
| 用 mock 验证 Qwen3-VL 布局微调与批量标注 | [模型 MVP](docs/guides/QWEN_LAYOUT_MVP.md)、`multires.py tools layout-lab` |
| 看在线服务及当前限制 | [服务说明](docs/guides/SAAS_READINESS.md) |
| 找目录整理前的文件 | [目录整理与迁移记录说明](docs/ORGANIZATION.md) |

## 当前能力边界

- 默认布局算法为 `anchor`，自定义算法接口保留。
- 浏览器预览和离线校验用于迭代；输出是待验收的适配候选，不能替代鸿蒙原生引擎和真机验证。
- 鸿蒙 5.0 及以上引擎迁移目前只有初步能力检查，尚未实现完整的命令、系统数据与主题包规范转换。已整理 [官方规范依据](docs/reference/HARMONYOS5_SPEC_SOURCES.md) 与 [分阶段实施文档](docs/guides/HARMONYOS5_IMPLEMENTATION_PLAN.md)，其中拟议接口和配置仍待实现。
- 在线服务目前为受控内测，具体限制见服务文档。

目录整理保留了 `theme/` 下原始主题和生成结果的位置，已有预览链接与源包记录可以继续使用。详细搬迁规则见 [目录整理说明](docs/ORGANIZATION.md)。

远程仓库保存源码、测试、配置、文档及小型 Mock 输入；真实 HWT、提取素材、生成结果、历史日志和压缩备份保留在本地。克隆后使用自己的主题包生成结果，见 [远程仓库内容说明](docs/REPOSITORY.md)。

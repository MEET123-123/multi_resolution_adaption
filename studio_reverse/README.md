# Theme Studio / 浏览器操作与布局数据实验区

这里是**与主代码分离的实验工作区**；主业务解析器仍在 `src/multires_app/`。不要将网页仿真器当作华为官方动态锁屏运行时。

| 路径 | 实验用途 | 当前限制 |
|---|---|---|
| `server.py`、`index.html`、`app.js` | 读取 HWT 的多设备预览实验台（8796） | 同源布局等比缩放/拉伸，不等同官方响应式设计 |
| `browser_automation/` | Playwright 浏览器自动化（8797） | 用户自己完成登录；网络记录需要脱敏 |
| `dataset_pipeline/` | 静态合成主题、10k 坐标扰动配对、校验 | 规则弱监督，非人工真值 |
| `web_theme_pipeline/` | Commons 素材收集、多交互模板、适配与质量门禁 | 必须单独记录交互验收、动画能力与真机限制 |

## 统一实验工具入口

`manage.py` 是新增的非侵入式分发器，兼容原有单脚本运行方式，不改变已有源码路径。

```powershell
.\.venv\Scripts\python.exe studio_reverse\manage.py list
.\.venv\Scripts\python.exe studio_reverse\manage.py doctor
.\.venv\Scripts\python.exe studio_reverse\manage.py families --help
.\.venv\Scripts\python.exe studio_reverse\manage.py artifacts-index
```

详细交互脚本归类参阅 [web_theme_pipeline/README.md](web_theme_pipeline/README.md)，历史函数名或相似文件不应直接删除。

## 如何运行

在仓库根目录：

```powershell
.\.venv\Scripts\python.exe studio_reverse\server.py --port 8796
.\.venv\Scripts\python.exe studio_reverse\browser_automation\server.py --port 8797
```

数据生成请阅读各子目录的 `README.md` 及 `web_theme_pipeline/README_RUNTIME_QA.md`。默认生成目录位于 `artifacts/learning/`，禁止覆写以前已成功的主题和模型产物。

**模块约定**：实验性 Python 脚本可以引用 `src/multires_app/` 的 Runtime 和质量检查；相同 XML 语义不要在实验目录重新实现一套。需要进入主包的稳定功能，先写测试与接口说明，再经独立迁移。

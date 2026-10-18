# 远程仓库内容说明

仓库：<https://github.com/MEET123-123/multi_resolution_adaption>

## 已纳入版本管理

- `src/`、根目录启动器与项目安装配置。
- `tests/`、持续集成工作流。
- `configs/`、设计说明、修复报告、Word 设计原稿与提取文本。
- `samples/` 下的示例脚本、配置和三个小型 Mock 输入。
- `theme/tablet-2560x1600/` 中的构建脚本、布局配置与说明。
- 第三方依赖声明与许可证。

## 保留在本地

- `.venv/`、Python 缓存和包安装元数据。
- 真实主题包、提取图片和视频、生成的 XML/HWT、预览与项目缓存。
- `artifacts/` 内日志、整理前快照、验证摘要。
- `archives/` 内历史压缩备份及服务运行数据。

这些路径已加入 `.gitignore`。历史报告可能引用本地截图、主题结果或绝对路径，它们是历史证据，克隆仓库后不会自动出现。

## 克隆后使用

```powershell
git clone https://github.com/MEET123-123/multi_resolution_adaption.git
cd multi_resolution_adaption
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[service]"
.\.venv\Scripts\python.exe multires.py --help
```

把自己的主题包放到 `theme/`，按 [快速使用](QUICKSTART.md) 创建源画布配置并运行转换。未生成真实主题结果前，`theme/adaptation/` 不存在属于正常情况。

首次上传未运行新的转换、单元测试或真机验证；以当前源码附带的说明和实际运行结果为准。

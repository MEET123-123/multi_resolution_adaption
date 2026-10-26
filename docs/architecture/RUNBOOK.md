# 开发者运行命令速查

所有命令从 `F:\multi-res` 根目录运行。使用项目现有 `.venv`。

| 目标 | 入口 | 说明 |
|---|---|---|
| 查看命令 | `.\.venv\Scripts\python.exe multires.py --help` | 统一 CLI |
| 查看工具 | `.\.venv\Scripts\python.exe multires.py tools --help` | 批量/模型/评测工具 |
| 查看适配主题 | `.\.venv\Scripts\python.exe multires.py serve .\theme\adaptation --port 8766` | 打开 127.0.0.1:8766 |
| 实时预览原始主题 | `.\.venv\Scripts\python.exe multires.py live "theme\<文件名>.hwt"` | 支持点击、长按、滑动等受支持交互 |
| 一体化工作台 | `.\.venv\Scripts\python.exe multires.py tools workbench --port 8790` | 适配与反馈 |
| 独立浏览器自动化 | `.\.venv\Scripts\python.exe studio_reverse\browser_automation\server.py --port 8797` | Edge/Chrome，登录由用户自己完成 |
| 实验多设备预览器 | `.\.venv\Scripts\python.exe studio_reverse\server.py --port 8796` | 非华为官方渲染 |
| 仓库结构盘点 | `.\.venv\Scripts\python.exe scripts\project_inventory.py` | 只读，写新报告 |

## 先验收，再批量

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_live_preview.py tests\test_learning.py -q
```

模型训练、批量转换和交互主题生成涉及大量数据或GPU资源，先看 [学习 MVP](../guides/QWEN_LAYOUT_MVP.md)、[多交互质量门禁](../../studio_reverse/web_theme_pipeline/README_RUNTIME_QA.md)，不要把未验证的程序生成截图视为真机结果。

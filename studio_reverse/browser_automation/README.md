# 本地浏览器自动化工作台

目标：独立打开浏览器，人工登录华为 Theme Studio Pro，随后用自动化工具点击、输入、检查页面、截图，并记录脱敏网络请求摘要。

## 启动
在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\studio_reverse\browser_automation\requirements.txt
.\.venv\Scripts\python.exe .\studio_reverse\browser_automation\server.py --port 8797
```

打开 http://127.0.0.1:8797 ，点击“启动浏览器”，在弹出的 Edge 或 Chrome 窗口自行登录。

如果没有 Edge/Chrome，先运行 ` .\.venv\Scripts\python.exe -m playwright install chromium `，再选择 Playwright Chromium。

## 支持
- HTTP(S) 导航；按角色、文本、标签、Placeholder、CSS 选择器查找元素并点击、输入、选择。
- 页面截图，查看主文档可见按钮/链接、iframe 列表；JSON 步骤编排（最多30步）。
- 捕获 Fetch/XHR/Document/WebSocket 连接请求的**摘要**（方法、URL 路径、资源类型、状态），不记录请求体和敏感请求头。
- `.browser-profile/` 保存独立浏览器会话；`captures/` 保存截图和脱敏报告，这些目录不应提交到 Git。

## 注意
- 此模块是本地原创浏览器控制器，不是华为官方内部代码或协议；不保证登录后各按钮的实际文本与示例一致。
- 不读取现有浏览器 Cookie，不绕过登录、验证码或权限；账号密码请直接在浏览器窗口输入。
- 不等同于你的现有 HWT/XML 实时预览器。后续可从真实操作中形成交互记录，再对照 `src/multires_app/preview/live_preview.py` 补齐本地渲染。
- 服务只监听 `127.0.0.1`，请求需要会话 token；不要暴露到公网。

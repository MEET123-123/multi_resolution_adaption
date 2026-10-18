# 面向客户的在线主题适配服务

当前阶段为 **private-preview（受控内测）**。已实现客户工作台与可运行服务流程，尚未达到公网收费交付条件。通用转换输出锁屏脚本适配候选，保留原包其余内容；不会自动把所有手机资源、品类元数据、壁纸和系统皮肤转换成完整平板主题。

## 本轮已实现

| 客户流程 | 实现与限制 |
|---|---|
| 登录工作区 | 管理员分配可撤销随机密钥；数据库保存 SHA256 摘要；网页仅在页面会话中持有密钥 |
| 上传主题 | 原包只读；检查路径、大小写冲突、加密、符号链接、嵌套包、CRC、解压预算和图片尺寸 |
| 异步转换 | 有界队列、独立 Python 子进程、超时终止；失败不发布结果；客户端轮询状态 |
| 布局生成 | 相近比例复用；大变化按源顺序逐行两列；组件统一缩放；横屏时间日期留白评分 |
| 人工调整 | 离线模型 JSON、参考图 bbox 建议；应用前校验；没有远程模型调用 |
| 版本管理 | 成功后切换活动版本；失败保留旧版；可恢复已发布版本，页面提供恢复初始版 |
| 客户隔离 | jobs/tasks/artifacts 都按客户所有权查询，其他客户不能读取 |
| 预览授权 | 10 分钟签名链接，限指定客户/任务/版本的允许产物；密钥撤销后失效 |
| 验证证据 | 自动回归、真实 HTTP 上传至回退、实际子进程转换；明确 `nativeDeviceTest: false` |

代码入口：`saas_api.py`（ASGI/API）、`saas_dashboard.html`（客户页面）、`saas_store.py`（所有权/版本）、`saas_jobs.py`（队列/进程）、`saas_worker.py`（转换）。旧 `interaction_server.py` 是本机调试器，不能直接作为公网服务。

## 本机启动

现有 `.venv` 已装好依赖。新环境可安装锁定版本；以下为 PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[service]"
$env:MULTIRES_SERVICE_ROOT = 'F:\multi-res\.saas-data'
$env:MULTIRES_ALLOWED_HOSTS = 'localhost,127.0.0.1'
.\.venv\Scripts\python.exe .\multires.py tools saas-store create-client --root $env:MULTIRES_SERVICE_ROOT --name '内测客户'
.\.venv\Scripts\python.exe -m uvicorn multires_app.service.saas_api:create_app --factory --host 127.0.0.1 --port 8787 --workers 1 --no-access-log
```

创建命令输出一次完整密钥，请交给对应客户，不要放进仓库、截图或聊天记录。打开 `http://127.0.0.1:8787/` 输入密钥。测试不会留下实际客户账号，没有默认通用密码。

```powershell
.\.venv\Scripts\python.exe .\multires.py tools saas-store revoke-client --root $env:MULTIRES_SERVICE_ROOT --client 客户ID
```

当前只支持 **一个实例、一个 Uvicorn worker**。启动将未完成任务标为失败，不自动重试；已发布版本保留。增加 worker 会导致恢复逻辑与本地队列相互干扰，不能直接扩容。数据库、上传、日志、中间文件均在服务根目录内，必须放在站点静态目录之外，生产环境需独立服务账号和文件 ACL。

## API 与数据约定

除入口页、健康检查和签名预览外，API 使用 `Authorization: Bearer <客户密钥>`。

| 方法与路径 | 内容 |
|---|---|
| `POST /v1/jobs?width=2560&height=1600&mode=auto` | 原始 HWT 字节流，Content-Type 为 application/octet-stream；返回 job/task |
| `GET /v1/jobs`、`GET /v1/jobs/{id}` | 自己的任务列表与详情 |
| `GET /v1/tasks/{id}` | 轮询结果或错误 |
| `GET /v1/jobs/{id}/preview` | 获得临时预览地址 |
| `GET /v1/jobs/{id}/files/adapted.hwt` | 下载活动版本候选包 |
| `GET /v1/jobs/{id}/files/readiness.json` | 下载该版本的验收缺口与复核统计 |
| `GET /v1/jobs/{id}/layout-request` | 导出活动版本的微调请求 |
| `POST /v1/jobs/{id}/layout-proposal` | JSON 建议，校验/编译成功后发布新版本 |
| `POST /v1/jobs/{id}/reference-image` | 图片字节流，异步生成建议，不自动应用 |
| `POST /v1/jobs/{id}/revisions/{n}/activate` | 切换已发布版本 |

在线初版默认导出/匹配 `state-000`，离线 CLI 可选其他状态；JSON 默认将同几何组件的锚点应用到其他状态。静态在线预览回放已采样状态，不提供本地调试器的无限实时执行服务。

上传可附 `Idempotency-Key`。同客户、同键、同目标参数返回原任务，参数不同报错；该键不校验再次上传的文件内容，换文件必须换键。应用/匹配采用单任务互斥；失败可重新提交，版本编号允许有空缺。

预算：HWT 100MB、单项解压 64MB、嵌套总解压 768MB、5000 项、深度 3、压缩比 250；图片 3200 万像素。参考图 10MB/2000 万像素，JSON 1MB；目标边长 240–8192 且最多 3200 万像素。每客户 3 个活动任务、全局 12 个，默认执行 1 个进程，超时 240 秒；每客户 20 个存储任务、每任务最多 8 个版本。目录扫描做 2GB 上传前检查，**不是硬配额**，解压和并发写入需另外限制。

进程超时不构成安全沙箱。POSIX 设置 CPU/地址空间上限；Windows 没有对应硬限制。进程继承服务环境，未禁出站网络。公网部署前必须完成隔离。

预览链接相当于临时访问凭证，拿到即可在有效期内读取该版本。示例关闭 access log；正式网关/监控也需脱敏 URL 和 Authorization。响应设置无缓存、nosniff、HTML 脚本哈希 CSP、no-referrer；这些不能代替部署审查。

## 上线验收顺序

| 优先级 | 需要完成 | 完成证据 |
|---|---|---|
| P0 结果交付 | 支持的 HarmonyOS/平板型号清单；导入、解锁、旋转、连续操作、持久设置实机回归；包级转换与复杂组件人工复核 | 设备/系统版本、安装记录、截图/录像、通过用例与缺陷单 |
| P0 任务隔离 | TLS 网关；无权限服务账号；任务容器、禁出站、只读源包、硬 CPU/内存/磁盘限额；请求超时/限流；恶意文件扫描 | 超额/恶意输入与隔离测试、部署审查 |
| P0 客户数据 | 正式账号/组织/角色、密钥轮换；资源使用授权；保留周期、删除、备份恢复；审计与监控 | 权限矩阵、越权测试、删除/恢复/告警演练 |
| P1 稳定运营 | PostgreSQL、持久队列/对象存储；任务租约、幂等提交、崩溃重试；并发/长时间/容量压测 | 目标负载下的错误率、P95、恢复时间和成本 |
| P1 商业流程 | 套餐、计量/账单、订单交付、人工审核、客服定位与恢复 | 订单到验收的试运营记录 |
| P2 自动化 | 标注评测集、视觉回归、低置信度转人工；裁切/平铺/九宫格资源编译，需要时接生成模型 | 独立评测的可读性、重叠、风格一致性、人工通过率 |

两款复杂主题仍有布局警告和有界探索，不能作为自动交付通过样本。先建立确定性适配、可审查修正和设备验收数据，再决定是否需要模型微调。

## 重复验证

```powershell
.\.venv\Scripts\python.exe tests/run_quality_checks.py
.\.venv\Scripts\python.exe .\multires.py tools release-readiness theme/adaptive-code-demo/bear/project.json --out readiness-bear.json
```

`quality-results.json` 记录实际测试数、失败/跳过与依赖版本；CI 配置见 `.github/workflows/quality.yml`，本地通过不代表 CI 或真机通过。就绪报告汇总复核数量、覆盖与上线缺口，当前明确 `productionReady: false`、`automaticDeliveryReady: false`。

上传检查参考 [OWASP 文件上传资料](https://community.owasp.org/vulnerabilities/Unrestricted_File_Upload)，协议参考 [ASGI HTTP 规范](https://asgi.readthedocs.io/en/latest/specs/www.html)。实际部署需按自身环境验证。

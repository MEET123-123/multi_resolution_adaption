# 华为应用市场中国大陆普通应用采集

范围：官网公开匿名网页数据，固定 `zone=CN`、`locale=zh_CN`，仅接受官网 16 个普通应用分类且 `ctype=0` 的条目；游戏和 `webApp=1` 的快应用不纳入。不同 AppGallery 应用 ID 分别保留，不将 Android / HarmonyOS 或同名应用擅自合并。

采集遍历官方应用分类、子分类和应用排行榜的全部公开分页，穿插逐条读取详情，沿“相关推荐”“同开发者其他应用”等列表扩展发现。不会枚举不存在的 ID，也不会将搜索结果摘要当作完整描述。

## 运行

仅使用 Python 3.11+ 标准库，无需安装第三方包。在项目根目录运行：

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe scripts/huawei_appgallery/crawl.py
```

默认持续采集到可发现队列耗尽，单线程，最小请求间隔 0.35 秒。设置检查点：

```powershell
.\.venv\Scripts\python.exe scripts/huawei_appgallery/crawl.py --max-seconds 1800
```

再次执行相同命令可接着 SQLite 中的待处理队列继续，不重复下载已完成条目。`--max-apps 10000` 表示累计获得 10,000 条有完整描述的应用时导出并停止；默认 0 不设数量上限。`--output` 可指定独立的数据目录。退出或异常会导出已获得结果，Ctrl+C 可安全停止。每 1,000 次成功处理自动导出一次。

```powershell
.\.venv\Scripts\python.exe scripts/huawei_appgallery/crawl.py --export-only
.\.venv\Scripts\python.exe scripts/huawei_appgallery/validate.py
```

网络 401 / 非会话过期 403 / 429 会停止并保存检查点，不绕过验证码或访问限制。匿名会话码按官网前端的 `getInterfaceCode` 流程初始化、过期时刷新；不需要账号，不保存会话码。分页安全上限默认每目录 1,000 页，达到上限会写入 SQLite 的 `meta` 表。

## 输出

默认目录：`artifacts/huawei_appgallery/data/`。

| 文件 | 内容 |
| --- | --- |
| `apps.csv` | UTF-8 BOM，适合导入 Excel、数据库和分析工具；多行描述按照 CSV 标准引用 |
| `apps.jsonl` | 每行一个完整应用对象；推荐程序处理这个文件 |
| `crawl.sqlite3` | 去重索引、已完成记录、待采集队列、分类分页进度和排除项 |
| `raw/details/*.json.gz` | 官方详情原始响应中的应用元数据和描述卡片，不保留用户评论 |
| `raw/catalog/*.json.gz` | 分类及目录原始响应，便于审计和重新解析 |
| `manifest.json` | 实际条数、分类统计、描述长度、停止原因和覆盖限制 |
| `validation.json` | JSONL / CSV 一致性、原始描述逐条校验结果 |
| `failed_or_unavailable.jsonl` | 详情不可用、介绍为空或请求失败的条目，明确区别于成功数据 |
| `background_job.json` | 持续采集任务的进程 ID、运行/停止/完成状态（使用持续运行入口时生成） |
| `progress.json` | 最近一次采集进度（每 100 次处理更新） |
| `icons/<app_id>.png` 等 | 实际下载的官方原始图标，按照图片真实格式保留后缀 |
| `icons_progress.json` | 图标补下载的成功、待处理、缺失和错误数量 |

`description_raw` 是接口 `detailappintrocard.appIntro` 的原文，不截断、不改写、不补充。`description` 只清理 HTML 标记/实体和首尾空白。`description_sha256` 是原文 UTF-8 的 SHA-256，可与原始响应核对。官方原文很短时仍如实保留。

其他字段包括：名称、包名、应用 ID、分类/子分类、开发者、短介绍、版本、更新时间、大小、评分、下载量、年龄要求、价格、图标/截图链接、隐私政策、官网、权限、平台封装类型、来源网址和北京时间采集时间。`update_notes` 仅在官方详情实际返回更新卡片时填写，缺失时留空。未下载 APK 或截图图片。

图标会另存到 `icons/`，不改写原始图片字节。CSV/JSONL 增加 `icon_local_path`（相对于数据目录）、`icon_status`、`icon_sha256`、`icon_size_bytes`、`icon_width`、`icon_height`、`icon_downloaded_at`、`icon_error`。只有图片格式、完整性校验通过且文件落盘成功才标记为 `done`。缺失图标记为 `missing`，网络等错误记录原因并最多尝试三次。保留 `icon_url` 可追溯来源。

持续入口现在会自动启动图标补采进程，先补齐已有应用，再跟随新应用下载。图标进程最多 4 个并发请求，全局请求间隔至少 0.1 秒；遇到访问限制或限流时停止。安装有 Pillow 时会额外校验图片解码，当前项目环境已提供 Pillow。需要单独补图标可运行：

```powershell
.\.venv\Scripts\python.exe scripts/huawei_appgallery/download_icons.py --output F:/multi-res/artifacts/huawei_appgallery/data
```

加 `--once` 可只处理当前已有应用。补采期间每约 1,000 个图标更新 CSV/JSONL，并在追上已有数据后再次导出；两个进程使用文件锁避免同时覆盖导出。`STOP` 同时停止描述和图标采集，`STOP_ICONS` 只停止图标进程。所有已提交的进度都可续跑。

详情和图标进程对 SQLite 的写入使用独立的跨进程事务锁，网络请求在锁外执行，写入失败会回滚；数据库等待时间为 60 秒。导出使用另一把文件锁，图片和原始详情文件不受导出锁阻塞。

持续采集入口 `run_continuous.py --output <数据目录绝对路径>` 不设时间或条数上限，队列耗尽后自动逐条校验并更新 `background_job.json`。采集时每 1,000 次处理更新 CSV/JSONL；同一目录只运行一个采集进程。需要停止时在数据目录新建名为 `STOP` 的空文件，任务会在当前请求完成后导出并停止。删除该文件后可再次运行接着采集。程序或电脑关闭会中断采集，SQLite 保留已提交进度。

## 覆盖与解释

- 这是采集时刻官网公开入口可发现的数据集，不能证明覆盖应用市场全部在架应用；推荐排序和地域/设备可见性可能影响发现范围。
- 同一个应用 ID 只导出一次；同一包名对应多个上架 ID 时保留各自原始条目。
- 详情下架时接口可能仍返回 `rtnCode=0` 和推荐列表；脚本必须核对详情里的应用 ID，不会把推荐应用的介绍冒充该条目。
- 名称里出现“游戏”的辅助工具仍可能是官方普通应用；过滤依据是官方分类和类型，不按名称关键词误删。
- `pending` 代表尚未采集，`unavailable` 代表详情不再提供，`empty_description` 代表官方没有返回介绍；成功导出仅含 `done`。
- 描述版权属于各应用权利人；数据保留官方来源供核验。

# 可插拔评测与 Badcase 反馈闭环

本轮依据用户提供的 `theme/recircle.md` 增量实现。保留旧 `eval-batch` 汇总工具；新增 `feedback-loop` 负责逐场景证据、候选回归、人工标签与固定用例。所有产物进入独立运行目录，原始主题和已有适配缓存只读。

## 现状与复用（M0）

| 能力 | 现有实现 | 本轮接入 |
|---|---|---|
| 布局与重排 | `scene_clusters.schedule_device`、AnchorLayout、Kiwi | Refiner 调用原接口，不增加第二套求解器 |
| 几何与关系 | screen_relations、spatial_alignment、spatial_flow、corner_geometry、offscreen_geometry | 逐规则 Observation，异常单独 UNKNOWN |
| 光学居中 | alpha 可见边界、group membership | 从源图重新测量，检查组件组整体中心，支持 x/y；不依赖 solver 的 centerAxes 标志 |
| 美学 | aesthetic_optimizer.Evaluator | 重新计算选中布局，不拿丢弃候选分数代替最终结果 |
| 渲染 | paint_visibility.raster_snapshot | 原图、目标图、标注图及 unsupported 清单 |
| 交互 | InteractiveSession、InteractionHistory | 多步 source/target 手势事务、控制值一致性和调试器根快照恢复 |
| 脚本保真 | export_script、validate_export、XML lexical audit | 候选接受之前执行原有导出与审计 |
| 历史报告 | validation.json、live-validation.json | 没有当前候选哈希绑定的历史通过结果只作为 UNKNOWN 证据 |

无原生设备数据、人工金标准、IQA/视觉模型时，分别显示 `not_measured` 或 `needs_native_validation`；不提供虚构的 Precision/Recall。

## 状态与规则

- `pass`：当前适用规则已有通过证据。
- `fail`：数值、明确约束、源居中保持、脚本/交互等确定失败。
- `review`：bbox 新增相交等存在风险、尚不能证明实际视觉遮挡的问题。
- `unknown`：缺少源状态、探索截断、未回放、解释器不支持、插件异常等。
- `not_applicable`：没有该设计关系，或用户的手动布局规则接管。

严重程度 P0/P1/P2 与置信度分开；样本少不会把确定的脚本失败降级。默认目标中心容差为画布轴长的 0.1%，且至少 0.5 个逻辑像素，这是工程初始阈值，尚未经过设计师标注校准。

中心意图根据源组件组的可见内容边界判断，而非图片透明画布。源中心接近画布中心（轴长 1.5% 内，至少 2 逻辑像素）时建立关系；源码已有多稳定帧证据时要求这些帧均满足，避免动画经过中心时错误锁定。自动组保持整体居中，不把组内各图片分别拉到中心。角落组件和显式手动锚点保留自身规则。

源分离、目标 bbox 相交先进入 REVIEW；明确的间距契约由原有 flow 校验器确认后进入 FAIL。仍需积累人工样本，校准透明装饰和合法重叠的误报。

## 使用

在项目根目录运行：

```powershell
# 单项目，所有已有目标设备
.\.venv\Scripts\python.exe multires.py tools feedback-loop scan --project theme/adaptation/wufu/scenes/project.json

# 批量评测（新的运行目录，不覆盖历史）
.\.venv\Scripts\python.exe multires.py tools feedback-loop scan --workers 3 --out artifacts/evaluation/runs/batch-v1

# 只评指定主题与设备，执行有预算的多步交互及响应式采样
.\.venv\Scripts\python.exe multires.py tools feedback-loop scan --only wufu --target tablet-mock-16-10-landscape --replay-traces 3 --responsive --config configs/evaluation-rules.example.json

# 调用原布局插件重新求解，有限迭代；候选经重新评测、逐项回归及导出审计
.\.venv\Scripts\python.exe multires.py tools feedback-loop iterate --only wufu --rounds 1 --out artifacts/evaluation/runs/iteration-v1

# 比较同输入/设备/规则/配置的两次评测
.\.venv\Scripts\python.exe multires.py tools feedback-loop compare artifacts/evaluation/runs/run-a artifacts/evaluation/runs/run-b --out artifacts/evaluation/compare/ab.json

# 中断后恢复同一批次；资源紧张时逐主题处理
.\.venv\Scripts\python.exe multires.py tools feedback-loop scan --workers 1 --resume --out artifacts/evaluation/runs/batch-v1

# 校验观察/Badcase 格式
.\.venv\Scripts\python.exe multires.py tools feedback-loop validate artifacts/evaluation/runs/batch-v1/badcases.jsonl
```

退出码：0 = 所有适用检查通过；1 = 存在确定失败/回归；2 = 无确定失败，但存在 REVIEW/UNKNOWN。`compare` 返回 0 只表示没有检测到相对回归，不表示没有存量错误或未知项。

默认不做耗时的交互回放和 viewport sweep，报告对应 UNKNOWN。`--replay-traces` 从较深路径开始抽样，最大步骤默认 12；没有测到的分支不当作通过。`back` 验证的是调试历史恢复，主题自身的返回按钮必须包含在动作路径里验证。

## 人工反馈 → 固定回归

看板 `report.html` 展示规则、组件、原图、标注图和原始动作路径；JSON/CLI 为可追溯标注入口。

```powershell
# Case ID 从看板/observations.jsonl 获取
.\.venv\Scripts\python.exe multires.py tools feedback-loop review artifacts/evaluation/runs/run-a --case <case-id> --label confirm --reviewer designer --cause layout --notes "时间组应水平居中"

# 确认/驳回后才能冻结；期望修复后通过，已有 fixture 禁止自动覆盖
.\.venv\Scripts\python.exe multires.py tools feedback-loop freeze artifacts/evaluation/runs/run-a --case <case-id> --expected pass --split validation --out artifacts/evaluation/golden/time-center.json

# 对新一次运行检查固定用例
.\.venv\Scripts\python.exe multires.py tools feedback-loop regressions artifacts/evaluation/runs/run-b --fixtures artifacts/evaluation/golden --out artifacts/evaluation/regressions/result.json
```

`reviews.jsonl` 追加记录标注人、时间、输入和观察摘要，不修改自动检测结果。支持 confirm/dismiss/unknown、根因备注与 A/B/both/neither 偏好。`pass-audit.json` 使用固定 seed 从通过样本抽样，避免只看最差案例。

同一输入主题不得分散在 train/validation/test；源脚本、状态路径发生变化后旧用例会变成 UNKNOWN，需要复核匹配，不能悄悄通过。聚类只输出 `suspected`，标注人确认原因之前不宣称找到真实根因。当前不自动训练排序器、不自动改权重、不自动更新 golden_test。

## 候选接受与回滚

1. 候选只允许修改布局结果；源脚本、图、组件身份、资源、源状态与设备配置受不变性检查。
2. 在同输入/同配置下重新运行各评价插件，逐状态比较。
3. 拒绝新增失败、检查缺失、UNKNOWN 增加、既有关系恶化、单状态美学代理下降超预算。
4. 必须改善至少一个确定 FAIL；只改善主观 REVIEW 不自动接受。
5. 接受前调用既有 XML 导出、强制约束和保真审计；失败保留基线。
6. 候选、前后观察、comparison、导出结果写在 `candidates/`；接受的候选附预览与素材副本。不会覆盖 `theme/adaptation`。

`iterations.json` 记录接受/拒绝理由。没有改善也停止，最多 3 轮。自动接受仅表示保留离线候选，仍需完成未知项和原生验收。回滚通过继续使用原缓存或上一个运行目录实现。

## 插件接口

`evaluation/core.py` 定义 `Evaluator`、`Refiner` Protocol。布局插件接口 `layout_scene(...)` 保持不变。

```python
from multires_app.evaluation import Observation

class MyEvaluator:
    name = "custom-visual"
    version = "1.0"

    def evaluate(self, context):
        # 只读 context.project；模型版本、预处理、置信度写入 evidence。
        yield Observation("CUSTOM.VISUAL", "unknown", stage="visual",
                          evidence={"reason": "model unavailable"})

def create():
    return MyEvaluator()
```

通过 `--plugin your_module:create` 加入；通过 `--refiner your_module:create` 接入 `propose(project, observations, options) -> candidate_project`。插件只从明确的命令行参数加载，不从主题 XML 或资源内容加载。插件属于可信本地 Python 扩展，不是安全沙箱；异常会变成 UNKNOWN，已产生的部分 PASS 会丢弃，但恶意插件或无限阻塞插件仍需进程级隔离。

规则扩展时增加稳定规则名并升级插件版本。Observation 校验状态、严重度、置信度、有限残差和 JSON 数值；输入身份、动作、阶段、算法/代码摘要、评价器版本写入运行记录。CLI 配置例子见 `configs/evaluation-rules.example.json`。

## ReDeCheck 方法迁移

参考 [作者论文](https://philmcminn.com/publications/walsh2017a.pdf) 和 [项目仓库](https://github.com/redecheck/redecheck)，独立实现采样关系图，没有复制 Java 源码或新增 Java/Selenium 运行时依赖。

- DOM 节点替换为主题组件与可见 bbox。
- 支持 width/height 两条采样路径；同一设备、状态分别建图。
- 均匀采样、已声明边界±1 采样、相邻关系变化二分细化。
- 保存可见、包含、中心、上下/左右、相交及对齐关系。
- 检出 A→B→A 短区间异常，保留为 REVIEW，允许有意断点重排。
- 报告预算、失败 probe、未细化区间；`exhaustive=false`，不把有限采样当作连续区间穷尽证明。

当前 probe 会调用现有布局插件重新求解，主要测试求解器对视口变化的稳定性；它不等价于已生成 HWT 在设备上动态旋转或展开。后者还需要原生证据。

## 本轮范围与后续扩展

批量报告逐行汇总，不将所有主题的反序列化对象同时放入内存。通过样本的动作轨迹保存在共享 `repro.json`；`cases.sqlite3` 为 Case ID 提供定位索引，标注时无需反复读整个大报告。`inputs/` 保存参与本轮评测的项目快照，便于缓存更新后仍能复现。

渲染设置单素材 800 万像素、单视图源素材合计 2400 万像素及投影图层合计 1600 万像素的初始预算；预算外资源标为 UNKNOWN，不强行解码。每个证据视图结束清理全尺寸图片缓存。以上是内存控制预算，不是布局质量标准。并发数应根据机器内存选择；恢复模式会保留不完整输出到 `interrupted/`，只复用摘要、配置和评价器版本一致的已完成主题。

已提供 M0–M4 的离线最小闭环；M5 的模型评价、人工金标准 Precision/Recall 校准、浏览器像素基线与原生设备采集尚未实现。原图/目标图/问题框均为模拟证据，不对不同分辨率生成误导性的整图像素差。

长任务当前可按 `--only` 划分主题批次接入已有任务队列。核心 EvaluationContext/Protocol 不绑定某个排版算法或模型，后续可增加 OCR、IQA、浏览器截图和原生证据插件。

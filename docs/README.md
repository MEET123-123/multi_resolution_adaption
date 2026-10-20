# 文档导航

初次阅读：[项目结构](PROJECT_STRUCTURE.md) → [快速使用](QUICKSTART.md) → [接入自己的算法](CUSTOM_LAYOUT.md)。

[目录整理说明](ORGANIZATION.md) 解释文件搬迁及旧路径查找。

## 设计与使用

- [HarmonyOS 5.0 及以上：规范对照与迁移方案](guides/HARMONYOS5_MIGRATION.md)
- [HarmonyOS 5+：逐文件改造、接口草案与验收清单](guides/HARMONYOS5_IMPLEMENTATION_PLAN.md)
- [保留布局与组件重排：代码化适配流程](guides/ADAPTATION_WORKFLOW.md)
- [布局美学评价与迭代优化](guides/AESTHETICS.md)
- [批量评测、badcase 归因与反向优化](guides/EVAL_AND_BADCASE.md)
- [可插拔评测闭环：居中保持、ReDeCheck 关系图与固定回归](guides/FEEDBACK_LOOP.md)
- [Qwen3-VL 布局微调 MVP：mock、批量标注与真实配对接口](guides/QWEN_LAYOUT_MVP.md)
- [模型 MVP 接口审计](guides/IMPLEMENTATION_AUDIT.md) / [缓存数据流水线](guides/DATA_PIPELINE_PLAN.md)
- [UI 数据集调研与主题标注方案：标签、配对、质检和训练任务](guides/UI_DATASET_ANNOTATION_STRATEGY.md)
- [九宫格锚点、容器与约束求解](guides/ANCHOR_LAYOUT.md)
- [交互状态与变量变化的数据模型](guides/INTERACTION_STATE_MODEL.md)
- [布局 JSON 微调与参考图匹配](guides/LAYOUT_REFINEMENT.md)
- [居中、边缘与上下间距](guides/LAYOUT_RELATIONS.md)
- [布局复用、两列重排与资源处理](guides/LAYOUT_RESEARCH.md)
- [旧版项目总览（历史记录）](guides/LEGACY_README.md)
- [面向客户的在线主题适配服务](guides/SAAS_READINESS.md)
- [交互场景、组件重排与主题回写](guides/SCENE_WORKFLOW.md)

## 修复与验证报告

- [初升与叠峰：真实主题模型流程验证与限制](reports/REAL_THEME_MVP_RESULTS.md)
- [Qwen3-VL mock 微调：实测结果、约束检查与质量门禁](reports/QWEN_MVP_RESULTS.md)
- [自动评测反馈闭环：全量基线、修复候选与资源控制](reports/FEEDBACK_LOOP_RESULTS.md)

- [美学评价与真实主题迭代结果](reports/AESTHETICS_RESULTS.md)
- [对齐关系修复与全量验证](reports/ALIGNMENT_RESULTS.md)
- [AnchorLayout V1 静态轮次结果（媒体首帧改造前）](reports/ANCHOR_LAYOUT_RESULTS.md)
- [半岛日记改：顶部面板提前出现修复](reports/BANDAO_DRAWER_RESULTS.md)
- [半岛日记改：时间纵向重叠修复](reports/BANDAO_LAYOUT_RESULTS.md)
- [波蓝：底部角落与弹窗缩放修复](reports/BOLAN_LAYOUT_RESULTS.md)
- [稳定场景边界与平衡缩放](reports/CANVAS_BOUNDS_RESULTS.md)
- [底部手电筒、相机角落锚点修复](reports/CORNER_ANCHORS_RESULTS.md)
- [居中与四向布局：本轮修复和全量验证](reports/LAYOUT_RELATIONS_RESULTS.md)
- [媒体首帧布局结果](reports/MEDIA_LAYOUT_RESULTS.md)
- [贴边与底部均分修复](reports/SCREEN_RELATIONS_RESULTS.md)
- [左右滑动双场景识别修复](reports/SWIPE_PAGES_RESULTS.md)

## 设计原稿

- [HarmonyOS 5+ 官方规范来源与核对记录（2026-10-19）](reference/HARMONYOS5_SPEC_SOURCES.md)
- [anchor-design-source](reference/anchor-design-source.txt)
- [华为主题多分辨率适配_锚点分组容器布局技术设计_V1.0](reference/华为主题多分辨率适配_锚点分组容器布局技术设计_V1.0.docx)

## 历史证据说明

报告中的验证次数和结果属于各轮修复记录。目录整理没有重新执行这些测试或真机验收，当前源码位置以项目结构与迁移清单为准。

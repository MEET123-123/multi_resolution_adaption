# 数据集标注结构示例包 v1.2

这是虚构的格式示例，包含1个家族、2个状态、1个目标配置。没有真实主题XML、原始素材、脚本运行结果、人工批准或真机证据。全部图像为程序绘制的框图，全部初始答案标为weak；不可以作为生产数据集、训练真值或成绩。

## 建议阅读顺序

1. 阅读manifest.json和release/dataset-card.json，确认用途和限制。
2. 看samples.jsonl：一行一个“源状态+目标请求”，requestKey关联约束，g0等仅是样本内别名。
3. 对照annotations与layouts：元素运行实例、源节点、组件和组保留对应；源框与目标框的坐标空间不同。
4. 看relations和trajectories：这是拟增加的sidecar，当前CLI并不自动生成或导入这些schema。
5. 看templates/review.template.json：仅为API格式模板。approved这个字符串用于示范请求格式，不代表已有审批；它未放入reviews.jsonl，禁止导入真实库。
6. 看provenance和native-evidence：未审核、未测试内容保持空值，不能用示例补造证据。
7. 运行 `python validate_examples.py`，只检查示例JSON、文件引用、组ID、requestKey及基本数值范围。

## 标注作业要点

先做20条独立双标校准，再标角色、组、关系、交互及目标。成员重组要重建请求；当前annotationEdits仅支持elementSemantics、groupSemantics、sceneIntent。拖框后先求解、渲染再确认，不能拿未更新底图审批。在线提交用最新baseRevision，离线导入采取单写者与固定版本。

## 坐标

request.groups.sourceBox及inputLayout.elementLevel.sourceBox相对1080×2400源画布归一化到1000；annotations中的sourceBox/script_bbox为源逻辑xywh；目标答案相对safeRegion归一化到1000。target图只画建议框，没有执行等比求解。图像上方42像素为示例说明条，不属于逻辑画布，示例图不能用于几何训练。

## 与现有工程的关系

multires-layout/1答案与review字段按F:/multi-res现有实现整理。samples是格式教学用子集，不保证适合直接导入serve或export-sft；缺少真实project、完整render结果和脚本事实。不会启动服务、执行训练、联网或修改生产目录。完整流程见随附技术文档第7.17至7.25节。

## 生产补齐项

真实源包与哈希口径；设备和引擎版本；完整关键事件与动画采样；授权状态；已审成员和目标脚本；复算后的画面；真实审核身份与时间；原生已测或待测范围；家族去重及冻结划分；实际工时。每次发布固定文件摘要与数据卡。

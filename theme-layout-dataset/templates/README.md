# 表单怎么填写

所有模板都是空白结构，不是已标注样本。复制对象模板到任务的对应数组中，再由人工填写。不要直接提交空模板。

| 字段 | 填写方式 |
|---|---|
| `source.*` | 原包、解析项目、源图的实际路径与 SHA256；源状态 ID、样式、相位和源画布。`init --project --state` 可填入部分事实 |
| `source.coordinate_unit` | `engine_logical` 或已核对的 `native_pixel_verified`；源缩略图像素不可当布局单位 |
| `target.canvas / coordinate_unit / safe_region` | 目标引擎画布及安全区 `{x,y,w,h}`，单位必须明确 |
| `target.design_brief / brief_origin` | 事前需求；来源为 `designer_brief` 或 `product_requirement`，不要用目标答案反推的描述 |
| `dataset.*` | 家族、集合、家族审核/使用范围状态；后两项经确认才填 `confirmed` |
| `source_structure.scene_type` | 当前屏的用途，例如锁屏主场景/样式设置；按事实填写 |
| `source_structure.groups` | 复制 `group.blank.json` 对象；父组引用稳定 ID，叶成员使用事实库的节点 ID |
| `source_structure.exclusion_sets` | 每项 `{ "node_ids": [], "reason": null, "evidence": null }`，按相同原因批量说明，不逐节点重复写 |
| `*.content_regions` | 复制 `region.blank.json`；`kind=subject/protected/whitespace`，`origin=human/algorithm_candidate`；发布不接受未审核候选 |
| `*.relations` | 复制 `relation.blank.json`；对象 ID 为组件/区域/热区节点，或保留参照物 `source_canvas / target_canvas / target_safe_area` |
| `target_layout.mode` | 见词表；没有修改也要注明为什么保留 |
| `target_layout.topology` | `preserved` 或 `requires_topology_support`；后者单独保留，不通过当前根组布局发布检查 |
| `target_layout.group_boxes_1000` | 根组 ID 到目标安全区 xywh 数组的字典；不放源元素框或局部框 |
| `target_layout.mapping` | 每个根组一条 `{ "source_group_id": null, "target_group_id": null }`；保留拓扑时使用相同稳定 ID |
| `target_layout.local_tasks` | 局部任务文件路径数组；子任务另行版本化，当前检查工具不认证局部排布执行效果 |
| `target_layout.reference_*` | 人工核验的目标渲染图与来源；来源词表见 `config/taxonomy.json` |
| `quality.*` | `pending/passed/failed/not_applicable`，说明在 `quality.notes`；只有交互/写回可说明不适用 |
| `review.*` | 标注人、独立复核人、阶段、仲裁说明及挂起原因；不能由程序批量批准 |

源 `content_regions` 框相对源画布；目标 `content_regions` 框相对目标安全区。壁纸经过裁剪/缩放后，目标主体区必须重新核对，不能直接照抄源框。人物景深允许遮挡的范围通过关系例外记录。

关系参数：边距/间距使用 `parameters` 明示 `value` 或 `min/max`、`unit`、`tolerance`；单位为 `normalized_1000` 时必须说明相对区域的轴。`equal_gap` 还应有有序组件 ID 列表，`allow_overlap` 应说明允许区域/遮挡范围。检查工具核对结构引用，数值是否符合设计由人工与后续执行器核验。

`group.bbox_basis` 为 `script/visible/hit/clipped`；`offscreen_policy` 为 `inside/state_driven/allowed_transition`。`evidence` 必须简要说明分组依据，不能只写“正确”“很好看”。

目前 `--release` 面向不改变拓扑的根组布局子集。它校验字段、身份、引用、哈希和覆盖，不验证图片视觉内容、不测关系实际满足情况、不认证真机效果，也不执行微调。局部重排、拓扑变更和完整关系计划仍保存于标注库，待单独执行/导出协议支持。

# 接入自己的布局算法

真实主题使用场景算法接口。先看 [示例](../samples/custom_scene_layout.py)，主要入口是 [场景流程](../src/multires_app/pipeline/scene_pipeline.py) 中的 `load_scene_algorithm()`。

## 接口

```python
class MySceneLayout:
    name = "my-layout-v1"

    def layout_scene(self, components, source, target, context):
        return {
            component["id"]: {"sx": 1.0, "sy": 1.0, "tx": 0.0, "ty": 0.0}
            for component in components
        }
```

按组件 ID 返回相对于脚本当前坐标的变换。四个字段为横向缩放、纵向缩放、横向平移、纵向平移。上面仅示意接口，单位变换不代表完成适配；交互、素材和动画由其他模块处理。

调用 `scenes` 或 `relayout` 时指定：

```text
--algorithm samples/custom_scene_layout.py:MySceneLayout
```

使用自己的文件和类名。`samples/custom_scene_layout.py` 中实际提供的类名可直接在文件中查看。

| 数据 | 含义 |
|---|---|
| `components` | 本轮提交求解的组件、边界、角色、所属节点 |
| `source`、`target` | 源设备和目标设备配置 |
| `context.logicalViewport` | 脚本使用的逻辑画布 |
| `context.layoutRequest` | 整体重排、局部变化、弹层或复用请求 |
| `context.fixedTransforms` | 公共组件需要沿用的变换 |
| `context.model`、`context.state` | 脚本模型与当前状态 |

字段以 `pipeline/scene_clusters.py` 构造的上下文为准。默认算法在 `layout/anchor_layout.py`，约束求解在 `layout/anchor_solver.py`；可以替换整体算法，也可以复用现有关系推断和校验。

旧元素级 `layout(elements, source_profile, target_profile, context)` 接口仍保留在 `layout/layout_api.py`。

目录整理保留接口签名。以前保存的内置算法模块名通过 `compat.py` 映射；独立插件中的旧源码导入需要更新为 `multires_app.<分组>.<模块>`。

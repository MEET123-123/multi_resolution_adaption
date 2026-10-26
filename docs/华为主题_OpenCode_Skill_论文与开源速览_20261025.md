# 华为主题适配：论文与开源思路速览

> 面向团队快速阅读｜2026-10-25｜技术路线：OpenCode + 文本大模型 + Skill，直接修改主题脚本与素材。  
> **一句话结论：** 先用专用工具保证“改得对”，再用真实预览保证“看得好”，最后用案例检索和失败经验提高弱模型效果。

## 一、核心论文（一句话读懂）

| 优先级 | 论文与入口 | 核心思路与项目启发 |
|---|---|---|
| **P0** | [SWE-agent（NeurIPS 2024）](https://arxiv.org/abs/2405.15793) · [代码](https://github.com/SWE-agent/SWE-agent) | **给模型设计专用操作接口**，让 DeepSeek 调用“解析主题、精确补丁、校验”工具，而不是自由重写 XML。 |
| **P0** | [LayoutNUWA（ICLR 2024）](https://arxiv.org/abs/2309.09506) · [代码](https://github.com/ProjectNUWA/LayoutNUWA) | **把布局变成代码生成与补全任务**，先确定可变部分，再让模型生成布局计划和脚本补丁。 |
| **P0** | [UI2Codeᴺ（ICML 2026）](https://proceedings.mlr.press/v306/yang26v.html) · [代码](https://github.com/zai-org/UI2Code_N) | **生成→渲染→观察→修复**，把现有实时预览改成 Agent 可调用的反馈工具。 |
| **P0** | [LayoutPrompter（NeurIPS 2023）](https://github.com/microsoft/LayoutGeneration/tree/main/LayoutPrompter) | **动态选优秀示例并对多个候选排序**，让 DeepSeek 借鉴已验收的横屏主题方案，而不是从零设计。 |
| **P1** | [RALF（CVPR 2024）](https://openaccess.thecvf.com/content/CVPR2024/html/Horita_Retrieval-Augmented_Layout_Transformer_for_Content-Aware_Layout_Generation_CVPR_2024_paper.html) · [代码](https://github.com/CyberAgentAILab/RALF) | **检索相似内容与布局来辅助生成**，构建按设备比例、组件关系和交互复杂度检索的主题案例库。 |
| **P1** | [Self-Refine（NeurIPS 2023）](https://arxiv.org/abs/2303.17651) | **反馈后迭代修改初稿**，每次只针对确定错误做局部修复，限制最多三轮。 |
| **P1** | [GEPA（ICLR 2026）](https://arxiv.org/abs/2507.19457) · [代码](https://github.com/gepa-ai/gepa) | **从失败轨迹反思并优化提示词**，用真实渲染与校验报告迭代 Skill，但更新前必须回归测试。 |
| **P2** | [Distilling Step-by-Step!（ACL 2023）](https://aclanthology.org/2023.findings-acl.507/) · [代码](https://github.com/google-research/distilling-step-by-step) | **用强模型提供的解释辅助小模型学习**，后续积累已审核的适配策略和修复轨迹再考虑微调。 |
| **P3** | [Agent Lightning（2025）](https://arxiv.org/abs/2508.03680) · [代码](https://github.com/microsoft/agent-lightning) | **把 Agent 执行轨迹用于后续训练优化**，当前先完整记录工具调用与结果，暂不急着做强化学习。 |

## 二、开源工具（一句话决定用不用）

| 开源项目 | 一句话用途 |
|---|---|
| [OpenCode Skills](https://docs.opencode.ai/docs/skills/) | 用 `SKILL.md` 沉淀主题规范、适配步骤和常见错误处理经验，**立即接入**。 |
| [OpenCode Custom Tools](https://docs.opencode.ai/docs/custom-tools/) | 把现有 Python 解析、XML 修改、素材处理、预览和评测包装成可控工具，**立即接入**。 |
| [Aider](https://github.com/Aider-AI/aider) | 借鉴“规划与编辑分离”，减少模型在修改复杂脚本时误伤其他节点。 |
| [DSPy](https://github.com/stanfordnlp/dspy) | 有稳定评测数据后自动优化 Prompt 和 Few-shot 示例，**第二阶段再做**。 |
| [Playwright](https://playwright.dev/docs/test-snapshots) | 固定环境下自动截图和对比浏览器预览，**不能替代真机验收**。 |
| [SAM 2](https://github.com/facebookresearch/sam2) | 复杂主题的前景分割与 Mask 生成，**按素材需要接入**。 |
| [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) | 原始图片放大后模糊时做超分辨率修复，**按需接入**。 |

## 三、最值得做的 4 个实验

1. **专用工具 vs. 直接改 XML：** 固定 DeepSeek 与主题集，比较脚本有效率、交互保真和误改率（SWE-agent）。
2. **单次生成 vs. 渲染修复：** 给 Agent 多状态预览与结构化错误信息，比较三轮以内的修复收益与成本（UI2Codeᴺ）。
3. **无案例 vs. 检索 Top-3 案例：** 用已审核的真实主题补丁做 Few-shot，比较横屏构图与人工盲评（LayoutPrompter / RALF）。
4. **原 Skill vs. 失败驱动的新 Skill：** 从坏例生成候选规则，用固定验证集检查是否改进且无新回归（GEPA）。

## 四、推荐阅读和落地顺序

**第 1 阶段：** SWE-agent + LayoutNUWA → 为 OpenCode 做专用工具、规范 Skill 和可回滚 XML/素材修改。  
**第 2 阶段：** UI2Codeᴺ + Self-Refine → 接入真实渲染、多状态检查、错误定位和有限自动修复。  
**第 3 阶段：** LayoutPrompter + RALF → 建已审核案例库，提供相似示例和多候选比较。  
**第 4 阶段：** GEPA + DSPy → 用失败经验迭代 Skill/Prompt；数据充分后才考虑蒸馏或强化学习。

**验收重点：** 同一主题、设备和模型预算下比较**脚本通过率、交互保真、视觉盲评、成功交付率、成本与耗时**；浏览器合格不代表鸿蒙真机合格，也不能直接宣称 DeepSeek 的通用能力达到 GPT。

**配套详版：** [OpenCode + Skill 工程化路线图](./华为主题_OpenCode_Skill适配工程化路线图_20261024.md)。

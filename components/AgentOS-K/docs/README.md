# AgentOS 实施指南

更新：2026-09-15；当前采用单一工作区的通用核心与可选产品扩展。结构与入口见根 README 和 architecture.md；CORE-002 记录本轮重构与清理。历史 RAG 真实配置与检索验收保持暂停。

**执行依据在本目录的指导文件，用户报告入口为 [human/README.md](human/README.md)。** 需求先落实到范围、架构、任务卡、分工和验收，再执行、测试、审阅，最后汇报。human/ 顶层仅保留当前报告 README.md，每次交付的完整存档位于 human/archive/，不承担指导或授权规则维护；优先更新既有文件。

## 已确认的项目理解

AgentOS 在 WSL2 中运行，用统一适配层接入保持独立 GitHub 仓库的产品。首个产品为 X0luka/RAG-AGENT。复用产品原网页的上传、切分、查询，集中查看状态、调用现有能力、归档原网页会话。开发仍以 VS Code 为入口；是否引入 pi 实现诊断/代码修改属于后续自治设计，不是首版的编码 provider 调度需求。

本指南根据用户后续澄清及固定提交源码修订，优先于此前的 AgentOS v1.0 草案。旧草案中“首版必须两个产品”“默认不使用向量数据库”“重建上传问答 UI”“固定 4.5GB 任务池”“全部数据自动归入 var”不再适用于当前接入。

## 阅读与维护

| 何时 | 必读 |
|---|---|
| 每次接手 | [工程规则](engineering-rules.md)、[当前任务](plans/current.md)、[交接](handoff/current.md) |
| 理解需求 | [产品范围](product-scope.md)、[自主边界](autonomy-boundaries.md) |
| 修改设计/代码 | [架构](architecture.md)、[统一契约](adapter-contract.md) |
| 接入 RAG | [源码审阅](integrations/rag-agent.md)、[会话数据](conversation-data.md)、[环境证据](evidence/environment.md) |
| 提交或升级 | [Git 流程](git-workflow.md)、[验收](verification.md)、[运行手册](operations.md) |
| 规划或改变范围 | [路线图](plans/roadmap.md)、[决策](decisions/README.md) |
| 选择并执行任务 | [任务卡](plans/tasks.md)、[确认事项分工](plans/confirmation-matrix.md)、[执行证据格式](plans/evidence-template.md) |
| 审核本包 | [审阅报告](review.md)、[变更记录](CHANGELOG.md) |

规则冲突按系统/工具约束、用户当前明确指令、适用仓库规则处理。用户授权优先于文档旧假设。事实来自代码与实测；产品目标来自用户，不能让代码现状反向取消目标。无法协调的目标冲突只暂停相关工作，不自动降低验收门槛。

每条描述区分：**已验证事实**、**拟实施设计**、**待验证项**。契约中的新增接口不代表远端已实现。变更设计须更新理由、受影响验收和变更记录；禁止仅为了让实现过关而修改目标。

## 本轮实际完成与未完成

以 [DEV-001](plans/runs/DEV-001.md) 为准：两个完整 WSL Git checkout 已建立并实现离线接入，历史 35 项回归与原网页恢复集成通过；CHECK-002 已验证受限检索下的真实 DeepSeek，原版完整检索及生产部署未验收。


## GitHub Wiki 新任务
执行依据：[Wiki 规范](integrations/github-wiki.md)，包含任务、接口、验证与交付。当前用户授权优先于旧分工中的禁止本地提交/允许推送：本轮允许本地提交，明确禁止任何推送。

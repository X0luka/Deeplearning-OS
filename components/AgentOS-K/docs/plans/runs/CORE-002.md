# CORE-002 核心与扩展统一、工作树清理

日期：2026-09-15（Asia/Shanghai）。执行责任：Agent。基线 6817143，初始工作区干净；当前功能全部保留，本轮没有移植旧 main/rag 源码覆盖当前版本。

## 实施

先更新架构、范围、契约、任务、分工与验收，再迁移代码。通用核心保留 domain/ports/application/worker/host 和通用 adapters；RAG/Wiki 适配进入 aos/integrations/rag、wiki。RAG 的 Qdrant/Streamlit 探测及容器启动规则、HTTP query、错误提示全部从核心迁出。bootstrap 通过通用发现与 Integration 描述装配；--products 支持 all/none/单个/列表，none 不导入产品包且不启动 Worker。模板使用产品元数据；未选扩展的旧档案仍可筛选读取。共享 Worker 不领取未选择产品的任务。

Windows 启动脚本只启动 Host runner；runner 使用通用 --autostart，Wiki 扩展保持自动启动策略。现有 RAG 手动关闭与代次/锁保护保留。旧 adapters/rag.py、wiki.py 已迁移删除，维护脚本和测试导入同步更新。

## 清理与保护

已移除 AgentOS-K-core 和 AgentOS-K-rag 两个额外工作树及其可再生环境/缓存。删除前两个工作树均无未提交或未跟踪修改，且没有工作目录位于其中的运行进程。core 的独有 docs/human/README.md 已复制并核对 SHA-256；所有分支已导出且验证 Git bundle，main/rag 分支提交仍可解析。

恢复材料：var/maintenance/CORE-002/before-cleanup.bundle、cleanup.json 及保存的报告。独立 GitHub-Wiki/RAG-AGENT 仓库未修改；RAG 原有未提交改动保留。GitHub-Wiki-backups 保留，因为备份与恢复数据库不同。本轮未删除会话、日志历史、备份或旧分支。

## 验证与本地更新

46 项测试通过，ruff 与 git diff 检查通过。覆盖原 37 项行为、空扩展真实进程启动、产品包不导入、单/多产品选择、历史产品筛选、第三扩展装配、Worker 选择与任务领取；完整回执 var/verification/20260914T192459.json。

浏览器使用真实核心 + 合成第三工具 + 独立 Worker：默认关闭、开启后成功同步、刷新保持、关闭后同步按钮禁用且无新增请求/任务。fixture 位于 var/test-assets/CORE-002-browser.py，证据 var/maintenance/CORE-002/browser.json；测试进程已清理。

正式宿主更新前已作 SQLite 一致性备份，位置见 var/maintenance/CORE-002/before-live.json。通过原 Windows 持久入口重启 Host/Worker；正式浏览器 RAG 未启用、Wiki 正常，全局同步只处理已启用产品。8 条原档案内容哈希一致，RAG 任务数保持 428，Wiki 最新同步成功，见 live.json。

验证中修正了迁移导入排序和异步测试 subprocess 使用；原 37 项回归初轮已通过。没有调用真实模型、启动真实 RAG 或执行其完整检索验收。旧 RAG 依赖/质量缺口继续暂停，不推送。

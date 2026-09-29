> 历史组件设计/运维记录；其中路径已改为占位符。当前独立副本的运行和验收以仓库根 docs/DEVELOPMENT_SETUP.md 与 docs/ACCEPTANCE_STATUS.md 为准，不执行旧部署指令。

# Git 与 GitHub 工作流

## 推荐方式

正式宿主仓库为 `${WORKBENCH_SOURCE}`，独立产品为同级 `GitHub-Wiki` 与 `RAG-AGENT`。宿主在当前功能线上使用短期任务分支；旧 main/rag 分支只保留历史，不再维护产品组合版本。CORE-002 已清理两个额外工作树，恢复资料保存在 var/maintenance/CORE-002/。本轮只作本地提交，禁止推送。

`commit` 只写本地历史；`push` 才上传 GitHub；PR 创建和合并又是不同动作。当前 GitHub CLI 查询的权限包含 push/admin，但 Git HTTPS 仍未连通，且尚未检查分支保护规则，不等于已验证实际 push 成功。

## 任务流程

1. 查已有 checkout，执行 git status，确认仓库/remote/当前分支。未知修改不清理、不自动 stash。
2. 没有 checkout 才 clone；使用既有认证，不把 token 放 URL、文件或输出。先验证 WSL 的 Git 网络与认证，再 clone。
3. fetch 后从明确基线建立短期分支，例如 feat/aos-history-export。先列出已有分支避免撞名。不要在本地未提交修改上自动 pull/rebase。
4. 实现一个完整 Task，运行其 verify 并审阅 diff。文档与代码同步更新。Conventional Commits 示例：`feat(memory): add versioned history export`。
5. 授权的编码 Task 按用户严格记录要求创建本地完成提交；不把每次保存当提交，不使用无法验证的半成品充当完成。
6. push 获授权后推送任务分支，创建 PR 需在授权范围内。PR 写问题、行为变化、迁移/回退及验证证据；合并前重新确认最新提交对应 CI。
7. 合并/部署必须有明确授权，记录运行中的代码提交与 schema 版本。

历史背景（建仓前，不作为当前状态）：早期“查询并完善文档”在尚无正式 AgentOS 仓库的交付目录完成：本包有 CHANGELOG，但不假称它已经有 Git 提交。迁入正式仓库后应作为 `docs(harness)` 提交；本轮没有创建新 GitHub 仓库或推送。

## 单人双工具不混乱

默认同一 checkout 单写者，切换工具前更新交接；只有显式并行开发才分配独立 worktree/分支。一个任务一个所有者。不要让 Windows Git 和 WSL Git 同时操作同一目录；正式开发统一在 WSL。

## 跨仓库版本

AgentOS 仓库记录兼容清单：产品 URL、commit、adapter_version、contract_version、schema_version、验证日期。不使用共享源码目录或把产品复制进 monorepo。

涉及两仓库时，先发布向后兼容的 RAG 导出补丁，再启用 AgentOS 消费；分别提交并用 Task ID/PR 互相引用。Git 无跨仓库原子提交，必须记录可兼容的组合。产品升级不自动跟随 HEAD。

## 数据与历史保护

忽略 .env、.venv、PDF、SQLite 及 journal/WAL/SHM、会话原文、索引和缓存。RAG 当前忽略 memory.db 与 journal，WAL/SHM 排除需补充。仓库已有 Zone.Identifier 文件仅作为清理候选，不能混入功能补丁自动删除。

提交前审阅 staged diff 与文件清单，禁止 git add 全目录后不检查。禁止强推、reset --hard、git clean 或改写他人提交作为默认修复手段。已推送修复通常新建 revert/fix 提交；数据库回退不能只靠 git revert。

分支保护、要求 PR/CI、禁止直接改 master 为推荐 GitHub 设置；实际启用这些设置需另行授权。本轮不改远端设置。

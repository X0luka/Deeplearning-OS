> 历史组件设计/运维记录；其中路径已改为占位符。当前独立副本的运行和验收以仓库根 docs/DEVELOPMENT_SETUP.md 与 docs/ACCEPTANCE_STATUS.md 为准，不执行旧部署指令。

# WSL 运行与恢复手册

## 当前默认：桌面会话（DESKTOP-001，2026-09-19）

双击 Windows 桌面 **AgentOS** 快捷方式，打开独立 WebView2 主窗口。窗口会启动工作台、Wiki、PaperReader；重复打开聚焦已有窗口。产品页在默认浏览器中打开，关闭产品标签页不停止服务。关闭主窗口或点击首页“退出 AgentOS”，停止该会话拥有的进程并释放 WSL 连接。独立运行的产品显示“独立运行”，退出不接管或停止它们。RAG 不在桌面组合中，代码和数据保留。

旧的登录任务与 `agentos-k.service` 自启已禁用。本模式不执行 `wsl --shutdown`，也不会在后台每分钟唤醒 Ubuntu。下面旧常驻方案仅供历史/显式回滚使用，不能同时启用。

安装：Windows PowerShell 执行仓库的 `scripts/Install-AgentOS-Desktop.ps1`。脚本先编译并检查 WebView2 Runtime，再备份计划任务、unit、旧启动入口与三个项目的 SQLite，然后停用旧任务及服务、创建快捷方式。默认目录 `${WORKBENCH_SOURCE_WINDOWS}\Desktop`；依赖 .NET Framework 4.7.2+、WebView2 Runtime、已安装的 Ubuntu 与三个 Python 虚拟环境。WebView2 SDK 固定版本与 SHA256 写入构建脚本，不提交二进制。当前发行版/用户/路径在 `desktop/desktop.json`，固定为 Ubuntu/xix/AgentOS-K，无空格。命令行不携带会话凭据。

卸载桌面入口：`scripts/Uninstall-AgentOS-Desktop.ps1`。先退出主窗口；删除快捷方式，保留程序、所有数据和迁移备份，旧自启继续禁用。重复安装保留首次迁移基线。

显式回滚：`scripts/Uninstall-AgentOS-Desktop.ps1 -Rollback -Backup <首次迁移目录>`。恢复旧任务 XML、unit 启用状态和旧启动脚本，不覆盖数据库。旧常驻方案有 WSL 空闲退出的已知限制；回滚不代表修复这一限制。数据库恢复是单独维护动作，不能用覆盖当前数据库作为卸载步骤。

诊断：

- Linux 会话事件：`var/desktop/session.log`；产品与 Worker 日志：`var/logs/`。
- Windows 外壳错误：`%LOCALAPPDATA%\AgentOS\logs\`；从封装应用启动时 Windows 可能重定向此路径到该应用的 LocalCache。
- 迁移备份：`${WORKBENCH_SOURCE_WINDOWS}\migration\`；数据库快照：`var/maintenance/DESKTOP-<时间>/`。
- 产品失败时首页显示错误并允许“重试”；端口身份冲突不终止占用者。工作台 7777 已有实例时桌面显示冲突，应正常退出旧工作台。
- 忙碌状态来自产品 `/health.busy`。退出前可等待、确认停止或取消；未知状态按忙碌提示。等待期间可取消等待。确认停止仍先发送正常退出信号，每服务最多 30 秒，再校验进程组身份后强制结束。
- 外壳崩溃后管道 EOF 触发清理。下次启动在独占锁内核对 session/PID/启动标识/boot ID/进程组；不能仅凭端口或旧 PID 终止进程。
- `scripts/Observe-AgentOS-Desktop.ps1` 从 Windows 连续观察三个身份端点与网页；不调用 WSL，避免观察命令本身维持 Ubuntu。

浏览器普通访问不持有退出凭据，不能通过 HTTP 关闭桌面会话。桌面凭据仅在私有标准输入管道中传递；主窗口仅接受自身工作台来源的退出消息，产品网页在系统浏览器中打开。

> 当前规则（2026-09-14）：RAG 默认关闭，仅手动启用后允许产品活动；以本文 RAG-SWITCH-001 节为准。旧 RAG 专属未验收事项暂停，历史证据保留。

## 当前环境与部署决定

证据见 [环境报告](evidence/environment.md)。Ubuntu 24.04.4 LTS、systemd、Git/Python3/uv/Node 存在。尚未查询具体工具版本、Docker 引擎就绪情况、端口占用或用户级 cgroup 限制生效；部署前必须验证。

正式目录：`${WORKBENCH_SOURCE}` 与 `${LINUX_USER_HOME}/code/RAG-AGENT`。各自虚拟环境。现有 RAG 开发模式为 Qdrant 容器 + 本地 FastAPI/Streamlit，优先验证这一模式；不能假定 Docker Desktop 存在就等于 Ubuntu 的 Docker 集成可用。若采用原 Compose，同样先限制暴露地址和验证资源。

## 开工检查顺序

1. WSL 中检查 git/python3/uv 版本、仓库候选路径、已有修改、端口与实际常驻进程。
2. 验证 WSL 到 GitHub 的连通与认证。Windows gh API 成功不证明 WSL git 凭据可用。不得复制 token 到文档或不加检查地修改代理。
3. 克隆或复用完整仓库，核对基线 commit。API 快照没有 .git，不作为可提交代码工作区。
4. 读取仓库规则，创建任务分支，检查 uv.lock 与 Python 兼容性。安装动作须在开发阶段授权后进行。
5. 配置真实密钥时只使用本地受限配置，不读取/展示用户密钥。无真实调用授权时使用隔离测试替身，确保不会联网消费。
6. 启动 Qdrant 与产品，检查 health 内容、日志与 UI；再起 AgentOS。缺依赖应明示，而非显示绿色。

## 已有运行入口（来源于代码/README，尚未本机执行）

从 RAG 根目录分别运行：

```bash
# 先完成联网/安装授权、配置校验、端口与资源检查
docker compose up -d qdrant
uv sync --frozen --extra dev
uv run uvicorn src.api.main:app --host 127.0.0.1 --port 8000
# 另一终端，相同 RAG 根目录
uv run streamlit run ui/app.py --server.address=127.0.0.1 --server.port=8501
```

这里 --frozen 是部署可复现要求；现有 CI 用 uv sync --extra dev。锁文件不匹配应报告，不能在安装过程中悄悄升级依赖。上述 Qdrant Compose 端口原样会发布到所有地址；正式启动前通过经过审阅的本地 Compose override 绑定到 127.0.0.1，确认访问范围。

API 与 UI 都以同一 RAG 工作目录启动，确保相对 data/raw 可见。不要只设置 DATA_DIR 就宣称所有路径已迁移：config 中 raw_dir/db_path/bm25_index_path/log_path 各自独立，UI 还写死了 data/raw。首版维持现状并登记目录，集中迁移另做任务。

## 数据与备份清单

| 数据 | 归属 | 备份要求 |
|---|---|---|
| RAG data/memory.db | 产品历史与文档元信息 | 受控停写或 SQLite 一致性备份 |
| RAG data/raw | 原始 PDF/资料 | 备份原始版本，不能随索引清理 |
| data/processed/bm25.pkl | 派生索引 | 可重建；保存可减少恢复成本 |
| qdrant_storage 命名卷 | 产品向量与 payload | 明确卷位置；一致性快照或停服备份，或有成本预算的重建 |
| AgentOS 档案/游标/实例身份 | 归档系统 | 同一一致性恢复集合 |
| 配置与密钥 | 各产品 | 配置模板入 Git，真实密钥独立安全保存 |
| Git 代码与 uv.lock | 各仓库 | 推送后远端保存；未提交内容不由 GitHub 自动保护 |

GitHub 代码仓库不替代会话和 PDF 备份。独立备份目标尚未确认，不能宣称具备整盘故障恢复能力。

第一版受控停写备份：暂停同步与新写请求 → 等待/停止写任务 → 生成 SQLite 一致性副本及文件清单 → 备份原始资料和需要保留的索引 → 校验 → 恢复服务。不要直接复制活动 SQLite 单个 db 文件，也不要在线随意复制 Qdrant 卷当作一致快照。

日志初始保留 30 天，问答档案默认长期保留。备份初始建议 7 日/4 周/3 月；未验证可恢复前不清理唯一旧副本。关机错过备份，下一启动补做并显示最近成功时间。

恢复在隔离数据目录验证，不覆盖现有数据：还原 → 校验行数与文件 hash → 核对 source instance/epoch → 服务启动 → 历史读取 → 同步重放去重。若产品源库回滚，按导出契约更换 epoch 并执行重新对账，不能继续旧游标。

## 常见异常

- health 返回 degraded：检查 checks 字段；配置缺失与不可达分开处理。
- 上传后找不到文件：检查 UI/API cwd 或共享卷，不能直接扩大允许路径范围。
- HTTP 超时：先检查是否生成了 interaction，不自动重复收费查询。
- 历史有空答案：当作待确认状态，不能自动填入成功内容。
- WSL 内存不足：收紧任务/常驻进程，再评估配置修改；不要直接照抄 8GB 上限。
- Git API 可读但 git clone 失败：分别诊断目标域名、代理与认证；不修改全局网络配置碰运气。

## 统一入口与当前限制

目标见架构与 T09：只启动 AgentOS，再由控制台启动并打开产品。当前 serve 启动 Host（7777）并自动管理 Worker；打开产品时依次检查 Qdrant（6333）、API（8000）和网页（8501），必要时启动并等待就绪。本文分终端命令为开发诊断步骤，不是最终产品流程；favicon.ico 404 不影响入口，8501 连接拒绝需检查原网页服务。隔离启动和浏览器入口已验证，完整生产入口仍需 Docker/Qdrant 配置与真实产品验证。

当前工作目录使用 WSL 正式仓库及各自 .venv，Windows work/ 不用于运行产品。复跑真实验收需沿用已获准外发范围和加载密钥的环境；读取已有报告无需再次收费调用。

### T09 实际启动与故障处理

只需在 AgentOS 根目录运行 `.venv/bin/python -m aos.bootstrap serve`，现有旧 Host 进程需先退出再按新代码启动。原网页与 API 以 RAG 根目录和其 .venv 启动；可用 --rag-dir 指定路径。子进程沿用当前环境，API 的 QDRANT_URL 固定为本机 6333，UI 的 API_BASE 指向登记 API，本机 HTTP 绕过代理；不自动填充提供商密钥或发起问答。

Qdrant 离线时只启动已经部署的 rag_memory_qdrant 容器；Docker 不可用、容器缺失、镜像名称或 loopback 端口配置不符时，等待页显示原因。禁止通过删除/重建卷排错；原 Compose 的全地址端口不满足本机限制。当前 WSL Docker 集成未就绪，生产容器配置修正仍待执行，不能把该状态写成已成功启动产品。

启动超时/退出检查 `<data-dir>/logs/rag_api.log` 或 rag_ui.log，日志权限为 0600，不直接显示原始日志到网页。失败启动只终止本次新建且身份一致的进程；已成功启动的上游服务保留供重试。正常关闭 Host 仅停止自身 Worker；外部 Worker 不停止，产品进程保持运行。Worker 每 5 秒检查，失败后 15 秒重试。产品退出后再次点击打开会重新检查和启动。

复用手动启动的 Streamlit 需核对工作目录、ui/app.py、显式端口和实际监听进程；身份不明时报告端口冲突。暂不提供产品停止按钮；产品进程管理记录不等同于资源限额或完整灾备。

浏览器隔离检查入口为 scripts/runtime_browser_check.py，需要临时 Playwright/Chromium 和相邻 RAG 的 Streamlit 环境；仅启动合成本机 API/网页，不调用提供商。该检查验证控制台启动与跳转，不替代真实 RAG 上传/检索验收。

### 真实配置启动失败（2026-09-08 已诊断，修复待实施）

用户终端显示 Settings 在导入时缺 aihubmix_api_key、cohere_api_key、langfuse_public_key、langfuse_secret_key，API 尚未监听就退出；随后 Streamlit 请求 /api/history 返回连接拒绝。当前 RAG 根目录无 .env；环境中存在其他字段不证明其凭据有效。

原版配置把上述字段全部设为必填，embedding/rerank 客户端也在模块导入时创建。不能通过随意填空或假密钥宣称可用。拟修复为基础服务可启动、能力分别校验和延迟初始化、Langfuse 显式可禁用；网页捕获 API 不可达，宿主预检汇总缺失项并脱敏。该方案尚未实施。

完整原版检索仍需有效 AIHubMix、Cohere、生成提供商和 Qdrant；Langfuse 只负责观测，不应强制要求用户启用外发。已有 DeepSeek 测试许可不扩展其他服务。

Docker 命令仍返回 WSL 未启用集成。Docker Desktop 启动后，在 Settings → Resources → WSL Integration 为实际使用的发行版开启集成并 Apply；随后核查现有 Qdrant 容器和本机端口，保留数据卷。依据：[Docker 官方说明](https://docs.docker.com/desktop/features/wsl/)。全局配置本轮未更改。

## RAG-SWITCH-001 使用与更新
Host 每次启动，RAG 卡片显示“未启用”。点击“启动 RAG”开关后才检查依赖，失败按页面原因处理并手动重试。就绪后可打开原网页并自动归档。点击关闭开关暂停接入，等待“已关闭”；已就绪 RAG 服务仍可独立运行，旧档案随时可读。刷新不复位，重启 Host 会关闭；Wiki 行为不变。

更新前使用 SQLite 一致性备份保存宿主 archive.db；只更改宿主活动管理表。重启旧 Host 与其 Worker，使新门控同时生效，保留 Wiki 和独立产品服务。验证 RAG 默认为关闭且无新增任务。不要直接回退到会自动同步 RAG 的旧版本；需要回退时先停宿主并保留备份，记录行为差异。Windows 脚本只是 WSL 入口，Wiki 由宿主启动。

## CORE-002 单主线核心与扩展（2026-09-15）

日常使用正式 AgentOS-K 目录。--products all 为默认多产品模式；--products none 为无扩展档案模式；--products wiki --autostart 只加载并自动启动 Wiki；--products rag 仍须在界面手动开启。Windows 脚本只启动宿主 runner；runner 传通用 --autostart，由扩展决定策略。

本次清理的工作树为 AgentOS-K-core 与 AgentOS-K-rag；main/rag 历史分支保留，不是当前运行版本。Git bundle、core 独有报告和清理哈希清单位于 var/maintenance/CORE-002/。必要时使用 git worktree add <新目录> <历史分支> 恢复代码，虚拟环境按锁文件重建。本次保留 GitHub-Wiki-backups 的备份与恢复目录，因为数据库内容不同。

更新前 SQLite 快照位于同一维护目录，见 before-live.json；本轮不改变产品数据或数据库 schema。仅重启 Host/Worker，独立产品保留。旧版本回退会失去新扩展选择，回退前停止宿主并核对备份。

## RUN-RECOVERY-001（2026-09-17）
AgentOS 与 Wiki 位于 WSL，发行版停止时两个网站都会不可访问。当前使用两层管理：`agentos-k.service` 由 systemd 直接监督现有 `aos.local_runner`，失败后 5 秒重启；Windows 任务 `AgentOS-K Startup` 仅在当前用户登录时执行一次。S4U 非交互令牌实测无法启动当前用户的 WSL 发行版，因此任务使用 Limited/Interactive 令牌，通过 GUI 子系统的 `wscript.exe` 以窗口样式 0 调用 `wsl.exe -d Ubuntu -u root --exec /usr/bin/systemctl start agentos-k.service`。WSL root 仅用于控制该 systemd unit；服务进程仍按 unit 配置以 `xix` 运行。没有每分钟轮询，也不创建 PowerShell、cmd 或 Windows Terminal 窗口。

Windows 安装入口仍为 `Install-AgentOS-Recovery.ps1`。安装器会导出旧 `AgentOS-K Local Recovery` XML、禁用旧任务、停止旧 runner、安装并启动 systemd unit，验证身份端点后才删除旧任务。`Start-AgentOS-Task.vbs` 仅承担无窗口启动，不做健康轮询。`Uninstall-AgentOS-Service.ps1` 停止并移除 unit 和新任务；安装目录存在备份 XML 时，同时恢复旧任务。版本源文件位于 `scripts/` 和 `systemd/`。

常用操作：

```bash
sudo systemctl status agentos-k
sudo journalctl -u agentos-k
tail -f ${WORKBENCH_SOURCE}/var/logs/host.log
sudo systemctl start agentos-k
sudo systemctl stop agentos-k
sudo systemctl disable --now agentos-k
sudo systemctl enable --now agentos-k
```

`systemctl stop` 是临时停止，下一次 WSL 启动仍会按 enabled 状态启动；长期维护使用 `disable --now`。人工执行 `wsl --shutdown` 后不会由 Windows 后台轮询重新唤醒；下次 Windows 登录、手动运行启动入口或主动进入 WSL 时，已启用的 unit 会再次启动。Windows 关机、睡眠或注销期间不提供网页，也不承诺浏览器请求零中断。


## LLM-UI-001 交接（2026-09-20）

API 与模型设置已接入 AgentOS；正式 Reader/Wiki 使用 managed-v1，旧独立配置保留。全局 Custom、产品/任务默认和会话覆盖已实现。新增模型从连接目录选择，未知特殊能力须关闭或确认。完整验证、数据恢复位置与限制见 AgentOS-K/docs/plans/runs/LLM-UI-001.md；当前本地产品报告见 docs/human/README.md。工程回归 200 项通过，无真实生成、无推送；历史语义质量结论不变。平台配置备份包含 var/llm/config.db 与 private，必须私有保存。


## DATA-001 数据中心代码回退
开发分支 codex/data-knowledge-center，基线 a3cd72bc6d6a686858fbde7434447698790601f3，回退分支 rollback/data-center-baseline-20260922。源码 bundle、archive-baseline.db 与恢复验证回执位于 var/maintenance/DATA-001，均仅本地保存。
本批没有 schema 变化；无需恢复旧数据库。先正常退出 AgentOS 桌面会话，确认无产品任务及工作区未提交修改，再在正式 WSL 仓库执行 `git switch rollback/data-center-baseline-20260922`，重新打开既有桌面快捷方式。切换回 `codex/data-knowledge-center` 后同样重开即恢复新入口。发现未提交修改时先保留和审阅，不执行 reset --hard 或强制覆盖。新版本期间新增的档案保留，已在隔离副本验证旧版可读。
数据库备份用于独立恢复事故，不用于日常界面回退。需要恢复时按原有一致性恢复流程处理，不能覆盖当前唯一副本。


## DATA-002 V0.1：手动归档与真实认知地图（2026-09-29）

本节覆盖本文旧“自动管理 Worker / 自动归档”的运行说明。正式桌面已改为不启动归档 Worker 和轮询守护。新助手直接读取 `var/assistant/assistant.db`；旧产品记录保留于 `var/archive.db`。`/data` 默认打开地图，“刷新列表”只查询，“更新旧档案”执行一次增量归档。每产品 120 秒超时，重复请求复用活动任务；应用正常关闭会中断活动归档，任务结束后释放锁和停止页面任务查询。硬中断遗留的 running 任务在下次手动更新持有锁时标记 interrupted，不自动重放。

兼容的显式 `aos.bootstrap worker` 命令仍保留，只供主动运维；手动更新与它共用 worker.lock，发生冲突报告 archive_worker_busy。应用和产品健康检查仍运行，不宣称没有任何后台活动。旧源停用时不参与手动更新，已有归档仍可查询。

地图快照保存 `var/data-center/map.json`，只含候选主题、记录身份、来源摘录与更新时间。新记录进入“尚未整理”，来源变化提示复核，删除会话不会从导入副本复活。当前生成过程为本轮本地整理，不是已交付的自动 AI 整理接口。新 API `/api/v1/data/items` 统一查询，原 `/api/v1/data/records` 继续提供旧归档兼容视图；两者总数不应强求相等。

恢复：先正常退出宿主；备份位于 `var/maintenance/DATA-002-20260929`，有修改前源码和 SQLite 一致性备份。优先撤回本任务的源代码增量，并保留现有业务库和新增会话。不得用旧库覆盖当前库；不得整体恢复源码目录覆盖同时进行的助手/导航修改。地图可独立备份/替换，不影响原问答。撤回同步改动会恢复旧定时同步行为，应明确记录差异。

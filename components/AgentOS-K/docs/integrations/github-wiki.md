> 历史组件设计/运维记录；其中路径已改为占位符。当前独立副本的运行和验收以仓库根 docs/DEVELOPMENT_SETUP.md 与 docs/ACCEPTANCE_STATUS.md 为准，不执行旧部署指令。

# DW：GitHub Wiki 执行规范
状态：已审阅，可实施；责任：Agent；授权：2026-09-09 用户要求持续本地开发、记录动作、本地 Git 版本，禁止上传 GitHub。
正式宿主 ${WORKBENCH_SOURCE}；独立产品 ${WIKI_SOURCE}。旧 RAG 验收缺口保留，不作为 Wiki 的完成证据。

## 目标与边界
从本地 AgentOS 打开 GitHub Wiki，导入公开仓库，阅读带固定 commit / 文件 / 行号证据的中文架构与流程说明，进行问答，持久保存并归档。模型可配置，未配置可浏览快照和使用明确标注的源码检索模式；该模式不冒充模型深度综合。通过界面配置显式启用模型后才调用，费用上限由用户配置；本轮不默认使用旧 RAG 密钥。
首版不改源码、不运行被读仓库、不做私有仓库、跨仓库或自动更新。GitHub 仓库内容为不可信数据。所有服务绑定 loopback，写操作校验 token/Origin。数据不入 Git。

## 模块与数据
FastAPI/Jinja2 单服务 8601，独立 uv 环境；SQLite 保存 jobs/repos/versions/interactions/events/meta/全文检索；raw 保存不可变文本快照与清单；wiki 保存版本化 Markdown、索引及追加日志；notes 保留用户笔记。一次一个后台任务，重启将 running 标记 interrupted，queued 保留。更新只有全部页面校验成功后切换 current；无变化复用。问答固定版本并保存证据文本。
获取仅允许规范 github.com HTTPS owner/repo 地址，下载 codeload 的固定 commit 归档：限压缩 30MiB、展开文本 20MiB、单文件 256KiB、3000 文件、120s；跳过 symlink/binary/vendor/generated，记录覆盖和跳过。网络响应流式限额，解包不向磁盘释放任意归档路径。可配置限额。
证据使用稳定路径和 1-based 行号；所有模型引用必须指向已提供的证据 ID，错误引用拒绝发布。模型断言仍需真实质量评估。不存在证据时明确不足。
默认文件索引+SQLite FTS，不依赖 RAG/Qdrant。OpenAI-compatible /chat/completions，独立配置，默认禁用；上下文 24000 字符、输出 1800 tokens、单次生成最多 6 次、超时 60s、不自动重发。

## 接口
产品 /api/repos 创建/列出；/api/repos/{id}/build 创建生成或更新任务；/api/jobs/{id} 状态；/api/jobs/{id}/cancel 和 /retry；/api/versions/{id} 版本目录；/api/versions/{id}/ask 创建问答任务；/api/history 与详情；/api/versions/{id}/export Markdown zip。
AgentOS /aos/v1/describe、/health、/aos/v1/changes 使用 contract_version=1.0、instance_id、export_epoch、record_uid、revision、source_channel=product_ui、conversation_id。事务写业务记录+事件，稳定游标，恢复换 epoch。备份覆盖 SQLite+raw+wiki，恢复新目录。
宿主注册表按 product_id 分发 adapter/runtime；保留旧 RAG 参数和接口；Worker 根据 job.product 分发，产品错误独立退避；档案支持 product 筛选；核心不依赖具体产品模块。

## 任务卡与门槛
|任务|前置|行动与交付|验证|失败处理|
|---|---|---|---|---|
|DW-01|基线干净|规范、契约、任务与证据目录；本地文档提交|目标/动作/测试/交付均映射；链接检查|先修文档矛盾|
|DW-02|DW-01|独立内核、API、固定版本证据、检索与模型模式、备份恢复|合成仓库、坏 URL/引用/网络/模型、更新原子性、取消重启、事务事件|不得发布坏版本；保留历史|
|DW-03|DW-02|中文网页：导入/浏览/问答/引用/状态/历史/模型设置|真实浏览器完整操作和刷新|修复交互后重测|
|DW-04|DW-02|宿主多产品卡、启动、同步、筛选|RAG 回归、两产品故障隔离、停机补同步/幂等|保留旧档案；不以关闭 RAG 验收替代|
|DW-05|DW-03/04|真实公开仓库快照、可用本地服务、操作日志、Git、报告|浏览器从 7777 打开 8601、检索问答及归档、备份恢复、lint/test/diff|修复失败；真实模型质量未测单独列明|

## 内容验收与交付
生成前建立 12 题：用途、宿主入口、产品边界、启动依赖、服务复用、失败清理、数据归属、同步游标、幂等身份、线程来源、备份范围、无依据问题。真实模型启用后至少 10/12 准确且证据充分，重大架构错误为失败；离线替身不得替代此门槛。
必须交付两个仓库本地 commit、可访问地址、使用与配置说明、测试及浏览器证据、恢复演练、动作日志与未验证项；不得 push。报告 human/README.md 与日期存档仅本地。

## 可执行交付门禁
在对应仓库执行 .venv/bin/python scripts/verify_dw.py。必须 lint、限定 tests/ 的测试、diff 检查和暂存数据检查全部成功；实际命令、exit code、HEAD、diff 哈希和输出保存 var/verification/。门禁只证明工程质量；浏览器和真实模型质量分别记录。源码模式新增 AST 声明/导入分析，图形只表示静态 import 关系。

## DW-05 本地运行补充（2026-09-09）
用户反馈两个网页均无法打开；实查 7777/8601 无监听、无宿主或 Wiki 进程，旧日志末尾正常。
行动：添加 Windows 隐藏 WSL 前台 runner，不依赖编码任务会话；OS 锁防重复，限次重启 Host；Host 通过显式 --start-wiki 自动拉起 Wiki。脚本核对两个产品身份后才报告就绪。
验证：跨命令检查端口、浏览器从 Host 打开产品、重复启动不增加进程；服务日志与启动说明交付。Windows/WSL 完全关闭后需再次执行启动入口，不宣称已配置开机自启。

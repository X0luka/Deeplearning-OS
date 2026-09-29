# RAG-AGENT 接入审阅与实施映射

## 固定来源

- 仓库：[X0luka/RAG-AGENT](https://github.com/X0luka/RAG-AGENT)，公开，默认 master。
- 审阅提交：`642b0f582f5683522ddb56601613533b446fdc8b`，2026-09-06 通过 GitHub CLI API 获取。
- [该提交 CI](https://github.com/X0luka/RAG-AGENT/actions/runs/31892550797)：completed/success；不是本机运行证据。
- 获取方式：固定提交 contents API，保存在工作区审阅快照；未 clone .git，也没有通过 Git 传输校验完整 checkout。

## 已确认源码事实

| 事项 | 事实及源码 |
|---|---|
| 运行栈 | Python >=3.11，CI/Docker 使用 3.12；uv + uv.lock。[pyproject](https://github.com/X0luka/RAG-AGENT/blob/642b0f582f5683522ddb56601613533b446fdc8b/pyproject.toml) |
| 网页 | Streamlit Chat/History/Documents；API_BASE 默认 localhost:8000。[UI](https://github.com/X0luka/RAG-AGENT/blob/642b0f582f5683522ddb56601613533b446fdc8b/ui/app.py) |
| API | FastAPI，/api 前缀；独立 /health。[main](https://github.com/X0luka/RAG-AGENT/blob/642b0f582f5683522ddb56601613533b446fdc8b/src/api/main.py) |
| 历史 | SQLite + async SQLAlchemy，交互记录与文档表。[models](https://github.com/X0luka/RAG-AGENT/blob/642b0f582f5683522ddb56601613533b446fdc8b/src/memory/models.py) |
| 检索存储 | Qdrant + BM25 pickle，SQLite 仅文档元信息/历史。保留既有架构 |
| 模型依赖 | DeepSeek/OpenRouter 生成，AIHubMix embedding，Cohere rerank，Langfuse tracing；是代码配置，不保证当前凭据可用。[config](https://github.com/X0luka/RAG-AGENT/blob/642b0f582f5683522ddb56601613533b446fdc8b/src/config.py) |

## 现有 API：可直接适配

| 方法 | 路径 | 实际含义 |
|---|---|---|
| GET | /health | Qdrant/SQLite 检查和部分凭据 configured 状态；degraded 仍 HTTP 200 |
| POST | /api/query/sync | JSON：query、include_history、history_window、use_cheap_model；返回 answer/citations/interaction_id/latency_ms/cost_usd |
| POST | /api/query | SSE：start、delta、citations、done、error；不是 JSON 响应 |
| GET | /api/history?page=1&page_size=20 | 倒序交互摘要，items/total/page/page_size |
| GET | /api/history/{interaction_id} | 完整交互字段及 retrieved_chunks 元数据 |
| GET | /api/documents | 文档列表与总数，可供卡片使用 |
| POST | /api/feedback | 修改指定交互反馈，现有无更新游标 |
| POST | /api/ingest | JSON path/source_type/force；读取服务端文件，不是 multipart 上传 |
| DELETE | /api/documents/{id} | 删除文档索引/元数据，不作为 AgentOS 默认动作 |

来源：[query](https://github.com/X0luka/RAG-AGENT/blob/642b0f582f5683522ddb56601613533b446fdc8b/src/api/routes/query.py)、[history](https://github.com/X0luka/RAG-AGENT/blob/642b0f582f5683522ddb56601613533b446fdc8b/src/api/routes/history.py)、[ingest](https://github.com/X0luka/RAG-AGENT/blob/642b0f582f5683522ddb56601613533b446fdc8b/src/api/routes/ingest.py)。

上传发生在 Streamlit：文件写入相对路径 data/raw，再向 /api/ingest 传路径。API 和 UI 必须共享同一工作目录/数据挂载，否则 UI 上传成功但 API 找不到文件。原 Compose 通过共同挂载 ./data:/app/data 实现共享；本地模式两者都应从 RAG 根目录运行。

## 接入前必须解决的差距

| 级别 | 发现 | 最小方案 |
|---|---|---|
| 必需 | 无网页来源和线程 ID | 加 source_channel/conversation_id，原 UI 传标识；旧记录标 unknown |
| 必需 | 占位后更新、反馈覆盖，倒序分页不稳定 | 事务变更日志与游标导出；不只拉最大 ID |
| 必需 | _safe_finalize 吞写入异常仍可返回 done/成功 | 成功必须有持久化依据；失败显式标识，测试写库故障 |
| 必需 | 检索发生在占位之前 | 请求进入后先记录，使检索失败也可归档 |
| 必需 | 断流/取消的持久化无明确保证 | 增加状态与取消测试；不可声称已保留完整失败会话 |
| 必需 | 全局最近历史影响所有请求 | 新线程按 conversation_id 取上下文 |
| 必需 | 历史未存当时引用文本/页码 | 新记录保存可见引用快照；旧记录标缺失 |
| 部署必需 | /ingest 可接系统路径；Compose 端口默认非 loopback 限定，CORS 通配 | 本地监听与路径根校验；写接口的本地访问防护 |
| 运行必需 | UI 可绕过 AgentOS 执行 ingest | 标明非统一调度；产品整体资源限制，不伪称单任务租约保护 |

持久化行为依据：[store](https://github.com/X0luka/RAG-AGENT/blob/642b0f582f5683522ddb56601613533b446fdc8b/src/memory/store.py)、[generation/stream](https://github.com/X0luka/RAG-AGENT/blob/642b0f582f5683522ddb56601613533b446fdc8b/src/generation/stream.py)。风险是静态代码发现，尚未故障复现。

## 配置与外发

Settings 中六类凭据字段无默认值，启动导入就会校验；不能写成“没密钥也能正常运行产品”。测试可用隔离占位值，但不能误调用供应商。AgentOS 自身缺模型凭据仍应可启动，产品错误以 degraded 展示。

生成客户端使用 langfuse.openai 包装，存在 tracing 外发路径。首次真实 PDF 运行前确认模型/embedding/rerank/tracing 的外发范围及费用；不声称经过 AgentOS 就能拦截原网页的独立网络请求。是否禁用 tracing 须依据锁定版本验证，不能捏造环境开关。

## 拟修改文件范围

RAG：src/memory/models.py、store.py、迁移实现；src/api/schemas.py、routes/history.py 或新的 aos 路由；routes/query.py、generation/stream.py；ui/app.py（标识与历史恢复）；tests 的持久化/导出/断流测试；对应 docs/task、.gitignore，必要的本地部署配置。

AgentOS：独立 RagHttpAdapter、档案存储、同步 Worker、只读历史与状态页、契约测试。不要把 RAG src 整体装入 AgentOS 环境。

## 既有规约

RAG [docs/00-conventions.md](https://github.com/X0luka/RAG-AGENT/blob/642b0f582f5683522ddb56601613533b446fdc8b/docs/00-conventions.md) 约束 Task/verify、Conventional Commits 和依赖变更。其任务路径写 tasks/，实际目录为 docs/task/；以实际文件核对，不创建重复任务体系。本轮未改该规约。

## 已有测试的实际范围

[CI](https://github.com/X0luka/RAG-AGENT/blob/642b0f582f5683522ddb56601613533b446fdc8b/.github/workflows/ci.yml) 跑 ruff、test_chunker/test_bm25/test_hybrid 及 golden_set JSON 语法校验。配置使用占位凭据。没有由这些测试证明 API、数据库迁移、会话同步、取消恢复或真实 PDF 回答效果。新的验收必须补充，不能只引用现有绿色 CI。

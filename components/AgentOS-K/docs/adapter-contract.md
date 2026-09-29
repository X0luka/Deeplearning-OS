# 统一产品适配契约 v1（拟实施）

## DESKTOP-001 增量

桌面会话可选择 SessionProcesses，普通 CLI 仍使用原监督器。runtime 增加可选 ownership（starting/running/external），failed 继续使用既有 state/error。桌面组合强制启动所选扩展，不改独立产品启动规则。

产品 `/health` 可附带 `busy: bool`，表示 queued/running 任务；未知/无法获取状态在退出前保守提示。没有新增业务写接口、没有更改历史导出契约版本。桌面启动、状态、退出控制采用私有 stdin/stdout JSON 管道，每次启动新凭据校验；无网络监听、无网页可读取的退出令牌。EOF 触发与显式退出相同的归属清理。

> 当前规则（2026-09-14）：RAG 默认关闭，仅手动启用后允许产品活动；以本文 RAG-SWITCH-001 节为准。旧 RAG 专属未验收事项暂停，历史证据保留。

本契约是 AgentOS 侧接口。RAG 已有端点映射见 [接入审阅](integrations/rag-agent.md)；下面 `/aos/v1/` 为拟新增能力，不能当作现有 API 调用。

## 最小适配接口

```text
describe() -> ProductDescriptor
health() -> HealthReport
list_history(page, page_size) -> HistoryPage       # 兼容/诊断能力
get_interaction(source_id) -> InteractionEnvelope
export_changes(cursor, limit) -> ChangeBatch      # 可靠归档必需
invoke(operation, input, request_id) -> Result    # 有调用能力才提供
```

ProductDescriptor：product_id、instance_id、contract_version、source_commit、capabilities、ui_url、observed_at。instance_id 在首次配置时产生并持久化；产品复制为新实例时重新分配，不能仅用产品名去重。

HealthReport：reachable、status、checks、observed_at、stale、reason。HTTP 200 不等于全部依赖健康。读取 health 超时默认 3 秒且缓存，不阻塞首页；实现时按实际响应校准。

能力可选：health、query_sync、query_stream、documents_list、history_read、history_changes。缺少某项返回 unsupported，不要求所有产品实现 PDF 功能。连接错误与不支持分开表示。

## 调用映射与错误

AgentOS 初次联调优先调用 POST /api/query/sync，因为 JSON 结果容易核验。流式 `/api/query` 为 SSE，不能按普通 JSON 响应解析。两者需要计费/外发授权与配置，文档查询不会自动触发真实问答。

查询参数 query/include_history/history_window/use_cheap_model 按产品 schema 校验。诊断默认 include_history=false，避免现有全局历史污染；source_channel 在产品支持后标 agentos_probe。

错误归一化：unreachable、timeout、invalid_input、unsupported、provider_error、persistence_error、cursor_expired、schema_incompatible。保留 request_id 与非敏感来源错误码。查询无幂等支持时，超时不得自动重发：服务端可能已经完成并计费，应先核对历史。

## 拟新增可靠导出

```text
GET /aos/v1/changes?cursor=<opaque>&limit=100
→ {contract_version, instance_id, export_epoch,
   events: [{event_id, revision, operation, interaction}],
   next_cursor, has_more}
```

event_id 为单调提交序号；cursor 不透明，绑定实例和 export_epoch。每次创建、完成、失败、反馈变更在同一 SQLite 事务追加完整记录事件。导出只包含已提交事件，按 event_id 排序，不使用当前页偏移量作游标。

每个 interaction 包含稳定 record_uid，备份恢复后仍用于识别同一记录；epoch 只划分事件流代际，不能单独用它生成新的会话身份。回滚对账规则见 [会话数据](conversation-data.md)。

若先筛选来源，next_cursor 必须仍推进到已扫描的全局事件位置，空页不能造成无限循环。未知主版本拒绝并保持旧档案；可忽略明确可选的新字段。

归档事务先写事件去重记录与归档版本，再写 cursor，一次提交。进程崩溃后重读同批可幂等；禁止先保存游标。批量与单条响应设置大小上限，大附件用引用/独立下载，不塞入事件。

首版产品变更日志不自动截断；后续清理须引入消费者确认或版本化快照。cursor 失效返回明确错误；新快照含一致性高水位，完成导入后从该水位续读。数据库回滚/从旧备份恢复时更换 export_epoch，不能复用游标导致 ID 冲突。

事件涉及删除时仅标记源端删除，不默认物理删除 AgentOS 档案。真正擦除归档须有用户授权和备份保留策略联动。

## 最小宿主 API（拟实施）

GET /api/v1/apps、GET /api/v1/apps/{id}/health、POST /api/v1/apps/{id}/sync、GET /api/v1/history、GET /api/v1/history/{archive_id}、POST /api/v1/apps/{id}/invoke、GET /api/v1/jobs/{id}。

读接口分页且有限额；写接口校验本地会话/Origin；invoke 操作名必须来自注册表，禁止任意命令、URL 和系统路径。长期会话原文不写入常规诊断日志。

## 本机生命周期管理（T09）

进程监督由宿主 ProcessSupervisor 端口负责，独立于产品 HTTP 业务接口。POST /api/v1/apps/{id}/start 校验写令牌与 Origin，返回 202 后在后台启动；GET /api/v1/apps/{id}/runtime 返回 idle/starting/ready/failed、非敏感错误码与 Worker 状态。GET /products/{id}/open 只呈现等待页，由页面发起受保护的 POST 并轮询；就绪后保留宿主页面，显示指向登记本机 URL 的新标签页链接。启动接口不接受任意命令或路径，重复请求合并。


## DW 增量要求
新增独立 GitHub Wiki 产品；范围、模块、版本化接口和验收依照 [Wiki 规范](integrations/github-wiki.md)。既有 RAG 行为和数据保持兼容。

## RAG-SWITCH-001 接口增量（2026-09-14）
产品公开 HTTP 契约不变。Host 的 POST /api/v1/apps/{id}/start 为显式手动启用入口；新增 POST /api/v1/apps/{id}/pause，均检查现有写令牌与 Origin，后台处理并返回 202。runtime 增加 disabled/stopping 状态及 manual_activation 标志，保留 starting/ready/failed。重复请求幂等合并，stopping 中 start 返回 409 product_stopping。

手动产品非 ready 时，health 返回本地状态及 reachable=false，不探测远端；sync/invoke 返回 409 product_disabled，不入队。关闭与入队竞争由存储事务裁决。已登记列表仍包含关闭产品，附带活动状态；历史读取不依赖活动状态。首页全局同步仅向可同步产品发请求。已有 opening 页面仅 Wiki 等原自动产品保持自动启动脚本，RAG 必须明确点击。

## CORE-002 单主线核心与扩展（2026-09-15）

扩展在 aos/integrations/ 的本地子包实现 configure(parser) 与 create(args) -> Integration | None。Integration 包含 product、client、adapter、services、supervisor、manual_activation、autostart、preferred、startup_errors。不得导入独立产品私有模块。具体 CLI 配置、依赖探测与错误解释留在扩展；共同历史导出/版本校验可复用 adapters/history.py。

Host 既有 API 保持，产品列表来自所选扩展；none 时 apps 返回 []，历史列表与详情仍可读，页面保留历史产品筛选。启动拒绝未知产品，网页不加载模块。--products 仅接受本地可发现包名，支持 all/none/逗号列表；Worker 接收相同选择和绝对数据目录，不领取未选产品任务。产品 HTTP 导出协议及 RAG-SWITCH-001 状态语义不变。


## UI-UNIFY-002 — 统一简洁外观与直接进入（2026-09-19）
用户明确要求：参考助手界面的简洁风格，优化三产品字体、颜色和UI；移除宿主启动页的二次确认；Wiki/Reader跟随AgentOS亮暗外观。责任Agent。
行动：统一中性灰语义颜色、系统字体、细边框、阅读间距；覆盖产品聊天、图形、表单。宿主保存light/dark/system外观于独立JSON文件，原CSRF保护写入；产品通过同源只读代理读取公开外观契约，不共享私有模块/业务库，服务不可用时保留最近外观。首次导航携带有效外观，随后定期/聚焦同步，跨桌面WebView2与浏览器有效。
打开：仍使用既有受保护启动接口，ready后location.replace进入已登记产品URL，无第二次按钮；失败显示错误与重试，手动禁用产品门控保持。
交付/验证：先文档再代码，三仓工程回归、前端构建、真实页面主题/导航/响应式检查、故障/CSRF/非法值/存储恢复测试、交接报告。更新服务前检查busy并一致性备份数据库，只重启已识别进程；不调用模型，不推送，不改变会话/笔记/PDF内容。原文PDF保持纸张本色；界面跟随主题。
失败处理：保留用户数据和此前更改；新故障先修复，外观服务离线不阻塞阅读，未实测如实记录。前轮UI-THEME-001不跨仓库范围由本次用户授权扩展。


## LLM-UI-001（2026-09-20，用户授权实施）
目标：AgentOS UI 配置 API、动态模型目录、全局 Custom 与参数继承，Reader/Wiki 接入。
执行责任 Agent。前置：三仓干净基线与 var/maintenance/LLM-UI-20260920 的源码/数据库备份。
行动：先建立通用配置契约和界面，产品后端通过受鉴权内部接口解析配置，直接调用供应商。
固定任务引用/JSON 契约，特殊能力按需适配；受管模式不回退环境变量；产品独立模式保留。
交付：设置 UI、三个产品配置闭环、迁移/恢复与验证记录；不新增独立模型网关，不推送。
验证：离线供应商替身、跨产品隔离、配置继承、基础对话/工具/PDF/结构化回归、真实浏览器。
真实生成不由保存、刷新或探活触发；真实质量验证需明确调用范围及预算。
失败处理：保留原件/会话/额度及旧版本；不得将工程测试替代真实语义质量验收。
详细用户方案：Windows 工作区 AgentOS-K-API与模型设置执行方案-2026-09-20.md。


## UI-READ-001 宿主检索增量
GET / 与 /api/v1/history 增加可选 q（问题关键词，最多200字符）、date_from/date_to（ISO日期，按 UTC 更新日期，含首尾日）。既有 source/thread/product/page 兼容；无数据库迁移，SQL参数化、通配符按普通文字处理。浏览器以本地时区展示完整时间。同步仅排队和查询既有任务接口，不触发生成。


## DATA-001 只读中心接口
GET /data、/data/{archive_id}；GET /api/v1/data/records、/api/v1/data/records/{archive_id}；GET /api/v1/data/records/{archive_id}/export。列表 source=all/product_ui/legacy_unknown（默认 all，仅前两类个人记录），q 最长 200，product_id/thread 最长 200/64，page>=1，20条分页。q 对问题和回答按普通文字查找。返回 total/page/items/sources；sources 为归档来源及最近同步任务状态，不代表产品在线。单条仅返回同范围个人档案，不暴露诊断记录；导出 JSON 为已保存问答快照，不包含原件文件。GET 无产品请求或同步副作用。写同步继续使用已有 token+Origin 校验接口。

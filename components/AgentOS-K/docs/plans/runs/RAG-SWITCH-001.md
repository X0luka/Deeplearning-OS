# RAG-SWITCH-001 执行记录 · 2026-09-14

目标：默认关闭 RAG，只有用户明确启动并就绪后才检查与同步；保留 Wiki、适配器、产品独立运行与原档案。
责任：Agent。用户已审核要求先文档后编码。基线 AgentOS-K 6c8327f，初始干净；仅宿主和指导文档变更，未改 RAG/Wiki 源码、密钥或产品数据，无真实模型调用、无推送。

## 修改与审阅

先更新 AGENTS、范围、架构、契约、任务/分工、运行与验收规则，再实现。产品目录保留，RAG 默认 disabled；start → starting → ready/failed；pause → stopping → disabled。Host 重启复位，Worker 重启读取共享状态。共享 Worker 监督从 RAG 构造路径独立。手动产品忽略自动启动清单，GET 打开页不自动启动。

SQLite 仅增 product_activity 表；入队、领取与提交检查活动状态，提交校验代次；跨进程文件锁与取消处理确保关闭时排空在途操作。应用层无产品 ID 特判。关闭不停止就绪独立服务，不删旧会话/失败任务。重新开启重置退避，从原游标恢复。

## 验证证据

- 宿主 37 项测试通过，包含默认关闭零请求/零新增任务、直接 API/CSRF、遗留任务、重复开启、失败不调度、在途取消、旧代次拒绝、独立进程 Worker 状态共享、Host 重启复位和已有 Wiki 回归。最新完整回执见 var/verification 中本任务时间段 JSON。
- 浏览器：隔离合成 HTTP 产品 + 实际 LocalProcesses + 独立 Worker；默认关闭时全局同步只创建 Wiki 任务；开启后 RAG 第一条同步成功；刷新保留；关闭后刷新和全局同步均无新增 RAG 请求/任务；独立 RAG 进程身份仍有效。证据 var/verification/RAG-SWITCH-001-browser.json；可复现合成 fixture 保存在 var/test-assets/RAG-SWITCH-001-fixture.py。
- 使用原 Windows 隐藏启动入口启动正式 Host :7777 与 Wiki :8601；浏览器显示 RAG 未启用、Wiki 服务正常。全局同步后 Wiki 成功；原 RAG 3 条档案哈希一致，任务总数保持 428。证据 var/verification/RAG-SWITCH-001-live.json。
- 启动前使用 SQLite backup API 保存一致性快照，备份路径见 var/verification/RAG-SWITCH-001-before.json。旧档案与失败记录保留；正式库新增活动状态表。

## 验证中发现及处理

首轮 3 项失败：finish 的新条件破坏既有 queued 完成兼容；新增合成批次遗漏 has_more；旧 Worker 测试仍期待自动 RAG 失败任务。分别修复兼容条件、fixture 和批准需求下的断言。随后 35 项通过，新增在途健康与启动取消检查后 37 项通过。导入顺序 lint 已修正。一次证据脚本误用系统 Python 缺 httpx，改用项目虚拟环境完成。

隔离与正式 Wiki 冷启动首次同步均可能早于服务就绪出现 unreachable，后续同步成功；保留记录，未扩大范围改变 Wiki 原调度策略。真实 RAG 依赖/检索验收继续暂停，未启动真实 RAG、Qdrant 或真实模型。

## 交接

用户刷新工作台即可见开关。重启 Host 后 RAG 关闭；关闭仅暂停宿主接入。历史失败任务继续显示是保留旧记录，不代表新版本继续探测。回退需先停宿主并核对备份，旧版本会恢复自动 RAG 同步，不能直接当作等价回退。运行源码/提交见当前交付记录；不推送。

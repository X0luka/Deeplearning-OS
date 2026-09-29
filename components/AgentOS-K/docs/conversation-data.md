# 产品原网页会话档案

## 事实与缺口

当前 RAG 已用 SQLite `interactions` 保存 query/answer/retrieved_chunks/model/provider/tokens/cost/feedback/latency。没有 conversation_id、source_channel、updated_at、revision 或状态字段；历史为时间倒序 offset 分页。模型生成结束后更新同一条占位记录，反馈也覆盖原记录。

因此不能只拉 id 大于水位的记录，也不能把空答案判为成功。原始 UI 的 st.session_state.messages 不等于持久化线程；浏览器刷新后线程状态与数据库关系没有明确 ID。

## 产品侧最小改动

1. 新增 record_uid（持久 UUID）、nullable conversation_id、source_channel、request_id、status、updated_at、revision，及事务变更日志。使用显式迁移脚本；现有 create_all 不会自动给旧表加列。旧记录迁移时分配并持久保存 record_uid，新记录使用新 UUID，避免旧备份恢复后自增 ID 复用造成错误合并。
2. 原网页每个新线程分配并保留 ID；明确新建、刷新与恢复线程行为。网页请求带 product_ui；直接 API/CLI 无标记时归为 api/unknown，绝不默认网页。
3. 在检索前持久保存已接收的请求，使检索失败也可归档。将该 interaction_id 贯穿生成，避免重复占位。
4. 创建、完成、反馈与错误更新和变更日志同事务。持久化失败不能只写日志后仍发送“完全成功”。
5. 断流/取消定义 interrupted，持久化已生成部分或明确标记未保存部分；实现必须以取消和断连接测试验证。服务器未接收到的输入不可能由后端归档，不承诺保存未提交文本。
6. 历史上下文按 conversation_id 隔离；当前查询使用全局最近问答，不得将多个网页线程混为同一上下文。

## 统一归档结构

```text
archive_identity = product_id + instance_id + record_uid
record:
  conversation_id? / source_channel / request_id?
  record_uid / source_interaction_id / export_epoch / revision / created_at / updated_at
  query / answer / status / feedback
  citations[] / retrieved_metadata / source_payload
  model / provider / token_usage / cost_value / cost_kind
  product_commit / adapter_version / imported_at
```

source_payload 保留经密钥过滤的源记录，不包含任意进程环境。旧数据来源和线程为 unknown，不自动编造。cost_kind=source_estimate；当前代码用静态价表，未知模型可能返回 0，不能解释为真实免费。

档案库建议含 archive_current、archive_revisions、sync_events、sync_checkpoints、product_instances。事件去重键包含实例、epoch、event_id；同一 epoch 内 revision 用来拒绝乱序覆盖。恢复旧库换 epoch 后，同一 record_uid 重新对账，不生成第二份会话；保留此前版本及源端回滚标识，不能用较旧源内容无声覆盖较新档案。新 epoch 中源端 ID 复用但 record_uid 不同，应视为新记录。默认档案列表只看 product_ui；legacy_unknown 单独可见以免丢弃旧历史。诊断任务结果保存在任务记录，不当作用户网页会话。

## 引用保真

当前历史只存 chunk IDs、scores 和 sources；没有完整文本、页码与文档 hash。引用的 source_id 是本次结果的序号，不是全局文档 ID；重建索引产生新 point UUID。

新记录应保存用户当时可见的引用快照（序号、source、page、text_preview）和文档版本/hash（能够获取时）。保留原 PDF 版本或至少 hash 与“原版本不可用”标识。历史缺少的引用快照标记缺失，不能拿重建后的 chunk 假装原证据。是否归档全文不属于首版必需范围。

## 首次导入与后续同步

对旧数据库先一致性备份，新增 schema 后在受控事务中为现有记录建立 bootstrap 事件或生成带高水位快照。旧记录 source_channel=legacy_unknown，人工确认后才允许批量改分类。使用 export_changes 持续同步。

现有 /api/history + /api/history/{id} 可做联调读取和停写条件下的有限全量导入；倒序 offset 在并发新增时会移位，不能作为稳定同步协议。即使重复扫全量，也不能弥补已丢失的会话来源与引用。

## 保留与恢复

默认长期保留产品网页问答与修订；日志 30 天轮换不影响档案。产品端不能在确认有完整档案前清理变更日志。索引是派生数据，PDF、SQLite 历史、档案、同步身份/epoch 是恢复核心。备份保护范围见 [运行手册](operations.md)。

保存会话不等于允许将它发给外部模型。AgentOS 历史浏览与同步应本地完成，既有 RAG tracing/模型外发另见接入文档，不假定“本地运行”就是“全部本地处理”。

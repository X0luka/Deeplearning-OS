# INLINE-DIAGRAMS-20260927

本轮已在 OpenHands 当前固定快照的六类 Wiki 正文下加入 26 张图：概览 3、架构 7、流程 7、阅读路线 7、运行与测试 1、边界与未解问题 1。

沿用当前配置的 deepseek-v4-flash 生成草稿，React Flow 渲染、ELK 布局。模型调用仅本任务关闭不受该通道支持的 web_search，未修改全局配置。Codex 阅读对应固定快照文件后逐图修正。输入仓库实际是 Agent Canvas 前端及启动工具，外部 Agent/Automation 服务内部实现不在审核范围。

审核修正包括：WS 断线时 REST 排队与缺少会话 ID 的错误分支；WebSocket 上下文与 EventHandler 职责；独立的后端探测入口；ProfilesService 内部适配与外部服务的区分；扩展/MCP/技能接口的边界；文件上传与 Git SDK 方法的区分。阅读图的虚线只代表建议阅读顺序。

每张图附源码路径与行号链接、可展开的文字关系。保留原正文，已发现的过时概括在相关图说明中明确更正；本轮并未声称全部历史正文已重写。原始模型输出、draft.json、reviewed.json、review-log.json 保留在该版本 architecture/knowledge/page-diagrams 中。

## 生成与发布约束

wiki_app.architecture.page_diagrams.generate 是现有知识流程的补充批处理入口。它按正文小节取引用源码生成草稿，校验节点、边、证据和小节覆盖；本轮不是所有仓库自动生成的开关。后续版本仍需生成与审核。

人工/Agent 阅读源码、修改 draft 后，在审核副本中记录 review.status=approved 及逐图审查说明，校验通过才原子替换 current.json。展示严格匹配 knowledge_id 与原正文哈希，知识更新时旧图不会混入。仅结构校验通过不能证明语义正确。

## 验证与恢复

6 项新增图数据测试；完整工程门禁及前端类型检查/构建通过。浏览器逐页检查六类页面，检查深色主题、ELK 连线与节点文本，修正主题对比度和多余留白。运行时无图组件错误日志。对应源码证据逐段与固定快照比对。

备份与恢复证据：var/maintenance/INLINE-DIAGRAMS-20260927。SQLite 备份恢复到隔离文件后 integrity_check=ok；未对正式数据库执行回滚。本次无数据迁移。撤回此批图可先将 page-diagrams/current.json 改名留存，页面回退为原正文；代码原件也已备份。没有执行被分析仓库的代码，外部后端运行行为未经验证。

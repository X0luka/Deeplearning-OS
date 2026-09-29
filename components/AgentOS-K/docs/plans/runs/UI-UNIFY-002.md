# UI-UNIFY-002：统一简洁外观与直接进入

2026-09-19；执行责任Agent。用户要求覆盖AgentOS、Wiki、Reader的字体/颜色/亮暗模式，并移除启动页的二次确认。此前UI-THEME-001仅宿主的范围由本次明确授权扩展。

## 结果

中性黑白灰、统一系统字体、细边框、较舒展阅读行距。共享appearance.css/js，工作台、Wiki正文/源码/架构图/聊天和Reader阅读/聊天/笔记/表单使用语义颜色。原始PDF不反色、不修改原件。
宿主公开GET /api/v1/appearance返回mode（light/dark/system）；POST沿用宿主写令牌与Origin校验，原子保存到var/appearance.json。产品同源GET代理只请求本机7777的公开外观端点，过滤其他字段；故障返回503，客户端保留缓存。无跨库读写、无跨产品私有模块导入，无CORS/CSP放宽。
工作台是偏好唯一写入入口，产品每3秒/重新聚焦同步，支持桌面WebView2与浏览器不同存储。打开链接携带有效偏好用于首屏；产品随后从地址栏清理该参数。宿主启动页沿用原启动/错误/手动禁用门控，ready后location.replace到登记URL。
维护：aos/host/static/appearance.*为共享源；scripts/sync_ui_assets.py显式分发到产品static目录；三个副本哈希一致。Wiki架构图已TypeScript检查并重新构建。

## 验证

- 原有回归：AgentOS55、Wiki57、Reader66项通过；新增持久化/CSRF/非法值/损坏文件恢复/产品代理离线测试分别2、1、1项通过，共182项。
- Wiki正式交付门禁再运行58项测试及lint/diff/暂存数据检查全部通过，回执var/verification/20260919T152415.json。
- 三仓代码与测试lint、git diff --check通过。Wiki npm run check / npm run build通过。
- 18组主题文字对比度：亮色最低5.07:1，暗色最低6.58:1；此项不等于整站无障碍认证。
- 实际浏览器：点击宿主Wiki/Reader入口后，新增页面自动到8601/8602，无二次确认。
- 工作台亮/暗/系统切换，Wiki与Reader已打开页面无需刷新自动同步；实际观察三处data-theme/appearance一致。共享JS哈希一致。
- Wiki首页、正文、架构总览（7节点）及聊天栏检查；Reader真实论文第7页、工具栏、历史回答、原文白色画布且filter:none检查。无新模型请求。
- 1440桌面布局；Reader390宽时flex纵向排布、文件库可见、documentWidth375无溢出；工作台414宽documentWidth399无溢出。浏览器Reader未捕获脚本错误。

## 本地更新与恢复

确认8601/8602 busy=false后，一致性备份三份SQLite并检查integrity_check=ok。备份与更新前差异：AgentOS-K/var/maintenance/UI-UNIFY-002-20260919-231440。
核对/proc命令后SIGTERM优雅结束桌面会话及独立Wiki，按原命令重启独立Wiki，再重开AgentOS桌面会话以恢复宿主与Reader。保留原产品归属，不关闭整个WSL。未修改PDF、源码快照、会话/笔记内容，未调用模型或推送。
需要回退时恢复本轮源码及构建资源并按同样空闲检查流程重载服务；外观JSON可保留，无数据库迁移需要恢复。数据库备份作为恢复材料，不覆盖活跃数据库。

## 限制

当前产品外观代理使用正式本机7777端口；自定义宿主端口须同步调整代理配置。Windows原生标题栏/系统对话框未改；实际原生窗口的独立视觉验收未执行。系统切换选项/当前暗色系统已实测，操作系统设置的实时变更未由Agent操作。历史论文语义质量结论保持。原生登录/睡眠恢复等旧待验收项不由本次UI测试覆盖。

> 历史组件设计/运维记录；其中路径已改为占位符。当前独立副本的运行和验收以仓库根 docs/DEVELOPMENT_SETUP.md 与 docs/ACCEPTANCE_STATUS.md 为准，不执行旧部署指令。

# DESKTOP-001 — 桌面会话与应用统一启停

日期：2026-09-19；责任：Agent；用户明确要求执行完整方案。AgentOS 基线 b83d1f2，开始时三个仓库工作区干净。本轮无真实模型调用、无推送；历史 PAPER-001 语义质量未通过的结论保留。

## 交付行为

- Windows WebView2 独立主窗口、桌面 AgentOS 快捷方式；命名互斥锁与事件实现重复启动聚焦。
- 隐藏 `wsl.exe` 前台管道连接，窗口存在期间持有 Linux 桌面会话。默认组合 wiki,paper，不加载 RAG。
- Python 桌面会话独占锁；私有 JSON 管道校验每次新生成的 256 位凭据。退出令牌不放 URL、网页、参数或日志，不开 HTTP 退出入口。
- SessionProcesses 仅记录并终止自己创建的进程组；PID/start/boot ID/组成员身份持久化。正确既有实例显示独立运行；身份冲突不杀占用者。正常/EOF 退出清理，30 秒后才强制终止验证过的组。
- 首页显示启动、运行、独立运行和失败状态，失败可重试；主窗口与页面退出共用流程。产品页面用系统浏览器打开。/health.busy 是产品只读增量；忙碌/未知可等待、确认停止或取消。
- 安装、构建、卸载、显式回滚入口及运维说明。旧登录任务与 systemd 自启已禁用；不关闭整个 WSL，不自动恢复旧任务。

## 实施文件

AgentOS：`aos/desktop_session.py`、`aos/adapters/session_process.py`、装配/运行状态/Home 模板、`desktop/AgentOS.cs` 与配置、桌面安装构建卸载脚本、备份和验收脚本、生命周期测试与指导文档。
产品：Wiki `wiki_app/main.py` 与 Reader `paper_reader/web.py` 的健康接口增加 busy，原业务字段与数据库结构不变；各自说明与离线测试同步维护。

## 本机迁移与恢复

最终程序：`${WORKBENCH_SOURCE_WINDOWS}\Desktop\AgentOS.exe`。
第一次迁移备份：`${WORKBENCH_SOURCE_WINDOWS}\migration\20260919-041317`。第一次 SQLite 快照：`${WORKBENCH_SOURCE}/var/maintenance/DESKTOP-20260919-041317`，16 份快照全部 quick_check=ok。
重复安装另有 042617 快照；migration-path.txt 仍指向第一次备份。卸载不恢复旧任务，不删数据库、论文、笔记与会话。显式 -Rollback 才恢复旧启动配置，数据库恢复必须单独决定。实际回滚到旧常驻方案未执行，避免重新引入其已知空闲退出问题。

旧任务 `AgentOS-K Startup` 为 Disabled、仅 LogonTrigger、无 PT1M；旧每分钟任务不存在。unit 保留但 disabled/inactive。旧 Start-AgentOS.ps1 已备份并指向桌面入口。

## 已验证

| 验证 | 证据/结果 |
|---|---|
| Host 工程门禁 | 55 项测试通过；aos/tests lint、diff 检查通过；回执 var/verification/20260918T202807.json |
| Wiki 工程门禁 | 53 项测试通过；wiki_app/tests lint、diff 检查通过；回执 var/verification/20260918T202815.json |
| Reader 回归 | 65 项既有测试通过，新增 busy 离线测试 1 项通过；web.py 与新测试 lint 通过，两个既有依赖弃用警告 |
| Wiki busy 增量 | 新增离线测试 1 项通过；不运行后台 worker、不调用模型 |
| 四种独立实例组合 | verify_desktop_products.py 用真实产品服务、隔离空数据库：none/wiki/reader/both 全部 PASS；退出只停受管进程 |
| 进程与凭据故障 | 自动测试覆盖 PID 复用拒绝、端口身份冲突保护、超时/退出、残留子进程组恢复、错误凭据拒绝、管道 EOF 清理 |
| 快捷方式与重复打开 | 实际快捷方式启动；二次进程退出，只留下原主窗口进程 |
| 主窗口关闭 | 实际点击标题栏 ×；Host/Wiki/Reader 均退出，事件记录三者正常停止、forced=false |
| 外壳崩溃 | Windows 强制停止 AgentOS.exe；管道 EOF 清理后 Windows 检查三个端口均不可访问 |
| 部分失败与界面重试 | 身份校验后 SIGTERM 本次拥有的 Reader；Wiki/主窗口保持运行，首页显示失败；实际点击重试恢复 ready |
| 产品页打开 | 实际从 AgentOS 打开 Reader 到系统 Chrome，出现 PaperReader 标题 |
| 普通网页不能退出 | 错误管道凭据被拒；普通 HTTP 退出路径不存在；主窗口来源校验与管道凭据分离 |
| 卸载/重复安装 | 快捷方式移除后重装成功，数据保留，首次回滚基线不覆盖 |
| Windows 持续观察 | 持续 451 秒（约 7.5 分钟），16 次检查全部正常；用户明确要求停止长时间观察，后续依据使用日志排查。不宣称完成 15 分钟验收 |

Windows 外壳初次实测暴露 WSL 参数引号问题：`-d "Ubuntu"` 经 ProcessStartInfo 传入时被本机 WSL 当作含引号名称，返回 WSL_E_DISTRO_NOT_FOUND；通过相同 API 对照去除多余引号后恢复。外壳对固定参数禁止空白/引号。安装路径改到稳定的 Documents，避免 Codex 打包环境的 AppData 文件重定向影响快捷方式。

全目录 `ruff check .` 发现旧 scripts/deepseek_node_check.py 和 scripts/integration_check.py 共 5 项既有问题，不属于本轮改动；正式门禁范围 aos/tests 及新增 Python 脚本检查通过。不将全目录 lint 写成通过。

## 未完成的真实验收

2026-09-19 用户调整：已实现即可，取消继续等待 15 分钟，后续使用时按日志排查。按此要求停止观察，保留 451 秒实测记录；不安排自动观察任务。

- Windows 重新登录、睡眠/唤醒：NOT_RUN。本轮没有替用户登出或睡眠。
- 关闭产品浏览器标签页后的服务保留：NOT_RUN。自动化工具无法可靠判定 Chrome 当前 URL，主动终止本轮 UI 操作；没有绕过限制或伪记通过。
- 忙碌任务时等待/取消/确认停止的真实 Windows 对话框：NOT_RUN；未制造付费任务。忙碌字段使用离线测试，不声称真实模型任务验收。
- 本次未实际执行回滚到旧常驻配置；备份与恢复入口已提供。

## 旧结论更正

RUN-SERVICE-002 的故障恢复、单次 HTTP、任务返回码及 186 秒无闪窗观察，不足以证明 systemd 能让 WSL 常驻。Microsoft 说明 systemd 服务本身不阻止 WSL 空闲退出。本轮主窗口持有前台 WSL 连接，Windows 侧观察与桌面关闭/崩溃单独验收；不沿用旧结论替代新验收。


## 本地版本保存
AgentOS 实现提交 8f18031；PaperReader 546a053；GitHub-Wiki 0342984。未推送。数据核对：Wiki 历史 25 条、Reader 文档 2 份、关联问答 5 条与笔记均保留；Reader 两个会话仅 updated 时间变化，符合打开页面行为。完整校验记录在 var/verification/desktop-data-preservation.json。

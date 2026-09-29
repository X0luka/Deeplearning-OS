# RUN-SERVICE-002 无闪窗常驻执行记录

## 目标与基线

旧 `AgentOS-K Local Recovery` 在当前用户交互会话中每分钟启动 PowerShell。日志和进程采样确认它会连带启动 Windows Terminal，造成闪窗。用户批准使用平衡恢复策略：systemd 处理服务进程故障，Windows 仅在登录时启动一次；人工 `wsl --shutdown` 后不后台唤醒。

实施前正式仓库干净，Ubuntu PID 1 为 systemd，现有 runner、Host 和 Worker 由 `xix` 运行。旧任务 XML 已保存到 Windows 安装目录 `AgentOS-K-Local-Recovery.xml`。

## 实施结果

- 安装系统级 `agentos-k.service`，以 `xix` 运行现有 `aos.local_runner`，配置 `Restart=on-failure`、`RestartSec=5s`、`KillMode=control-group` 和 30 秒停止超时。
- Windows 新任务 `AgentOS-K Startup` 只包含一个登录触发器，无重复周期。S4U 任务虽然能注册，但调用当前用户 WSL 返回 1；最终改为 Limited/Interactive 令牌，由 GUI 子系统 `wscript.exe` 使用窗口样式 0 调用 WSL root 启动 systemd unit。服务进程仍以 `xix` 运行。
- 安装器按备份旧任务、禁用旧任务、停止旧 runner、安装 unit、验证身份、新建任务、删除旧任务的顺序执行。卸载入口可移除新任务和 unit，并从 XML 恢复旧任务。
- 手动 Windows 启动入口优先使用 systemd；未安装 unit 的环境保留旧 runner 兼容路径。

## 验证证据

- `systemd-analyze verify` 通过；unit 为 enabled/active，Host、Worker 和 runner 均位于 `/system.slice/agentos-k.service` 且用户为 `xix`。
- 强制终止 systemd 主进程后，MainPID 从 2260 变为 2302，身份端点在 7 秒后恢复。人工 `systemctl stop` 后等待 7 秒仍为 inactive，随后启动恢复 ready。
- 新任务连续手动触发三次，状态均为 Ready、结果均为 0；进程监控只捕捉到预期 `wscript.exe`，没有 Windows Terminal、conhost、cmd 或 PowerShell。
- 新任务 XML 不含 `PT1M`，旧任务不存在；连续观察 186 秒，旧 `Start-AgentOS.ps1 -NoBrowser` 事件为 0。
- 清理 S4U 试验任务后，正式任务由当前用户创建；安装器以普通权限连续重复执行两次均成功，不需要 UAC。
- 停止 systemd unit 后运行 Windows 手动入口，身份端点恢复且 unit 回到 active，兼容启动路径通过。
- `ruff check aos tests` 通过，`pytest -q` 为 49 passed；身份端点继续返回 `product_id=agentos-k`。

本轮未调用模型、未改变业务数据、监听范围或产品启用策略，也未推送远端。人工关闭 WSL 后保持关闭是已选择的策略，不作为故障恢复缺口。

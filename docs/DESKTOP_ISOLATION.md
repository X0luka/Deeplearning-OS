# 桌面隔离实施状态

开发实例配置与后台隔离已实现并测试，Windows 桌面壳已独立编译，WebView2 合成页面交互通过；完整后台联动、安装/卸载和真实 API 尚未验收。
desktop/desktop.json 继续保持禁用。没有添加第二个桌面入口。

## 已实现
- DesktopConfiguration.cs 在创建窗体和单实例锁前校验实例 ID、WSL 用户/发行版、规范仓库路径和三个不同端口。
- 实例 ID 必须以 dev- 开头。标题明确显示开发测试和实例 ID。
- Windows 单实例锁/唤醒事件使用 Deeplearning-OS.Development 专用前缀和实例 ID。
- Windows 日志与 WebView2 缓存在 LocalApplicationData/Deeplearning-OS-Development/<实例 ID> 下；拒绝专用目录的链接重定向。
- WSL 启动通过 /usr/bin/env -i 清空继承环境，不读取交互 shell profile。
- aos.desktop_instance 再次校验独立副本标记、目录链接、实例 ID、端口；所有 Linux 状态位于副本 .local/instances/<实例 ID>。
- 工作台、Wiki、Reader 使用 workbench、wiki、reader 数据目录；HOME、缓存、临时目录和进程锁也各有专用子目录。
- 显式传递两个产品的数据目录，保留非开发入口原有默认行为和 HTTP 合约。
- 开发模式拒绝接管端口上的外部服务；预检只是第一道检查，进程监督器仍检查服务归属。

## 已验证
- 新增 19 项 Python 测试通过，包含目录越界、符号链接、正式端口、重复端口、环境继承、占用服务拒绝和两个真实后台生命周期场景。
- 真正启动工作台、Wiki、Reader，读取公共身份接口，核对配置主机和数据落点。
- 检查启动的产品进程没有继承合成模型密钥和错误目录环境变量。
- 同一数据实例重复启动被拒绝；原测试实例保持可用。
- 正常退出和 stdin 管道断开均清理本次进程，三个测试端口释放。
- 原有 6 项桌面进程所有权、崩溃恢复与控制管道测试的定向回归通过。
- Windows 桌面壳和独立配置测试编译成功；配置测试拒绝 9 类无效/禁用配置，未启动桌面壳。

## 验证边界
所有后台运行都在隐藏原目录、断网的私有 PID/挂载/网络命名空间内，数据为新建空数据。
Windows 测试仅通过 AgentOS(false) 启动合成页面夹具，不启动 WSL 或真实后台；使用专用缓存并在测试结束后确认专属进程退出。
已独立获取固定版本 SDK，三个 DLL 微软签名校验有效，并打包 SDK 许可证；不再借用原安装 DLL。不等于完整产品分发验收。
开发配置暂时仅支持规范的无空格 Linux 路径与标准 WSL 用户名；安装器尚未设计完成。
Linux 路径保护用于避免误配置，不宣称抵御同用户恶意并发替换目录的攻击。
保留的历史安装、卸载和启动脚本仍禁用。

## 后续顺序
1. 独立固定版本 SDK 与测试包已完成，合成页面状态保持测试通过；继续不创建桌面快捷方式。
2. 验证 Windows 壳与完整后台联动、重复启动和恢复；合成页面的专用缓存与状态保持已通过。
3. 以合成数据测试备份恢复，再做安装/卸载范围验证。
4. 明确真实模型测试的提供商、手动凭据、样本与费用，随后验证 API 和网络矩阵。
5. 发布前完成隐私、许可证、干净 Windows 安装及依赖审查。

证据位于 .local/preparation/desktop-instance-tests.log 和 desktop-isolation-tests.json。
Windows 编译记录位于独立管理目录的 验证产物/desktop-isolation-01/verification.json。

新增证据：验证产物/desktop-sdk-03/verification.json。完整验收状态见 docs/ACCEPTANCE_STATUS.md。

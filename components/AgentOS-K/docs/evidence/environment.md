> 历史组件设计/运维记录；其中路径已改为占位符。当前独立副本的运行和验收以仓库根 docs/DEVELOPMENT_SETUP.md 与 docs/ACCEPTANCE_STATUS.md 为准，不执行旧部署指令。

# 环境与访问证据

时间：2026-09-06。本报告记录一次读取结果，不代表长期监控。

| 检查 | 实际结果 | 解释 |
|---|---|---|
| WSL 发行版 | Ubuntu、docker-desktop，均 version 2；查询前 stopped | 后续命令已启动 Ubuntu |
| Ubuntu | 24.04.4 LTS / Noble | 已进入发行版执行只读命令 |
| PID 1 | systemd | 用户单元/cgroup 实际限额仍未验证 |
| WSL 内存 | total 3917MiB，available 3563MiB | 轻负载时点，不能当作产品启动后的任务余量 |
| WSL swap | 1024MiB | 应急交换，不算任务容量 |
| Linux 根文件系统 | 1007G 总量，946G 可用 | 虚拟文件系统容量，不是物理 SSD 实际剩余 |
| Windows 物理内存查询 | 15.35GiB | 来自 Win32_ComputerSystem 报告 |
| Windows C 卷 | 456.36GiB 总量，153.37GiB 剩余 | 不能证明 WSL VHD 一定位于 C；VHD 实际位置待查 |
| Windows Git | 2.52.0.windows.1 | 可执行文件存在 |
| WSL 工具位置 | /usr/bin/git、python3、node、systemctl；${LINUX_USER_HOME}/.local/bin/uv | 版本和运行兼容性未验证 |
| 仓库候选目录 | ~/code/RAG-AGENT、~/projects/RAG-AGENT、~/RAG-AGENT 未发现 .git | 有限范围检查，不代表整台机器没有克隆 |
| Git HTTPS | Windows git ls-remote 指定仓库，提权后仍连接 github.com:443 失败 | 未成功 clone；WSL 网络路径未测 |
| GitHub CLI API | 成功读取 metadata、commit、source、CI | API 路径可用，不等于 Git 传输可用 |
| 仓库权限 | 当前 CLI 返回 pull/push/admin 等 true | 未实际推送，未检查保护分支要求 |

首次 WSL/gh 配置/Windows CIM 查询受执行环境权限限制；使用获准的只读方式后成功。不是用户机器未安装 WSL 或仓库不存在。失败 CIM 调用产生的 0 内存输出无效，已以成功结果替代。

未检查真实密钥、PDF 内容、GPU、Docker 服务是否启动、现有代理配置、VHD 位置、真实 API 调用。没有运行下载的项目代码。未修改 WSL 全局配置。

## 2026-09-07 至 09-08 更新

两独立 WSL 仓库及各自 .venv 已存在：${WORKBENCH_SOURCE}、${LINUX_USER_HOME}/code/RAG-AGENT。历史测试 Python 3.12.3；09-08 命令行入口 --help 加载成功。Windows work/ 为历史一次性生成脚本，不是部署目录，不重跑或删除；当前工具 cwd 已为 WSL 正式仓库，不再沿用“当前会话仍在 Windows”的描述。

PDF-001 位于 AgentOS var/test-assets/；CHECK-002 证据位于 var/acceptance/，均不进 Git。历史交互式 WSL bash 存在 DEEPSEEK_API_KEY，普通 bash -lc 未加载，不能据此推断所有终端都有密钥。未复制密钥或更改全局代理。既有本机代理可达、直连超时的结果见 plans/runs/DEV-001.md（相对于 docs/）。

用户 09-08 提供的日志显示 Host 7777 首页请求成功，点击原产品后连接被拒绝；代码仅提供默认 8501 链接。诊断环境 ss 因权限受限，未据此认定用户机器实时端口状态。生产 Qdrant、浏览器全流程和资源限制仍未验收。

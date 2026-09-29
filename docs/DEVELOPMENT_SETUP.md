> 开发者安装启动以 [DEVELOPER_QUICKSTART.md](DEVELOPER_QUICKSTART.md) 为准。以下保留维护者隔离测试细节。

# 独立准备环境
此副本尚不是可公开安装的产品。所有命令从本仓库根目录执行；不要在正式仓库运行。

## Python
使用已有 uv 可执行文件作为工具，不共用正式虚拟环境：
    python3 scripts/prepare_dependencies.py --uv /absolute/path/to/uv
需要代理时添加 --proxy http://host:port，仅对子进程生效。
日常安装不添加 --refresh-locks；该选项用于明确更新依赖锁。
默认安装基础依赖及 dev 组；增强PDF使用另外锁定的CPU环境，已在本机独立验收，见 PDF_CPU_SETUP.md。

## 前端
    python3 scripts/install_node.py
    python3 scripts/prepare_frontend.py
需要代理时分别添加 --proxy http://host:port。
Node 版本和 SHA256 固定在 toolchain.json；工具、缓存均在本仓库 .local 中。
系统 Node 不会被升级。前端脚本采用 npm ci --ignore-scripts。
Reader 和 Wiki 已完成构建与类型检查；缓存就绪后 --offline 安装和隔离构建亦通过。
默认 npm ci 保留锁文件；仅明确需要更新根工具锁时使用 --refresh-lock。
构建代码自动进入隔离命名空间；下载/解包阶段不在断网命名空间中。

## 浏览器准备
    python3 scripts/prepare_browser.py
需要代理时添加 --proxy http://host:port。浏览器仅放在 .local/browsers，无桌面入口。
    python3 scripts/test_frontends.py
上述测试按组件串行创建独立命名空间，包含 JavaScript 单元测试和 Chromium 浏览器测试。

## 隔离回归
    bash scripts/run-isolated.sh python3 scripts/isolation_selfcheck.py
    bash scripts/run-isolated.sh python3 scripts/test_components.py verification
此入口隐藏原用户目录与 Windows 挂载、禁用外网和正式服务连接、清空继承环境。
其资源仍来自同一台电脑和同一 WSL 发行版，不是另一台虚拟机。
测试降低调度优先级，按 0.1 秒间隔监测命名空间进程的 RSS 总和，超过 1 GiB 停止；限制总时长 600 秒。
RSS 采样不是内核硬配额，共享页面会重复计数，也不能承诺测试对本机性能零影响。
Chromium 需要较大虚拟地址预留，因此没有继续使用原 32 GiB 虚拟地址上限。
依赖下载步骤需要外网，不在上述断网命名空间中；仅写副本目录、清空凭据环境。
测试通过不能代替真实 API、桌面安装、离线/代理矩阵与干净机器验收。

历史启动、安装、卸载脚本仍被禁用。不要尝试用它们启动准备副本。

## 桌面隔离验证
开发实例入口为 aos.desktop_instance，仅用于此独立准备副本。默认桌面配置仍禁用。
测试可通过隔离包装器运行 tests/test_desktop_instance.py；勿直接从现用产品启动。
Windows 编译入口为 scripts/build_desktop.ps1，要求一个全新的输出目录和只读 WebView2 引用目录。
网络路径的 PowerShell 脚本可能受签名策略限制；复制所需源码和脚本到独立本地验证目录后执行，不改变执行策略。
该入口仅编译并执行纯配置测试，不安装、不运行产品；现在打包经过锁定哈希检查的 SDK DLL 与许可证，但不安装 WebView2 Runtime。
当前边界与后续验收见 docs/DESKTOP_ISOLATION.md。

SDK 准备：python3 scripts/prepare_webview2.py；可添加 --proxy 或在缓存齐全时 --offline。版本与 SHA256 在 toolchain.json 固定。
Windows 编译入口现在要求该脚本产生的 SDK 目录与 sdk-files.json，不能直接引用原产品安装目录。
DesktopPaneTest.exe 是合成页面测试夹具，运行时必须设置其专用 WEBVIEW2_USER_DATA_FOLDER；正式 desktop.json 仍禁用。

## 必需的助手分词器与增强PDF环境
助手精确token计数需要原版固定校验值的官方分词器。独立副本可运行 scripts/run-tokenizer-preparation.sh；
离线检查：components/AgentOS-K/.venv/bin/python components/AgentOS-K/scripts/install_assistant_tokenizers.py --offline。
缺失时先补齐，不使用粗略计数代替。该准备脚本只写副本 var/tokenizers。
增强PDF的CPU环境、模型资产及真实恢复测试见 PDF_CPU_SETUP.md；不能只安装基础依赖就宣称完整阅读功能可用。

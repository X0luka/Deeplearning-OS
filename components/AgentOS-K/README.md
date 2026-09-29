# AgentOS-K

本地产品工作台：统一打开独立工具、管理启用状态，并保存带来源的会话档案。产品原网页继续负责业务操作。

## 当前桌面入口（DESKTOP-001）

双击 Windows 桌面 **AgentOS**。独立主窗口启动工作台、Wiki 与 PaperReader；关闭主窗口停止本次创建的服务，独立运行的应用保留。产品网页在浏览器打开，RAG 暂不纳入桌面组合。旧登录任务和 systemd 自启已禁用。

安装/卸载/显式回滚：[运行手册](docs/operations.md)。源码：[桌面外壳](desktop/AgentOS.cs)、[会话管理](aos/desktop_session.py)。实测与未实测项目：[DESKTOP-001](docs/plans/runs/DESKTOP-001.md)。用户已取消继续长时间观察，后续按实际使用日志排查。

以下 CLI 与旧扩展说明仍供独立启动或维护使用，不能同时开启旧常驻与桌面模式。

## 功能

- 通用核心负责产品目录、启用开关、启动管理、同步调度与 SQLite 档案。
- GitHub-Wiki 扩展提供源码阅读工具入口与会话归档。
- RAG-AGENT 扩展默认关闭，手动开启并就绪后才检查与同步；关闭暂停接入，独立服务与历史档案保留。
- 不加载任何扩展时，仍可浏览与备份已有档案。

## 目录

```text
AgentOS-K/
├── aos/
│   ├── domain.py / ports.py       通用类型与接口
│   ├── application.py / worker.py 通用用例与调度
│   ├── host/                     工作台页面与 API
│   ├── adapters/                 通用 HTTP 历史、进程、SQLite、活动锁
│   ├── integrations/
│   │   ├── rag/                  RAG 接入
│   │   └── wiki/                 Wiki 接入
│   ├── bootstrap.py              统一装配入口
│   └── local_runner.py / backup.py
├── docs/                         指导文档
├── tests/                        回归测试
├── scripts/                      启动与维护入口
└── var/                          本地数据、日志与备份（不提交）
```

GitHub-Wiki 和 RAG-AGENT 产品本身仍在独立仓库，扩展通过公开 HTTP 接口访问它们。默认目录为 AgentOS-K 的同级目录；可通过对应目录参数设置。

## 启动

在 WSL 的 AgentOS-K 目录中安装 Python 3.12+ 与 uv，运行：

```bash
uv sync --frozen
uv run aos serve --autostart
```

打开 [工作台](http://127.0.0.1:7777)。Wiki 自动启动，RAG 保持关闭。Windows 常驻安装入口为 `scripts/Install-AgentOS-Recovery.ps1`：WSL systemd 监督服务，Windows 只在用户登录时启动一次 WSL；手动入口 `scripts/Start-AgentOS.cmd` 仍可使用。

按需选择扩展，默认 all：

```bash
uv run aos serve --products none
uv run aos serve --products wiki --autostart
uv run aos serve --products rag
uv run aos serve --products rag,wiki --autostart
```

none 只打开档案工作台，不启动产品或 Worker。选择扩展与手动开启是不同操作：选择 rag 后仍须点击启动开关。刷新保持开关状态，Host 重启后手动产品关闭。关闭工作台保留已就绪的独立产品服务。

通用选项：--data-dir、--port、--products（也可用 AOS_PRODUCTS）。扩展选项见对应 --help；RAG 支持 --rag-dir/--rag-api/--rag-ui，Wiki 支持 --wiki-dir/--wiki-url。兼容 --no-wiki 与 --start-wiki。核心运行不要求配置产品模型或容器。

## 备份

```bash
uv run python -m aos.backup var/archive.db var/backups/archive-snapshot.db
```

目标文件必须不存在。宿主备份只覆盖档案数据库，各产品资料由产品独立保存。密钥、数据库、日志和本地报告不提交 Git。


### 统一外观

AgentOS侧栏“外观”提供跟随系统、亮色和暗色。设置在本机持久保存，Wiki与Reader自动跟随（可见页面通常3秒内更新，返回标签页时立即检查）。三处界面使用统一系统字体、中性灰颜色与细边框。Reader的原始PDF保持纸张本色。
从AgentOS打开Wiki/Reader时，服务就绪后直接进入产品；启动失败时才显示错误和重试，不再要求二次点击。外观同步服务暂不可用时保留最近主题，阅读不受影响。


## 阅读布局与草稿
Reader/Wiki支持助手整栏拖动调宽、键盘调节及专注阅读；小屏通过视图按钮切换，文件库仍可打开。输入草稿保存在本机，笔记请点击保存。Enter换行，Ctrl/Cmd+Enter发送。外观继续跟随AgentOS亮/暗/系统设置。工作台支持问题关键词及UTC日期筛选。


## 数据与知识中心
工作台侧栏的“数据与知识中心”用于查看已归档问答。可以搜索问题或回答、按来源筛选、查看详情并导出单条 JSON。同步状态默认折叠；原有应用入口和概览页保留。当前中心不代表全部论文、仓库文件或笔记，引用与原件的接入范围在页面中明确说明。

# 开发者源码版：准备、配置和启动

本次交付面向能使用终端的开发者。便捷体验优先按[Docker指南](DOCKER.md)启动完整容器和浏览器；本页保留源码调试与已有Windows桌面壳的构建方式。
不要求普通用户一键安装包、自动升级或卸载器。当前验证平台为 Windows + WSL2 Ubuntu、Linux x86_64、Python 3.12。
其他系统、ARM和不同时代的依赖组合未验收。

## 1. 前提和目录
需要已有 Python 3.12、uv、curl、tar，以及支持用户命名空间的 WSL2/Linux。
本机已验证 uv 0.10.7；脚本不替你升级系统 Python、安装发行版或修改全局代理。
Windows 构建使用系统 .NET Framework C# 编译器，运行桌面需要 Microsoft Edge WebView2 Runtime。
缺少前提时先安装官方工具；不要执行组件历史安装、卸载或系统维护脚本。

把源码放到 Linux 文件系统中的独立目录，例如 ~/opensource/Deeplearning-OS。
当前桌面启动配置要求 Linux 路径只含英文字母、数字、下划线、短横线、点和斜杠，不含空格。
不要覆盖已有产品目录。以下 Linux 命令均在仓库根目录执行：

```bash
python3 scripts/init_development.py
python3 scripts/prepare_dependencies.py --uv "$(command -v uv)"
python3 scripts/install_node.py
python3 scripts/prepare_frontend.py
python3 scripts/prepare_webview2.py
```

需要显式代理时，为上述联网命令添加 --proxy http://HOST:PORT。
这是本次命令的代理，不修改系统设置。没有统一适用于所有电脑的 localhost 端口。
应用自己的网络设置须启动后单独配置，见 NETWORK_SPEC.md。
准备完成后日志在 .local/preparation；失败时修复明确错误再继续，不把失败日志当作安装成功。

## 2. 增强 PDF
如需原版增强PDF/OCR/公式解析，准备额外的CPU环境和模型；不要把基础文本抽取当作增强解析。
首次需下载约1.17 GB布局/表格/公式文件及OCR资产、CPU依赖，并留出依赖解包空间。

```bash
python3 scripts/prepare_pdf_cpu.py install --uv "$(command -v uv)"
components/AgentOS-K/.venv/bin/python scripts/download_pdf_assets.py
.local/pdf-cpu/venv/bin/python scripts/prefetch_pdf_ocr.py
.local/pdf-cpu/venv/bin/python scripts/verify_pdf_environment.py
```

前三项联网命令支持 --proxy；缓存齐备时支持 --offline。
最后一项只校验本地依赖与资产，成功后启用该源码目录的增强解析环境。
它不宣称你的电脑已完成15页质量验收；维护者验收另用 --require-acceptance。
缓存和环境位于 .local/pdf-cpu，实例缓存各自独立。已有标准解析/断点复用结果见 PDF_CPU_SETUP.md。

## 3. 在 Windows 构建桌面壳
先将最少必要源码和已验证的SDK放入新的 Windows 构建目录。
将下面用户目录换成你自己的路径，发行版名用 wsl.exe -l -q 核对；不要照搬其他人的用户名。

```bash
python3 scripts/stage_desktop.py \
  --output /mnt/c/Users/YOUR_WINDOWS_USER/Documents/Deeplearning-OS-build \
  --distribution Ubuntu --user "$(id -un)" --instance dev-main
```

然后在 Windows PowerShell 进入该构建目录：
```powershell
.\scripts\build_desktop.ps1 -OutputDirectory "$PWD\dist\desktop" -WebView2Directory "$PWD\sdk"
Copy-Item -LiteralPath .\desktop.development.json -Destination .\dist\desktop\desktop.json
& .\dist\desktop\Deeplearning-OS-Development.exe
```

构建脚本不会覆盖已有输出目录。再次构建使用新的目录。
若本机策略阻止 PowerShell 脚本执行，不要全局关闭执行策略，先按你设备的管理规则处理。
没有自动创建快捷方式；关闭桌面窗口会检查后台任务并按你选择退出。
如果端口被占用，用 stage_desktop.py --ports HOST WIKI PAPER 选择三个空闲开发端口，不能停止别人的服务抢占端口。
默认实例名 dev-main、端口18777/18778/18779。Windows缓存位于 %LOCALAPPDATA%\Deeplearning-OS-Development\dev-main；
业务数据位于源码目录 .local/instances/dev-main，凭据不从你的其他产品自动导入。

桌面后台使用会话握手协议，请通过上述桌面入口启动。启动后需要备用浏览器时，可访问配置的HOST端口。

## 4. API 与网络
进入“模型”设置页，手动配置服务商、基础地址、模型ID和密钥，并选择任务默认模型。
DeepSeek官方测试保留模型默认思考，测试实例输出额度16384；上限不是必须用满的额度。
过低上限可能被思考消耗后正文截断；根据实际任务调整，不用短测试额度作为产品配置。
不要将密钥放入源码、提交记录或截图。

Gemini可选：未配置时，原生联网搜索及向原生模型发送原始PDF不可用；
DeepSeek等兼容模型的文本问答/翻译、PDF本地解析/查看/笔记仍可使用。
本机代理只说明本机验证通过；无透明代理的纯直连、其他地区/运营商、Gemini真实调用仍未验证。
按目标站点实际可达性配置，应用不会自带Clash或自动更改网关。

## 5. 更新与保护
保留旧源码检查点并先备份独立实例，再在另一源码目录验证更新；不混合两个实例的数据与密钥。
备份恢复见 INSTANCE_BACKUP.md。不要对原产品使用这个项目的历史维护脚本。
此文档描述开发者源码安装；当前实际通过与未通过范围以 ACCEPTANCE_STATUS.md 为准。


### API 与模型的统一设置
在模型设置中填写服务商的 Base URL 和密钥，离开输入框后自动读取模型目录；从下拉框选择模型并保存。OpenAI 兼容并不统一域名，常见根地址为 https://域名/v1，具体以服务商为准，不填写 /chat/completions 后缀。读取目录不调用生成接口；不提供目录时可手动填写准确模型 ID。

编辑时已有密钥仅显示遮罩，保持不动表示保留；删空并保存表示清除，卡片显示“未配置密钥”。主卡片只保留编辑/删除；删除确认后移除连接及密钥，并解除默认/产品模型绑定，已有资料和会话保留。各模块覆盖、代理、能力标记位于高级设置。目录读取成功仅说明目录可读，不代表生成调用已验证。

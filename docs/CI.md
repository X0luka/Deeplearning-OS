# CI：覆盖范围与操作

工作流：`.github/workflows/ci.yml`。PR、main 更新和手动运行触发。
Linux 与 Windows 作业串行；同一分支的新运行取消旧运行。Linux 最长35分钟，Windows最长10分钟。
这些是单次时间边界，**不是账户费用上限**。私有仓库的可用额度、预算与停止使用设置由仓库所有者在 GitHub Billing 中确认。

## 自动验证的内容

- Ubuntu 24.04 干净检出：固定 uv、Python 3.12 系统解释器，按锁文件安装三个组件。
- 命名空间隔离自检：断外网、清空继承密钥、隐藏用户目录、系统只读。
- 三组件后端串行回归；固定 Node 的前端构建、类型检查、JS及Chromium回归。
- 疑似凭据模式和依赖锁声明检查：匹配凭据时阻止通过，报告只给位置，不打印匹配值。
- Windows 2022：官方 WebView2 SDK 下载与哈希校验、桌面壳编译、配置拒绝测试。

标准回归不配置 DeepSeek/Gemini 密钥，不调用付费模型。下载依赖需要网络；业务测试在断网命名空间内执行。
Ubuntu 的 AppArmor 用户命名空间开关只在 GitHub 托管的一次性虚拟机内设置，不应复制到本地安装指南。
Windows 作业不会运行桌面、安装 Runtime、创建快捷方式或测试 WSL。
报告使用文件白名单保留3天，绝不上传整个 `.local`、数据库、实例目录或模型设置。

## 不能证明的内容

通过 CI 不等于原版翻译质量、真实论文URL完整读取、真实仓库Wiki生成、增强PDF质量、实际用户代理网络或Windows桌面交互全部通过。
这些仍按 PRODUCT_ACCEPTANCE.md 单独取证。云端干净检出能发现本机隐含依赖，但不是所有新电脑兼容性保证。
当前凭据扫描是启发式检查，不是完整秘密检测；提交前仍须检查 staged diff，不得将密钥、私人资料或未脱敏日志提交。
本流程做持续集成，不自动部署、不发布镜像、不公开仓库、不自动合并。

## 日常变更

1. 从 main 创建主题分支，提交内容保持单一目的。
2. 使用明确的 commit 描述，如 `fix(reader): preserve completed checkpoints on retry`。
3. PR 说明用户可见变化、涉及的验收项、验证证据及未验证部分。
4. 检查目标提交对应的 CI 结果；失败修复后重跑，不能把旧提交的绿灯当成新提交通过。
5. 审查后合并。main 的必需检查/分支保护需单独配置并验证，工作流文件本身不会保护分支。
6. 真实模型质量检查按明确样本运行，保留生成结果与比较记录，不机械重复已成功的付费测试。

## 当前证据

2026-10-01：本地 Windows SDK 准备、桌面编译及9类无效配置测试通过；CI YAML结构校验通过；
扫描器合成假密钥、缺失依赖和缺失可选组的拒绝路径通过，假密钥未出现在输出或报告中。
实际源码凭据模式匹配0；这不等于完整审计。
基础CI已在运行36875451190通过Linux干净安装/后端/前端以及Windows编译；用户已确认账户费用保护。后续PR应检查各自提交，不能复用这一运行代替新变更验证。

## Docker 增量检查与私有仓库限制
Docker作业接在Windows之后串行构建和运行真实HTTP/容器重建验收；默认core用于快速回归，手动full_pdf参数选择完整CPU增强PDF镜像，普通用户Compose默认full。Docker最长45分钟，仍不是账户费用上限。
2026-10-01分支保护API返回403，说明当前私有仓库套餐不提供此功能。CI已可运行，但当前没有服务器强制合并门槛；维护流程必须检查最新提交后再合并。未升级套餐、未公开仓库。

## 已完成的云端运行

| 提交 / 运行 | 已验证范围 |
|---|---|
| 9f73e73 / [36881075254](https://github.com/X0luka/Deeplearning-OS/actions/runs/36881075254) | Linux干净安装/回归、Windows构建、core镜像真实HTTP、删除容器后重建仍保留数据 |
| c285df5 / [36882427952](https://github.com/X0luka/Deeplearning-OS/actions/runs/36882427952) | full PDF/OCR镜像构建、三服务启动、HTTP及卷重建 |
| 785328f / [36883350147](https://github.com/X0luka/Deeplearning-OS/actions/runs/36883350147) | 运行中拒绝备份、停止后完整备份、拒绝覆盖已有实例、空卷恢复后的实际数据核对 |
| 009f633 / [36885168385](https://github.com/X0luka/Deeplearning-OS/actions/runs/36885168385) | full镜像内实际增强解析一页合成PDF、4GiB容器限额、完成页hash/mtime复用，以及备份恢复 |
| b5b8379 / [36885398248](https://github.com/X0luka/Deeplearning-OS/actions/runs/36885398248) | 新实例质量额度、已有配置保持，连同Linux/Windows/core生命周期全部通过 |
| f59bb1a / [36886208656](https://github.com/X0luka/Deeplearning-OS/actions/runs/36886208656) | Reader菜单后台刷新时序修复及确定性浏览器回归，连同完整常规CI通过 |

初次009f633的PR运行36885168417曾在既有Reader菜单交互失败；保留失败记录，并通过独立修复解决，没有删除测试。该提交的完整镜像专项运行成功不是对这次失败的抹除。集成提交另行检查。

GitHub账户已确认费用保护。实际读取的Actions默认权限为read，工作流不能批准PR。私有阶段的外部fork审批接口不适用（422）；公开时重新核对，不把此结果当成已配置外部贡献审批。

这些是持续集成与验收，不会部署到用户电脑、发布镜像或自动公开仓库。没有启用定时后台监控；更新PR会自动触发工作流。


### 2026-10-02 追加验证

- [36888291642](https://github.com/X0luka/Deeplearning-OS/actions/runs/36888291642) / `4a73cac`：首次在Ubuntu镜像下载停滞至35分钟超时，非产品断言失败；同提交第二次完整通过，PR #8随后合并。未删除测试或放宽业务规则。
- [36891998785](https://github.com/X0luka/Deeplearning-OS/actions/runs/36891998785) / `c819cb4`：563项后端、37项前端、Windows构建及core Docker生命周期通过。
- [36893385832](https://github.com/X0luka/Deeplearning-OS/actions/runs/36893385832) / `2afa15c`：Wiki修订结构恢复增量后的Linux、Windows与core Docker全部通过，PR #10随后合并；报告为228＋171＋165＝564项后端、37项前端；Reader保留2个既有依赖弃用提示。

GitHub报告部分固定Action仍声明Node20运行时、目前由托管平台强制使用Node24；当前任务已成功，后续应单独审核Action升级，不能把平台兼容执行当成已完成依赖升级。此提示未改变本机Node或用户环境。

软件源曾在Chromium依赖阶段卡住，最终候选为该步骤设置10分钟总上限，并仅在一次性GitHub托管VM设置APT单连接30秒/2次重试。下载失败会明确失败，不跳过浏览器回归；不修改维护者本机APT或网络配置。

运行[36898011338](https://github.com/X0luka/Deeplearning-OS/actions/runs/36898011338)对应PR #11最终提交56ad1ec，Linux（567项后端及37项前端）、Windows、core Docker全部成功，随后按该提交合并。网络异常回归覆盖连接失败、读取超时和上游502，统一503且保留已有版本。


运行[36916442191](https://github.com/X0luka/Deeplearning-OS/actions/runs/36916442191)对应PR #13的96a92c8：579项后端（240＋171＋168）、Linux前端回归、Windows构建及core Docker全部成功。新增12项历史引用迁移回归；此CI不调用真实模型。后续提示文案/验收记录提交须以自身CI为准。

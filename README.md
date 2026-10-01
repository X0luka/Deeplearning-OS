# Deeplearning-OS

把论文阅读、GitHub源码阅读和跨资料问答放在同一个本地工作台。资料与配置保存在自己的实例中；模型请求发送到你配置的服务商。

目前是**私有开发者候选版**。首选 Docker + 浏览器体验，保留已有 Windows 桌面壳；尚不提供普通用户一键安装包、自动升级或卸载器。

## 快速体验

需要 Git，以及能运行 Linux x86_64 容器的 Docker 和 Compose。私有阶段需要该仓库的访问权限。

```sh
git clone https://github.com/X0luka/Deeplearning-OS.git
cd Deeplearning-OS
docker compose up -d --build
```

就绪后打开 http://127.0.0.1:18877/settings/models，在界面配置自己的 API。优先使用已做真实样本验证的 DeepSeek；通用 OpenAI 兼容入口保留，其他服务商不能视为全部验收通过。

默认完整镜像包含增强 PDF/OCR 组件，首次构建需要下载较多依赖和模型文件。模型密钥不参与镜像构建，不写进命令或 Git 文件。网络、端口、资源、停止与恢复见[Docker指南](docs/DOCKER.md)。

```sh
docker compose stop
# 下次继续使用同一份数据
docker compose start
```

数据保存在独立命名卷中。普通退出不删除卷；更新前先按指南备份。Windows 桌面构建和源码调试见[源码/桌面指南](docs/DEVELOPER_QUICKSTART.md)，不会自动创建第二个桌面快捷方式。

## 功能与当前证据

- Reader：支持的论文 URL、PDF上传、中文通读、笔记与阅读进度。公开论文已完成210单元真实翻译和导出；15页增强解析与断点复用已有独立证据。语义抽查不等于所有论文、所有公式均正确。
- Wiki：读取公开 GitHub 仓库的固定提交，不执行仓库代码。Python、JavaScript/TypeScript有专门结构解析；其他语言主要提供原文和检索。真实Python公开仓库已完成正文、10图/6节文字、导出和恢复；其他语言真实质量尚未逐一验收。
- 统一助手：保留当前资料、跨资料检索、引用与对话。真实双向轨迹的最新结果以验收表为准。
- 工程：GitHub托管Linux干净安装、Windows编译、真实Docker启动/数据卷重建/备份恢复已有成功记录；完整镜像也实际执行了增强PDF解析与检查点复用。

本机Docker Desktop目前因自身通信端点故障无法启动，尚未完成本机容器体验，见问题I-13。上述容器证据来自云端，不能代表已在所有新电脑或所有网络环境通过。Gemini仍为可选、未做真实调用；无透明代理的家庭直连环境与全新Windows桌面/WSL整机体验尚未验收。

## 查看与参与

- [用户使用验收表](docs/PRODUCT_ACCEPTANCE.md) · [工程验收表](docs/ENGINEERING_ACCEPTANCE.md) · [问题清单](docs/ISSUE_REGISTER.md)
- [验收状态](docs/ACCEPTANCE_STATUS.md) · [CI覆盖范围](docs/CI.md) · [网络规范](docs/NETWORK_SPEC.md)
- [贡献指南](CONTRIBUTING.md) · [安全反馈与密钥保护](SECURITY.md) · [第三方说明](THIRD_PARTY_NOTICES.md)

MIT许可见[LICENSE](LICENSE)。不运行组件历史安装/卸载脚本，不从日常产品自动导入数据库或环境变量密钥，也不自动把本候选的修改写回原开发目录。

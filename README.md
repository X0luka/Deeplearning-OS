# Deeplearning-OS
本地论文与代码阅读工作台，整合 PaperReader、GitHub Wiki、统一助手和知识记录。

当前为供开发者从源码配置运行的开源候选版。一键安装包、自动升级和卸载工具不属于本次交付。
Windows桌面是默认入口；浏览器是配置和备用入口。

**验证范围：在同一台 Windows + WSL2 电脑的新源码、新依赖目录完成安装与桌面启动测试；尚未在另一台全新电脑上验收，也不代表全部功能均已验证。**

已通过 550 项后台回归、15 页增强 PDF 标准解析与断点复用，以及小型合成项目的完整 Wiki 生成、配图、导出和恢复。Gemini 真实调用、无透明代理的纯直连网络、其他系统、大型仓库及整篇 PDF 付费翻译质量仍有未覆盖范围，详见验收状态。

- [用户使用验收表](docs/PRODUCT_ACCEPTANCE.md)
- [工程验收与执行顺序](docs/ENGINEERING_ACCEPTANCE.md)
- [问题与改进清单](docs/ISSUE_REGISTER.md)
- [开发者从源码启动](docs/DEVELOPER_QUICKSTART.md)
- [交付导航](docs/DELIVERY.md)
- [当前验收状态与剩余工作](docs/ACCEPTANCE_STATUS.md)
- [网络规范](docs/NETWORK_SPEC.md)
- [独立开发环境](docs/DEVELOPMENT_SETUP.md)
- [备份恢复](docs/INSTANCE_BACKUP.md)

不运行组件历史安装/卸载脚本，不导入现用个人数据库或环境变量密钥。
源码位于 components 下的三个组件；所有修改仅留在独立副本，不自动同步回原项目。

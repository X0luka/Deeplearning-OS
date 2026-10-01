# Docker 开发者体验（候选，容器验收进行中）

目标是下载源码后用 Docker 构建启动，无需复用维护者的 Python、Node、密钥或个人资料。当前仅 linux/amd64 镜像，Windows使用已有Docker Desktop的Linux容器模式。
此入口与桌面壳并存，不创建桌面快捷方式。尚在验证的项不作为已交付能力，最终状态见 PRODUCT_ACCEPTANCE.md。

## 首次启动

在独立源码目录执行：

```sh
docker compose up -d --build
docker compose ps
```

健康状态就绪后访问 http://127.0.0.1:18877/settings/models，在界面输入自己的模型密钥。默认推荐已验证的DeepSeek；Gemini仍可选。
三个端口18877/18878/18879分别用于工作台、Wiki、Reader；只绑定宿主127.0.0.1。不应把端口改为公网监听，这仍是单用户本地产品。
端口被占用时停止本次启动，在未跟踪的.env中设置三个不同端口，然后重新启动；不要停止其他程序抢占端口。容器与浏览器使用同一组端口，链接和嵌入助手保持一致。

默认构建完整full镜像，包含固定版本CPU增强PDF/OCR依赖和固定哈希模型资产。首次构建需要下载较多文件和数GB磁盘空间，时间取决于网络；不调用LLM。
core构建目标只用于快速工程验证，缺少增强PDF解析环境，不代表默认完整产品可用。普通用户无需选择core。

## 数据、停止和升级

```sh
docker compose stop
docker compose start
```

资料、阅读进度、笔记、会话和API设置保存在当前Compose项目的数据卷中；源码不作为可写目录挂入容器。
`docker compose down`会移除容器和项目网络，默认保留命名卷；再次up会复用同一项目卷。
不要把`down --volumes`当普通退出命令，它会删除数据。更换Compose项目名称会创建另一份数据卷，旧数据不会自动迁移。

更新前先停止容器并备份到一个全新的本地目录：

```sh
docker compose stop
docker compose cp app:/opt/dlos/.local/instances ./deeplearning-os-backup-before-update
```

此命令要求容器仍存在，因此先stop，勿先down。复制的是完整本地实例，**包括密钥**，不要提交Git或分享给他人；它与INSTANCE_BACKUP.md中主动排除密钥的产品备份不同。
备份和恢复演练仍需验收，不能仅凭复制命令成功就宣称可恢复。升级应从新源码构建后复用数据卷，保留旧镜像及备份；数据库跨版本兼容仍按发行说明判断。

## 网络配置

- 浏览器访问的127.0.0.1是你的电脑；容器内的127.0.0.1是容器自身。因此不能照搬WSL中127.0.0.1:7897的代理地址。
- 若需要宿主HTTP(S)代理，可在应用网络设置中使用`http://host.docker.internal:实际端口`。前提是该代理确实允许来自Docker网段的连接；这里只提供主机映射，不自动开启Clash权限或改系统路由。
- 按NETWORK_SPEC.md分别配置默认、模型、论文、Wiki四个通道。能够访问DeepSeek不等于能够访问GitHub、arXiv或PDF/OCR资源站点。
- 镜像构建下载发生在应用启动之前，应用内代理设置不控制构建网络。若Docker拉取/构建无法联网，先处理Docker自身的网络配置；不要把模型密钥写入Dockerfile、构建参数或公共日志。
- 无代理用户只要能直接访问所需站点即可使用相应功能；不能声称所有用户都必须安装Clash。当前电脑TUN模式下的成功不等于纯直连已验证。

## 资源与安全边界

容器使用非root用户、只读根文件系统、独立命名卷、默认桥接网络，禁止新增权限并移除Linux capabilities；不挂载Docker socket、主机源码、原资料或主机HOME。
容器共享宿主资源，并非另一台完全独立电脑。当前内存上限4GiB、CPU上限2核；复杂PDF可能需要更多资源，不能以缺内存为由静默降低译读质量。
API密钥按现有产品方式保存于数据卷的私有文件；Docker管理员仍可读取卷，这不构成对本机管理员的加密保护。
源码构建上下文排除.local、虚拟环境、数据库、PDF、日志和.env；普通CI不提供真实模型密钥。

## 验证与范围

Docker CI将实际启动三服务，检查鉴权/来源拒绝、设置中的密钥不回显、PDF上传/原件取回、笔记与位置保存，以及删除并重建容器后数据保留。
这些是HTTP与容器生命周期验证，不等于模型回答质量或增强PDF逐公式准确率。默认CI使用core，完整full镜像使用手动CI参数full_pdf。
原有源码/桌面回归继续运行，Docker不会取代原版质量要求。

参考：[Compose网络](https://docs.docker.com/compose/how-tos/networking/)、[服务配置](https://docs.docker.com/reference/compose-file/services/)、[数据卷](https://docs.docker.com/engine/storage/volumes/)。

新建实例默认输出额度为16384，避免重现已有质量样本中的3000额度截断；这只是允许的上限，不是每次必须生成这么多。已有实例设置保持不变，实际生成长度由任务和模型决定。若所选服务商模型上限较低，界面会显示已知上限并要求调整；不会静默缩短输出。

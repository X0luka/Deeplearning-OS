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

更新前先停止容器，使用宿主 Python 3 保存完整备份（在源码目录执行）：

```sh
docker compose stop
python scripts/docker_data.py backup ./deeplearning-os.private-backup.tar.gz
docker compose start
```

工具与运行中的产品使用同一实例锁；产品没停止会拒绝备份，不会替你停止服务。目标文件必须不存在。备份**包括密钥**，请保存在自己的私有位置，不提交Git或分享；这与INSTANCE_BACKUP.md中主动排除密钥的备份不同。

恢复到一个全新的Compose项目，不覆盖原数据：

```sh
python scripts/docker_data.py restore ./deeplearning-os.private-backup.tar.gz --project deeplearning-os-restored
docker compose --project-name deeplearning-os-restored up -d --no-build
```

先停止占用相同端口的旧实例；需要同时打开时给新项目配置三个不同端口。恢复工具只接受不存在的目标实例，拒绝覆盖已有资料，检查归档路径并保留文件权限。它使用已经构建的本地镜像，不下载或执行备份中的程序。

恢复后逐项检查模型配置、论文、笔记、阅读进度、Wiki和对话，再决定是否继续升级。保留旧卷、镜像及备份；跨版本数据库兼容按发行说明判断。云端将演练合成数据的备份和空卷恢复，真实个人资料仍需自己保管。

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

9f73e73的云端运行36881075254已通过core镜像的真实三服务启动与容器重建验收。Docker CI实际启动三服务，检查鉴权/来源拒绝、设置中的密钥不回显、PDF上传/原件取回、笔记与位置保存，以及删除并重建容器后数据保留。
这些是HTTP与容器生命周期验证，不等于模型回答质量或增强PDF逐公式准确率。默认CI使用core，完整full镜像使用手动CI参数full_pdf。
原有源码/桌面回归继续运行，Docker不会取代原版质量要求。

参考：[Compose网络](https://docs.docker.com/compose/how-tos/networking/)、[服务配置](https://docs.docker.com/reference/compose-file/services/)、[数据卷](https://docs.docker.com/engine/storage/volumes/)。

PDF解析会同时考虑宿主可用内存和容器cgroup内存配额/当前占用，内存不足时沿用暂停和检查点恢复流程，不把宿主总内存误认为容器额度。full镜像的专项CI还会实际解析一页合成PDF并核对页范围检查点复用；它不替代复杂论文或扫描件的质量验收。

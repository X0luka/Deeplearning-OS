# DW 真实内容质量题库
状态：真实模型未调用；以下为固定公开 AgentOS-K 832729ec56afc8a8dcf4289488b11de454ceb14c 的预设问题与证据方向。首次真实模型生成前已建立此题库。
执行责任 Agent。每题记录回答、引用、结论与错误；至少 10/12 正确，重大职责/调用链/数据归属错误为失败。不可用源码检索命中率替代模型回答正确率。

|题|问题|预期证据/判定|
|---|---|---|
|1|项目主要用途是什么？|README：本地工作台、档案、产品扩展|
|2|命令行入口在哪里？|pyproject.toml 的 aos 脚本；aos/bootstrap.py main|
|3|无产品扩展能否启动？|bootstrap selected 分支和 create_app 空产品；README|
|4|产品服务是否和宿主在同一进程？|进程适配器 subprocess，独立生命周期|
|5|如何避免同端口重复启动？|process lock/key/ensure|
|6|启动失败会停止外部服务吗？|created 条件及进程身份检查；不可断言总是停止|
|7|档案与产品数据归谁？|SQLiteArchive 及 architecture 数据方向|
|8|同步游标在何时更新？|sqlite.apply 同事务写 events/records/checkpoints|
|9|重复事件怎样避免重复归档？|事件唯一键和稳定记录身份|
|10|如何识别会话与旧来源？|ports、sqlite records source/thread；未知来源不能猜|
|11|宿主备份是否包含全部产品数据？|README 和 aos/backup.py；只备份档案|
|12|该项目生产用户总数是多少？|缺少证据，应拒绝捏造|

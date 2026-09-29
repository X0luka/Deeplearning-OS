# 任务执行证据格式

实施时 AgentOS 使用 docs/plans/runs/Txx.md；RAG 使用已有 docs/task/ 并互相引用 Task ID，不重复建立冲突记录。

```text
任务/状态：Txx；NOT_STARTED / READY / RUNNING / BLOCKED / VERIFIED
授权范围：用户明确指令及未包含的对外动作
目标与前置：依赖任务及实际通过证据
仓库：绝对路径、remote、branch、基线 commit
已有修改：保护或隔离方式
执行：实际完成步骤与修改文件
验证：命令、cwd、环境、exit code、结果摘要、证据路径
数据：合成/真实；mock/真实提供商；外发授权依据
验收：逐条 PASS / FAIL / NOT_RUN
提交：实际 commit；未提交则说明原因
偏差：改变内容、理由及是否影响用户范围
阻塞：失败证据、解除责任、影响任务、可继续任务
下一步：下一可执行任务与授权状态
```

不输出密钥、完整用户对话或环境变量。真实回答证据保留本地受限位置，Git 只记摘要/hash。文件存在不等于测试通过；mock、实际本地 API、真实模型是三类证据。commit/push/CI/merge/部署分别记录。

任务卡测试路径为拟新增文件，先实现再执行，不能宣称目前已有。代码有新修改后重跑受影响验证，旧提交结果不自动覆盖新提交。

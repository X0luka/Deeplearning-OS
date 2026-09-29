> 历史组件设计/运维记录；其中路径已改为占位符。当前独立副本的运行和验收以仓库根 docs/DEVELOPMENT_SETUP.md 与 docs/ACCEPTANCE_STATUS.md 为准，不执行旧部署指令。

# Reader 中文通读 · TRANSLATE-001
日期：2026-09-22。分支 codex/reader-fulltext-translation。本次为 Reader 增量，Wiki 和数据与知识中心产品方向保持既有设计。

## 使用与数据
在 Reader 选择论文后点击工具栏“中文通读”。页面先展示当前模型、段落数及调用/输出预留上限；主动开始才发送该篇提取文本。原件不上传，不搜索网络。按页中英对照、保存进度、停止后续段落、手动继续和 Markdown 导出已实现。
原 paper.db 增加 translations、translation_segments 两表。译文绑定论文、解析哈希及模型设置，保存原文片段和块内范围；不同版本分别保留，不覆盖原件、笔记或聊天。译读单独预留预算，不重置聊天额度。已完成段落不重复请求；超时、截断、中断需手动确认重试。每段执行后回到共享队列，聊天可在段落之间执行。
本轮译读存放于 Reader；尚未把全文译读同步到平台数据中心，中心原有问答范围不变。

## 验证
83 项离线工程测试通过（新增 8 项），ruff、TypeScript 构建及 git diff 检查通过。覆盖原文范围、版本绑定、重复提交、独立预算、中断恢复、截断与公式占位符、OpenAI 兼容及 Gemini 离线传输、无配置读取、导出和完整备份恢复。
真实浏览器使用独立合成资料和离线替身：启动、停止后 3/6 保存、重开仍 3/6、继续后 6/6 且累计 6 次请求、第二页中英对照，窄屏和桌面布局检查完成。没有真实付费调用，不代表真实译文语义验收。
部署前两产品空闲，正常关闭桌面后用现有启动器重新加载。首次受限环境启动未带起服务，改用正常主机权限启动后恢复。最终 Wiki/Reader 健康均200且空闲，新译读页面、数据中心200；正式库 integrity_check=ok，译读任务0。记录 var/maintenance/TRANSLATE-001/live-verification.json。
旧版本 Reader 在恢复副本（包含新增表）上初始化、完整性和原 documents 查询通过。Git bundle 校验通过。原阶段A质量失败记录保留。

## 回退
代码基线 b7bdaef221ca0a23c74fef2f68860974748cb4f9；回退分支 rollback/reader-before-translation-20260922。
var/maintenance/TRANSLATE-001 保留 baseline.bundle、reader-before、reader-before-deploy 完整备份、host-before-deploy.db、restored-check 恢复副本。
回退前正常退出桌面并另做当前数据备份，确认工作区干净；在 ${PAPERREADER_SOURCE} 切换回退分支并 npm run build，然后从原桌面入口启动。原库可以保留新增表，旧代码不会显示译读；不应用旧备份覆盖当前库，以免丢失后来笔记。恢复本版则切回开发分支并重新构建、启动。未推送。

## 边界
全文指全部非空提取文本（含参考文献），不等于 OCR、图中文字或表格/公式二维结构全部识别。公式保护仅覆盖已提取、同一片段内可识别的定界公式，不保证跨段或 PDF 数学版式。译文以文本展示，未重新排版 PDF。
完成只代表输出已保存且未检测到截断，不代表知识已核验；正确性、相关性、是否误导仍需真实使用和针对性评估。没有在本轮扩大真实模型评测许可。

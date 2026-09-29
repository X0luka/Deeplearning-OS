# PaperReader

本地论文阅读体验版：左侧原始PDF，右侧中文助手，可保存选段、解释与个人笔记。由AgentOS启动并同步会话档案。

## 直接使用

- 阅读器：http://127.0.0.1:8602
- AgentOS入口：http://127.0.0.1:7777/products/paper/open
- 点击“打开PDF”导入英文文本论文；可翻页、缩放，拖选文字后解释、翻译或记笔记。
- 回答引用可跳到原文并高亮；会话、阅读位置、笔记自动保存在本机。
- 已放入Attention论文和一条明确标记的旧回答回放，便于不产生新费用地体验引用跳转。

助手默认未启用。展开“启用这篇论文的助手”，确认外发和计费范围后，主动提问才会调用DeepSeek。每次启用最多20次请求、累计输入300万UTF-8字节、输出6万token，每次最多3000token；失败不自动重试。原有评测许可不会被复用。

## 安装与启动（WSL/Linux）

```sh
uv sync --extra dev --locked
npm ci
npm run build
uv run python -m paper_reader.web --port 8602 --data-dir var/reader
```

Node建议20及以上。PDF.js脚本本地部署，不把论文上传到PDF阅读服务。模型密钥优先读取DEEPSEEK_API_KEY环境变量；本机兼容读取.bashrc中同名的单条字面量赋值，不执行shell启动文件。可在数据目录provider.json配置非敏感proxy，或使用PAPER_MODEL_PROXY环境变量。

## 备份与恢复

```sh
uv run python -m paper_reader.backup backup var/reader /absolute/new-backup-directory
uv run python -m paper_reader.backup restore /absolute/new-backup-directory /absolute/new-reader-directory
uv run python -m paper_reader.web --port 8602 --data-dir /absolute/new-reader-directory
```

恢复必须使用新目录，切换前停止旧实例；恢复后保留文档、笔记、会话和记录身份，更新历史导出代次。密钥、模型调用许可和代理配置不随备份转移；恢复后重新配置和启用助手，不自动重发未完成问题。

## 边界

阅读质量尚未通过验收；对公式、表格、双栏与解析不完整的页面会显示限制。没有OCR、复杂证明、代码关联、多论文问答或自主联网研究。个人笔记不会被重新生成的回答覆盖。实施与验证记录见docs/status.md。


### 统一外观

AgentOS侧栏“外观”提供跟随系统、亮色和暗色。设置在本机持久保存，Wiki与Reader自动跟随（可见页面通常3秒内更新，返回标签页时立即检查）。三处界面使用统一系统字体、中性灰颜色与细边框。Reader的原始PDF保持纸张本色。
从AgentOS打开Wiki/Reader时，服务就绪后直接进入产品；启动失败时才显示错误和重试，不再要求二次点击。外观同步服务暂不可用时保留最近主题，阅读不受影响。


## 阅读工作区
聊天栏边缘可拖动调宽；Tab聚焦分隔条后用左右键调整，双击或Enter重置。可切换专注/并排，小屏保留完整入口。输入草稿按会话保存在本机，Enter换行、Ctrl/Cmd+Enter发送，未保存笔记可从草稿选择器恢复。清除浏览器数据会清除本机草稿；正式保存的记录独立保留。


## 中文通读

在 Reader 打开论文，点击“中文通读”，查看模型与本次调用范围后开始。译文按页中英对照并自动保存，可停止后续段落、继续和导出 Markdown。仅覆盖已提取文本，图表与公式请对照原 PDF。

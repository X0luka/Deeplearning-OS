"""Stable, scoped failure contracts. Never expose provider payloads."""
VERSION = 1
RULES = {
    "archive_size_limit": ("INGEST.ARCHIVE_BYTES", "stage", "压缩归档超过支持上限", "调整采集配置后重试"),
    "text_size_limit": ("INGEST.TEXT_BYTES", "local", "部分文件超过文本总量预算", "可阅读已采集部分，扩大预算后重建"),
    "file_count_limit": ("INGEST.FILE_COUNT", "local", "部分文件超过文件数量预算", "可阅读已采集部分，扩大预算后重建"),
    "file_size_limit": ("INGEST.FILE_BYTES", "local", "文件超过单文件限制", "其余文件继续处理"),
    "archive_entry_limit": ("INGEST.ENTRY_COUNT", "stage", "归档条目超过安全上限", "调整采集范围后重试"),
    "archive_scan_limit": ("INGEST.SCAN_BYTES", "stage", "归档解压扫描量超过安全上限", "检查归档体积"),
    "invalid_model_evidence": ("EVIDENCE.UNKNOWN_ID", "local", "本节模型输出的结构或引用不符合要求", "保留其他章节，本节暂提供源码检索，深度研究继续"),
    "model_output_truncated": ("MODEL.OUTPUT_TRUNCATED", "local", "本节模型输出不完整", "保留其他章节并缩小当前问题后重试"),
    "model_response_too_large": ("MODEL.RESPONSE_BYTES", "local", "本节模型响应超过传输上限", "保留其他章节，调整本节输出范围后重试"),
    "diagram_read_hint_unavailable": ("EVIDENCE.READ_HINT_UNAVAILABLE", "local", "补读提示路径不在固定快照中", "使用正文引用、有效路径和关键词继续检索；最终图仍须源码审核"),
    "knowledge_invalid_evidence": ("EVIDENCE.UNKNOWN_ID", "local", "模型引用了未提供的证据", "只修正失败主题；其他主题继续"),
    "knowledge_grounding_invalid_reference": ("EVIDENCE.UNKNOWN_ID", "local", "审核引用了未提供的证据", "只修正失败主题；其他主题继续"),
    "knowledge_audit_incomplete": ("QUALITY.AUDIT_INCOMPLETE", "local", "审核未覆盖全部结论或步骤", "只保留已核对部分并重新审核该主题"),
    "knowledge_invalid_response": ("MODEL.RESPONSE_FORMAT", "local", "模型响应不符合结构要求", "修正该阶段，其他主题继续"),
    "knowledge_invalid_research_plan": ("QUALITY.PLAN_FORMAT", "local", "研究规划不符合安全结构要求", "修正当前目录分组的规划"),
    "knowledge_review_required": ("QUALITY.UNSUPPORTED", "local", "内容尚未通过源码审核", "保留通过审核的主题并重试未完成部分"),
    "knowledge_context_too_large": ("MODEL.CONTEXT_BYTES", "local", "当前研究输入超过单次上下文限制", "拆分主题后恢复"),
    "model_finish_length": ("MODEL.OUTPUT_TRUNCATED", "local", "模型输出达到本次输出上限，响应不完整", "缩小该阶段内容或调整输出配置后显式重试"),
    "no_readable_files": ("INGEST.NO_SOURCE", "stage", "没有可读取的源码文本", "检查仓库与采集规则"),
    "model_not_configured": ("MODEL.CONFIGURATION", "stage", "未配置可用模型", "配置模型后恢复；源码阅读仍可用"),
    "repo_busy": ("DELETE.REPO_BUSY", "local", "仓库仍有运行或排队任务", "先取消任务并等待退出"),
    "deletion_preview_changed": ("DELETE.PREVIEW_CHANGED", "local", "删除范围已变化", "重新预览后确认"),
    "deletion_confirmation_required": ("DELETE.CONFIRMATION", "local", "仓库名称确认不匹配", "输入完整 owner/repo"),
    "repo_deleting": ("DELETE.IN_PROGRESS", "local", "仓库数据正在清理", "等待清理完成"),
    "snapshot_hash_mismatch": ("STORAGE.INTEGRITY", "stage", "快照内容与记录不一致", "停止使用受影响的快照并检查存储"),
    "analysis_timeout": ("ANALYSIS.TIME_BUDGET", "stage", "静态分析超过进程执行保护时限", "源码快照保留；检查慢文件后重试分析"),
    "architecture_analysis_failed": ("ANALYSIS.PROCESS_FAILED", "stage", "静态分析进程未完成", "源码快照保留；检查分析阶段后重试"),
    "github_unavailable": ("INGEST.NETWORK", "stage", "暂时无法连接 GitHub 或取得有效响应", "检查 GitHub 网络通道、代理及服务状态后重试；已有版本仍可阅读"),
    "github_rate_limited": ("INGEST.RATE_LIMIT", "stage", "GitHub 限流，暂时无法取得仓库", "保留已有版本；限流恢复后显式重试"),
    "repository_or_ref_not_found": ("INGEST.NOT_FOUND", "stage", "公开仓库或指定版本不存在", "核对 GitHub 地址及分支或提交"),
    "file_parse_failed": ("ANALYSIS.FILE_FAILED", "local", "该文件未能完成结构解析", "其他文件继续；仍可阅读该文件文本"),
    "research_budget_exhausted": ("QUALITY.RESEARCH_BUDGET", "local", "主题补读达到上限", "保留已核对内容，记录未解决问题"),
}
def failure(code, *, stage="", resource=None, limit=None, observed=None):
    code = str(code)
    rule, scope, message, action = RULES.get(code, (
        "EXECUTION.FAILED", "stage", "当前阶段执行失败", "保留成功阶段，检查后显式重试"))
    if code.startswith("provider_") or code in {"ConnectTimeout", "ReadTimeout", "TimeoutError", "HTTPStatusError", "RemoteProtocolError", "ConnectError", "ReadError", "WriteError", "ProxyError", "NetworkError"}:
        rule, scope, message, action = "PROVIDER.UNAVAILABLE", "stage", "外部服务调用失败", "生成阶段有限恢复后仍未完成；检查连接或服务状态后重试"
    if code in {"OSError", "OperationalError", "DatabaseError"}:
        rule, scope, message, action = "STORAGE.WRITE_FAILED", "stage", "存储操作失败", "检查磁盘与数据库后恢复"
    return {"code": code, "rule_id": rule, "rule_version": VERSION, "scope": scope,
            "stage": stage, "resource": resource, "limit": limit, "observed": observed,
            "message": message, "action": action, "retryable": True}
class RuleError(ValueError):
    def __init__(self, code, **details):
        super().__init__(code)
        self.failure = failure(code, **details)

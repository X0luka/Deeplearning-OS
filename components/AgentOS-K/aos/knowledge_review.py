"""Validate a review before publishing a source-backed organizational snapshot.

This is not a classifier. Review decisions must be supplied explicitly; no fixed
topic names or source-specific keywords are embedded in the product.
"""
import hashlib
import json


def fingerprint(record):
    return hashlib.sha256(json.dumps([record['query'], record['answer']],
                                    ensure_ascii=False).encode()).hexdigest()


def source_text(record, field):
    if field in ('query', 'answer'):
        return record[field]
    if field == 'selection':
        value = record.get('selection')
        text = value.get('text', '') if isinstance(value, dict) else value or ''
        if (record.get('explanation') or '').startswith('reader-note-source/v1\n'):
            text = json.loads(record['explanation'].split('\n', 1)[1]).get('source', {}).get('text', '')
        return text
    if field == 'citations':
        return '\n'.join(str(c.get('text') or c.get('quote') or '')
                         for c in record.get('citations', []))
    raise ValueError('unsupported_evidence_field')


def build_snapshot(records, review, generated):
    sources = {r['key']: r for r in records}
    topics = review['topics']
    topic_ids = [t['id'] for t in topics]
    if not 1 <= len(topics) <= 10 or len(set(topic_ids)) != len(topic_ids):
        raise ValueError('invalid_topic_count_or_identity')
    decisions = review['records']
    keys = [d['key'] for d in decisions]
    if len(set(keys)) != len(keys) or set(keys) != set(sources):
        raise ValueError('review_must_account_for_every_source')
    result = {}
    field_names = {'query': '原问题', 'answer': '历史回答（未作事实核验）',
                   'selection': '保存的选段', 'citations': '保存的引用原文'}
    for d in decisions:
        r = sources[d['key']]
        if d['fingerprint'] != fingerprint(r):
            raise ValueError('source_changed_since_review')
        tags, primary = d['topics'], d['primary']
        if len(set(tags)) != len(tags) or any(t not in topic_ids for t in tags):
            raise ValueError('unknown_or_duplicate_topic')
        if bool(tags) != bool(primary) or (tags and tags[0] != primary):
            raise ValueError('primary_topic_required')
        expected = 'classified' if tags else 'needs_context'
        if d['review_state'] != expected or not d['rationale'].strip():
            raise ValueError('review_state_or_reason_missing')
        if not tags and not d.get('warning'):
            raise ValueError('unclassified_reason_required')
        evidence = d.get('evidence') or [{'key': r['key'], 'field': 'query',
                                         'text': r['query'][:220]}]
        for e in evidence:
            if e['key'] not in sources or sources[e['key']]['thread'] != r['thread']:
                raise ValueError('evidence_must_be_from_same_conversation')
            if not e['text'] or e['text'] not in source_text(sources[e['key']], e['field']):
                raise ValueError('evidence_not_in_saved_source')
        result[r['key']] = {**d, 'title': r['answer'][:120] if r['kind'] == 'note' else r['query'][:160],
                            'updated': r['updated'], 'reviewed_at': generated,
                            'marked': bool(r.get('marked', False)),
                            'evidence': [{**e, 'label': field_names[e['field']]} for e in evidence],
                            'proof': {'field': field_names[evidence[0]['field']], 'text': evidence[0]['text']}}
    topic_rows = [{**t, 'keys': [k for k, d in result.items() if t['id'] in d['topics']],
                   'primary_count': sum(d['primary'] == t['id'] for d in result.values())}
                  for t in topics]
    return {'schema': 'reviewed-topics/v2', 'generated': generated,
            'method': '按会话的问题、历史回答及已存资料线索复核讨论方向；主分类用于整理，关联主题允许跨类。未核验历史回答的事实正确性。',
            'scope': '当前中心已接入的新旧问答与归档笔记；不代表所有论文、仓库全文或平台外聊天。',
            'topics': topic_rows, 'records': result,
            'coverage': {'reviewed': len(result), 'classified': sum(bool(d['primary']) for d in result.values()),
                         'unclassified': sum(not d['primary'] for d in result.values())}}

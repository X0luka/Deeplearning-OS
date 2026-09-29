"""Localized fallback and visible review notes without weakening object integrity."""
from bs4 import BeautifulSoup

from .reading_response import TranslationError

REASONS = {
    "translation_invalid_json": "模型返回格式无法可靠读取",
    "translation_invalid_schema": "模型返回字段不符合要求",
    "translation_duplicate_key": "模型重复返回段落编号",
    "translation_truncated": "模型输出不完整",
    "paragraph_coverage_changed": "模型未完整返回对应段落",
    "protected_marker_changed": "公式、引用或格式标记未能可靠还原",
    "inline_structure_changed": "段内结构未能可靠还原",
    "empty_translation": "模型未返回有效译文",
    "paragraph_exceeds_model_request_capacity": "此段超过当前请求容量",
}


def translate_group(group, request, partial, issues, attempts, checkpoint, save, limit):
    """One grouped attempt followed by at most two per-unit repairs, durably counted.

    Only TranslationError is local. Network/configuration failures propagate.
    Saved valid units are never resent. Resume cannot reset the repair allowance.
    """
    def accept(response):
        partial.update(response['translations'])
        for key in response['translations']:
            issues.pop(key, None)
        issues.update(response.get('issues', {}))

    def invoke(units):
        checkpoint()
        for unit in units:
            key = unit['id']
            attempts[key] = attempts.get(key, 0) + 1
        save()  # Record before sending; a crashed request still consumes an attempt.
        try:
            accept(request(units))
        except TranslationError as exc:
            accept({'translations': exc.valid, 'issues': exc.issues})
            for unit in units:
                if unit['id'] not in partial:
                    issues[unit['id']] = {'kind': 'pending', 'code': str(exc)}
        except Exception:
            for unit in units:
                attempts[unit['id']] -= 1
            save()
            raise
        save()

    eligible = [u for u in group if u['id'] not in partial and len(u['text']) <= limit and attempts.get(u['id'], 0) == 0]
    if eligible:
        invoke(eligible)
    for unit in group:
        key = unit['id']
        while key not in partial and len(unit['text']) <= limit and attempts.get(key, 0) < 3:
            invoke([unit])
        if key not in partial:
            checkpoint()
            code = 'paragraph_exceeds_model_request_capacity' if len(unit['text']) > limit else issues.get(key, {}).get('code', 'translation_invalid_schema')
            partial[key] = unit['text']
            issues[key] = {'kind': 'original', 'code': code, 'reason': REASONS.get(code, '此段未能可靠翻译')}
            save()
        elif issues.get(key, {}).get('kind') == 'pending':
            issues.pop(key)
            save()


def summary(issues, total):
    original = sum(i['kind'] == 'original' for i in issues.values())
    return {'total': total, 'translated': total - original, 'original': original,
            'review': sum(i['kind'] == 'review' for i in issues.values()), 'issues': len(issues)}


def annotate(translated, original, issues):
    if not issues:
        return translated
    root = BeautifulSoup(translated, 'html.parser')
    source = BeautifulSoup(original, 'html.parser')
    targets = {n['data-reading-unit']: n for n in root.select('[data-reading-unit]')}
    sources = {n['data-reading-unit']: n for n in source.select('[data-reading-unit]')}
    for key, issue in issues.items():
        target = targets[key]
        note = root.new_tag('span', attrs={'class': 'reading-review-note', 'role': 'note', 'data-review-kind': issue['kind']})
        note.string = ('此段暂未完成翻译，已保留原文。' if issue['kind'] == 'original' else '此处需核对原文。') + ' ' + issue.get('reason', '')
        target.insert(0, note)
        if issue['kind'] == 'review':
            detail = root.new_tag('details', attrs={'class': 'reading-review-source'})
            label = root.new_tag('summary')
            label.string = '查看原文'
            text = root.new_tag('div')
            text.string = sources[key].get_text(' ', strip=True)
            detail.append(label)
            detail.append(text)
            target.append(detail)
    return str(root)


CSS = """
.reading-review-note{display:block!important;color:#b91c1c!important;background:#fff1f2!important;border-left:3px solid #b91c1c;padding:7px 10px;margin:6px 0;font-size:14px;font-weight:normal;white-space:normal}
.reading-review-source{font-size:14px;margin:6px 0;padding:6px;border:1px solid #b91c1c;white-space:normal}
.reading-review-source summary{cursor:pointer;color:#b91c1c;background:#fff1f2}
"""

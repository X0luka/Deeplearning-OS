"""Strict translation responses and narrowly scoped structural recovery."""
import json
import re

from bs4 import BeautifulSoup

from .reading_structure import TOKEN, restore_unit


class TranslationError(ValueError):
    def __init__(self, code, *, valid=None, details=None, issues=None):
        super().__init__(code)
        self.valid = valid or {}
        self.issues = issues or {}
        self.details = details or {}


def heading_prefixes(plan):
    """Only an immediate, numeric, leading section label is owned by the renderer.

    Derive from the original DOM, without changing legacy plans or their hashes.
    No recovery is allowed for body references, formulae, or nested labels.
    """
    root = BeautifulSoup(plan['original'], 'html.parser')
    units = {u['id']: u for u in plan['units']}
    prefixes = {}
    for node in root.select('h1,h2,h3,h4,h5,h6'):
        unit = units.get(node.get('data-reading-unit'))
        if not unit:
            continue
        first = next((c for c in node.contents if str(c).strip()), None)
        if getattr(first, 'name', None) != 'span':
            continue
        classes = first.get('class', [])
        if not {'ltx_tag_section', 'ltx_tag_subsection', 'ltx_tag_subsubsection'}.intersection(classes):
            continue
        if first.find(True) or not re.fullmatch(r'\s*\d+(?:\.\d+)*\.?\s*', first.get_text()):
            continue
        for token, piece in unit['pieces'].items():
            if token.startswith('⟦P') and piece == str(first) and unit['text'].startswith(token):
                prefixes[unit['id']] = token
    return prefixes


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise TranslationError('translation_duplicate_key')
        result[key] = value
    return result


def decode_response(data, units, prefixes=None):
    prefixes = prefixes or {}
    try:
        choice = data['choices'][0]
        if choice.get('finish_reason') != 'stop':
            raise TranslationError('translation_truncated')
        content = choice['message']['content']
        if not isinstance(content, str):
            raise TranslationError('translation_invalid_schema')
        value = json.loads(content, object_pairs_hook=unique_object)
        expected = {u['id'] for u in units}
        if isinstance(value, dict) and set(value) == expected:
            value = {'translations': value}  # Exact direct ID maps are unambiguous.
        if not isinstance(value, dict) or not isinstance(value.get('translations'), dict):
            raise TranslationError('translation_invalid_schema')
        values = dict(value['translations'])
        review_notes = {}
        # Accept only lossless wrapper relocation with an exact, unambiguous ID set.
        # Some JSON-mode providers put a requested ID beside "translations".
        extras = set(value) - {'translations'}
        if extras:
            if extras != expected - set(values) or set(values) - expected:
                raise TranslationError('paragraph_coverage_changed')
            values.update({key: value[key] for key in extras})
        for key, translated in list(values.items()):
            if isinstance(translated, dict) and set(translated) in ({'text'}, {'text', 'review_note'}) and isinstance(translated['text'], str):
                if 'review_note' in translated:
                    if not isinstance(translated['review_note'], str):
                        raise TranslationError('translation_invalid_schema')
                    if translated['review_note'].strip():
                        review_notes[key] = translated['review_note'].strip()[:500]
                values[key] = translated['text']
    except json.JSONDecodeError as exc:
        raise TranslationError('translation_invalid_json', details={'line': exc.lineno, 'column': exc.colno}) from None
    except (KeyError, IndexError, TypeError, AttributeError):
        raise TranslationError('translation_invalid_schema') from None
    if set(values) - {u['id'] for u in units}:
        raise TranslationError('paragraph_coverage_changed')
    valid, failures, repairs, issues = {}, [], [], {}
    for unit in units:
        key = unit['id']
        if key not in values:
            failures.append({'unit': key, 'code': 'paragraph_coverage_changed'})
            continue
        translated = values[key]
        prefix = prefixes.get(key)
        if prefix and isinstance(translated, str) and translated.strip() and prefix not in TOKEN.findall(translated):
            translated = prefix + translated
            repairs.append(key)
        try:
            restore_unit(unit, translated)
            valid[key] = translated
            if key in review_notes:
                issues[key] = {'kind': 'review', 'code': 'model_uncertainty', 'reason': review_notes[key]}
        except ValueError as exc:
            failures.append({'unit': key, 'code': str(exc)})
    if failures:
        raise TranslationError(failures[0]['code'], valid=valid, issues=issues, details={'units': failures, 'restored_headings': repairs})
    return {'translations': valid, 'issues': issues, 'usage': data.get('usage'), 'model': data.get('model'),
            'restored_headings': repairs}

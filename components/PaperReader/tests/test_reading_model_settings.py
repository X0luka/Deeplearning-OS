import pytest

from paper_reader import reading_jobs


def test_reading_uses_platform_task_not_vendor_catalog(monkeypatch):
    monkeypatch.setattr(reading_jobs.llm_client, 'managed', lambda: True)
    calls = []

    def resolve(task, override, execution):
        calls.append((task, override, execution))
        return {'model': 'any-chat-model', 'custom': 'user style', 'max_output_tokens': 9000}

    monkeypatch.setattr(reading_jobs.llm_client, 'resolve', resolve)
    result = reading_jobs.settings_for_reading()
    assert result['model'] == 'any-chat-model'
    assert result['max_output_tokens'] == 9000
    assert result['custom'] == ''
    assert calls == [('reading', {'document_input': 'text', 'web_search': False, 'tools': False}, True)]


def test_reading_config_failure_does_not_fall_back_to_env(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'must-not-be-used')
    monkeypatch.setattr(reading_jobs.llm_client, 'managed', lambda: True)

    def fail(*args, **kwargs):
        raise ValueError('model_not_configured')

    monkeypatch.setattr(reading_jobs.llm_client, 'resolve', fail)
    with pytest.raises(ValueError, match='model_not_configured'):
        reading_jobs.settings_for_reading()

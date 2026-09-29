import re
import subprocess
from pathlib import Path

import httpx
import pytest

from wiki_app.main import create_app


def test_browser_auth_retry_contract():
    result = subprocess.run(['node', '--test', 'tests/import-auth.test.mjs'],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.asyncio
async def test_restart_rejects_old_token_before_repo_write_and_new_page_recovers(tmp_path):
    first = create_app(tmp_path)
    old = first.state.token
    second = create_app(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=second),
                                base_url='http://testserver') as client:
        body = {'url': 'https://github.com/huggingface/transformers'}
        rejected = await client.post('/api/repos', json=body, headers={'X-Wiki-Token': old})
        assert rejected.status_code == 403
        assert rejected.text == 'write_token_required'
        assert (await client.get('/api/repos')).json()['items'] == []
        page = await client.get('/')
        fresh = re.search(r'name="wiki-write-token" content="([^"]+)"', page.text).group(1)
        assert fresh != old
        assert page.headers['cache-control'] == 'no-store'
        assert '适用范围与当前能力' in page.text
        rejected_origin = await client.post('/api/repos', json=body,
            headers={'X-Wiki-Token': fresh, 'Origin': 'https://foreign.invalid'})
        assert rejected_origin.status_code == 403
        assert (await client.get('/api/repos')).json()['items'] == []
        accepted = await client.post('/api/repos', json=body, headers={'X-Wiki-Token': fresh})
        assert accepted.status_code == 201
        assert len((await client.get('/api/repos')).json()['items']) == 1

"""Isolated loopback-only UI fixture. Never imports a model gateway execution path."""
import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from conftest import tiny_pdf
from fastapi import Request
from fastapi.responses import JSONResponse
from paper_reader.web import create_app
from paper_reader.types import Question
import uvicorn

data = Path(tempfile.mkdtemp(prefix='paper-ui-fixture-'))
app = create_app(data)
store = app.state.store
for index in range(2):
    document = store.import_pdf(tiny_pdf(data / f'UI-fixture-{index + 1}.pdf', [[
        (50, 740 - j * 24, f'Synthetic reading line {j + 1}. Fixture paper {index + 1}.') for j in range(25)
    ], [(50, 740, 'Second page. This is synthetic test content.')]]))
    session = store.create_session(document['id'])
    request = Question(document=document['id'], mode='fulltext', question='离线长回答回放')
    identity, _ = store.enqueue_session(request, session['id'], f'fixture-seed-{index:020d}')
    store.set_job(identity, 'completed', answer={'markdown': '\n\n'.join(f'段落 {j}：这是用于验证滚动保持与阅读行宽的合成回答。' * 4 for j in range(20)), 'anchors': []})

# Remove actual paid-generation and appearance proxy endpoints in this isolated app.
app.router.routes = [route for route in app.router.routes if not (
    getattr(route, 'path', '') == '/api/questions' and 'POST' in getattr(route, 'methods', set())
    or getattr(route, 'path', '') == '/api/v1/appearance'
)]

@app.get('/api/v1/appearance')
def appearance():
    return {'mode': 'light', 'resolved': 'light'}

@app.post('/api/questions', status_code=202)
async def mock_ask(request: Request):
    body = await request.json()
    await asyncio.sleep(1)
    if 'FAIL' in body['question']['question']:
        return JSONResponse({'detail': '离线故障替身：问题未提交'}, status_code=503)
    identity, _ = store.enqueue_session(Question(**body['question']), body['session'], body['request_key'])
    store.set_job(identity, 'running', answer={'markdown': '离线生成替身开始。'})
    async def progress():
        for step in range(8):
            await asyncio.sleep(1)
            store.set_job(identity, 'running', answer={'markdown': ('新增内容用于验证阅读不跳动。\n\n' * (step + 1))})
        store.set_job(identity, 'completed', answer={'markdown': '离线替身完成。\n\n' * 20, 'anchors': []})
    asyncio.create_task(progress())
    return {'id': identity, 'created': True}

print('Isolated UI fixture:', data, flush=True)
uvicorn.run(app, host='127.0.0.1', port=8604, log_level='warning')

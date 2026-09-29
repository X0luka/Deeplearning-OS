import sys, tempfile, socket
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from aos.adapters.sqlite import SQLiteArchive
from aos.host.web import create_app
from aos.llm.store import ModelStore
with tempfile.TemporaryDirectory() as folder:
    app = create_app(None, None, SQLiteArchive(Path(folder) / 'archive.db'), model_store=ModelStore(Path(folder) / 'llm'))
    s = app.state.assistant.store
    s.create('First conversation', 'paper', [])
    s.create('Second conversation', 'general', [])
    async def no_model(*args, **kwargs):
        raise AssertionError('No model calls allowed')
    app.state.assistant.complete = no_model
    sock = socket.socket();sock.bind(('127.0.0.1', 0))
    print('FIXTURE=http://127.0.0.1:'+str(sock.getsockname()[1]), flush=True)
    uvicorn.Server(uvicorn.Config(app, log_level='error')).run(sockets=[sock])

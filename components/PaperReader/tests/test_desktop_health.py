"""Desktop exit advisory reports pending work without starting a model request."""
from fastapi.testclient import TestClient

from paper_reader.web import create_app


def test_health_busy_with_pending_jobs(tmp_path):
    app = create_app(tmp_path)
    # Without a context manager TestClient does not run lifespan/background workers.
    client = TestClient(app)
    try:
        assert client.get('/health').json()['busy'] is False
        with app.state.store.db() as db:
            # Use the minimum fields supported by SQLite; no question is dispatched.
            columns = {r[1]: r for r in db.execute('PRAGMA table_info(jobs)')}
            values = {'id': 'desktop-health-fixture', 'state': 'running'}
            for name, column in columns.items():
                if name not in values and column[3] and column[4] is None:
                    values[name] = '{}' if name in {'request', 'question'} else 'fixture'
            names = ','.join(values)
            marks = ','.join('?' for _ in values)
            db.execute(f'INSERT INTO jobs ({names}) VALUES ({marks})', tuple(values.values()))
        assert client.get('/health').json()['busy'] is True
        with app.state.store.db() as db:
            db.execute("UPDATE jobs SET state='completed' WHERE id='desktop-health-fixture'")
        assert client.get('/health').json()['busy'] is False
    finally:
        client.close()


def test_workbench_link_uses_current_instance(tmp_path, monkeypatch):
    monkeypatch.setenv("AOS_LLM_URL", "http://127.0.0.1:18777")
    client = TestClient(create_app(tmp_path))
    try:
        response = client.get("/")
        assert response.status_code == 200
        assert 'href="http://127.0.0.1:18777"' in response.text
        assert 'href="http://127.0.0.1:7777"' not in response.text
    finally:
        client.close()

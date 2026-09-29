import pytest

from aos.llm.store import ModelStore, address, proxy_address


@pytest.mark.parametrize('url', [
    'http://172.27.224.1:7890', 'http://127.0.0.1:7890',
    'http://[::1]:7890', 'https://proxy.example:8443',
])
def test_explicit_proxy_roundtrip(tmp_path, url):
    store = ModelStore(tmp_path)
    store.save_connection({'name': 'Manual configuration', 'protocol': 'chat',
                           'base_url': 'https://provider.example/v1',
                           'key': 'test-only-not-a-real-key', 'proxy': url})
    assert ModelStore(tmp_path).public()['connections'][0]['proxy'] == url
    assert 'test-only-not-a-real-key' not in str(store.public())


@pytest.mark.parametrize('url', [
    'http://user:password@proxy.example:7890', 'file:///tmp/proxy',
    'socks5://127.0.0.1:7890', 'http://localhost:bad', 'http://localhost:0',
    'http://localhost:7890/path', 'http://localhost:7890?q=secret',
    'http://localhost:7890#secret', 'http://local host:7890',
])
def test_unsupported_or_ambiguous_proxy_is_rejected(url):
    with pytest.raises(ValueError, match='invalid_proxy_address'):
        proxy_address(url)


def test_proxy_support_does_not_weaken_api_endpoint_policy():
    with pytest.raises(ValueError, match='invalid_api_address'):
        address('http://172.27.224.1:7890/v1')

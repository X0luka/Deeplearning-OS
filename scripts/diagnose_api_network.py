"""DNS/TLS only, including optional existing loopback proxy; no API credentials."""
import http.client
import json
import socket
import ssl

result = {"direct": {}, "existing_proxy": {}}
try:
    result["direct"]["dns"] = bool(socket.getaddrinfo("api.deepseek.com", 443, type=socket.SOCK_STREAM))
    result["direct"]["stage"] = "tcp"
    with socket.create_connection(("api.deepseek.com", 443), timeout=8) as raw:
        result["direct"]["stage"] = "tls"
        with ssl.create_default_context().wrap_socket(raw, server_hostname="api.deepseek.com") as secure:
            result["direct"]["tls"] = secure.version()
except Exception as exc:
    result["direct"]["error"] = type(exc).__name__
# CONNECT establishes only a tunnel; no provider HTTP request follows.
connection = http.client.HTTPSConnection("127.0.0.1", 7897, timeout=8,
                                          context=ssl.create_default_context())
try:
    connection.set_tunnel("api.deepseek.com", 443)
    connection.connect()
    result["existing_proxy"]["tls"] = connection.sock.version()
except Exception as exc:
    result["existing_proxy"]["error"] = type(exc).__name__
finally:
    connection.close()
print(json.dumps(result))

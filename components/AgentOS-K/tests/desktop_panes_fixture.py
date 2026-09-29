from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading,time
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  body=('<html><body><nav class="aos-primary-nav">web nav</nav><textarea id="draft"></textarea><a id="wiki" target="_blank" href="http://127.0.0.1:18671/">Wiki</a><a id="reader" target="_blank" href="http://127.0.0.1:18672/">Reader</a><a id="assistant" href="http://127.0.0.1:18670/assistant">Chat</a><div style="height:3000px">fixture</div></body></html>').encode()
  self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
servers=[ThreadingHTTPServer(('0.0.0.0',p),Handler) for p in [18670,18671,18672]]
for s in servers:threading.Thread(target=s.serve_forever,daemon=True).start()
print('Fixture ready',flush=True)
try:
 while True:time.sleep(1)
except KeyboardInterrupt:pass
finally:
 for s in servers:s.shutdown()

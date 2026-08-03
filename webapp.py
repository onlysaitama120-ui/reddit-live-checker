import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from checker import COMMENT_RE, RedditSession, check, load_snapshots, save_snapshots

HOST = "0.0.0.0"
PORT = 8000

_lock = threading.Lock()
_session = None
_snapshots = None


def get_session():
    global _session
    if _session is None:
        _session = RedditSession()
    return _session


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        try:
            return socket.gethostbyname_ex(socket.gethostname())[2][0]
        except Exception:
            return "127.0.0.1"


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reddit Live Checker</title>
<style>
  body { font-family: -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
         margin: 0; padding: 16px; max-width: 600px; margin: 0 auto;
         background: #f7f7f8; color: #1a1a1b; }
  h1 { font-size: 1.3rem; }
  textarea { width: 100%; box-sizing: border-box; height: 140px;
             border: 1px solid #ccc; border-radius: 8px; padding: 10px;
             font-size: 1rem; }
  button { width: 100%; padding: 14px; font-size: 1.1rem; border: none;
           border-radius: 8px; background: #ff4500; color: #fff; margin-top: 10px; }
  button:disabled { opacity: .6; }
  #status { margin-top: 10px; color: #555; }
  ul { list-style: none; padding: 0; }
  li { background: #fff; border: 1px solid #e0e0e0; border-radius: 8px;
       padding: 10px; margin-top: 8px; font-size: .95rem; word-break: break-all; }
  .LIVE { color: #2e7d32; } .REMOVED { color: #e65100; }
  .DELETED { color: #c62828; } .BLOCKED { color: #c62828; }
  .NOT_FOUND, .INVALID, .UNKNOWN { color: #777; }
  .tag { font-weight: bold; margin-right: 6px; }
  #summary { margin-top: 14px; font-weight: bold; }
</style>
</head>
<body>
  <h1>Reddit Live Checker</h1>
  <textarea id="links" placeholder="Paste one Reddit link per line"></textarea>
  <button id="btn">Check links</button>
  <div id="status"></div>
  <div id="summary"></div>
  <ul id="results"></ul>
<script>
const btn = document.getElementById('btn');
const links = document.getElementById('links');
const status = document.getElementById('status');
const summary = document.getElementById('summary');
const results = document.getElementById('results');

btn.onclick = async () => {
  const urls = links.value.split('\\n').map(s => s.trim()).filter(Boolean);
  if (!urls.length) { status.textContent = 'Paste at least one link.'; return; }
  btn.disabled = true;
  results.innerHTML = '';
  summary.textContent = '';
  status.textContent = 'Starting browser (first time takes ~15s)...';
  try {
    const res = await fetch('/check', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ urls })
    });
    const data = await res.json();
    status.textContent = '';
    for (const r of data.results) {
      const li = document.createElement('li');
      const tag = document.createElement('span');
      tag.className = 'tag ' + r.status;
      tag.textContent = r.status;
      li.appendChild(tag);
      li.appendChild(document.createTextNode(r.reason + ' — ' + r.url +
        (r.target && r.target !== r.url ? ' → ' + r.target : '')));
      results.appendChild(li);
    }
    const s = data.summary;
    summary.textContent = 'LIVE posts: ' + s.live_posts + ' | LIVE comments: ' + s.live_comments +
      ' | removed: ' + s.removed + ' | deleted: ' + s.deleted +
      ' | not found: ' + s.not_found + ' | blocked: ' + s.blocked;
  } catch (e) {
    status.textContent = 'Error: ' + e.message;
  } finally {
    btn.disabled = false;
  }
};
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] in ("/", "/index.html"):
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path.split("?")[0] != "/check":
            self.send_error(404)
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n) or b"{}")
            urls = [u for u in (payload.get("urls") or []) if isinstance(u, str) and u.strip()]
        except Exception:
            self._json({"error": "bad request"}, 400)
            return
        if not urls:
            self._json({"error": "no urls"}, 400)
            return
        try:
            with _lock:
                global _snapshots
                if _snapshots is None:
                    _snapshots = load_snapshots()
                sess = get_session()
                results = []
                counts = {"live_posts": 0, "live_comments": 0, "removed": 0,
                          "deleted": 0, "not_found": 0, "blocked": 0}
                for url in urls:
                    status, reason, target = check(url, sess, _snapshots)
                    if status == "BLOCKED":
                        sess._warm()
                        status, reason, target = check(url, sess, _snapshots)
                    if status == "LIVE":
                        if COMMENT_RE.search(target):
                            counts["live_comments"] += 1
                        else:
                            counts["live_posts"] += 1
                    elif status == "REMOVED":
                        counts["removed"] += 1
                    elif status == "DELETED":
                        counts["deleted"] += 1
                    elif status in ("NOT_FOUND", "INVALID", "UNKNOWN"):
                        counts["not_found"] += 1
                    else:
                        counts["blocked"] += 1
                    results.append({"url": url, "target": target, "status": status, "reason": reason})
                save_snapshots(_snapshots)
            self._json({"results": results, "summary": counts})
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


def main():
    server = HTTPServer((HOST, PORT), Handler)
    ip = lan_ip()
    print("Reddit Live Checker is running.")
    print(f"On this PC:      http://localhost:{PORT}")
    print(f"On your phone:   http://{ip}:{PORT}   (same Wi-Fi network)")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        if _session is not None:
            _session.close()
        server.server_close()


if __name__ == "__main__":
    main()

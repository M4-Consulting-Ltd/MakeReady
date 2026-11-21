#!/usr/bin/env python3
"""Watchtower SetReady: virtual "Set Ready" buttons for a Watchtower printer farm.

Serves index.html and forwards exactly two calls to Watchtower's public API, so the
API key stays on this machine and the browser has no CORS or certificate problems:

    GET  /api/printers                   -> GET  {base}/printers
    POST /api/printers/{name}/set-ready  -> POST {base}/printers/{name}/commands
                                            {"command": "set_ready"}

The API calls are the same ones the ClearCue hardware button makes
(https://github.com/ThePrintBench/ClearCue).

The Watchtower address and API key can be set from the page (Settings), which saves them
to config.json next to this file. They can also be given as environment variables or
written into config.json by hand:

    WATCHTOWER_URL       Watchtower address or API base URL (/api/public/v1 is added)
    WATCHTOWER_API_KEY   wt_... key with the control scope + set_ready
    WATCHTOWER_CA        path to Watchtower's CA certificate (PEM), for HTTPS
    WATCHTOWER_INSECURE  "1" to skip HTTPS certificate checks (not recommended)
    HOST                 address to listen on (default 127.0.0.1; 0.0.0.0 for the LAN)
    PORT                 port to listen on (default 8080)

Python 3.8+, standard library only.
"""
import json
import os
import ssl
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
TIMEOUT = 10


def load_config():
    cfg = {}
    path = os.path.join(HERE, "config.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    for key in ("WATCHTOWER_URL", "WATCHTOWER_API_KEY", "WATCHTOWER_CA",
                "WATCHTOWER_INSECURE", "HOST", "PORT"):
        if os.environ.get(key):
            cfg[key] = os.environ[key]
    return cfg


CFG = load_config()
LOCK = threading.Lock()
CONFIG_PATH = os.path.join(HERE, "config.json")
SAVED_KEYS = ("WATCHTOWER_URL", "WATCHTOWER_API_KEY", "WATCHTOWER_CA", "WATCHTOWER_INSECURE")


def truthy(v):
    return str(v).lower() in ("1", "true", "yes")


def normalise_url(text):
    """'192.168.1.50', '192.168.1.50:8443' or a full URL -> API base URL. '' if invalid."""
    url = str(text or "").strip().rstrip("/")
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    parts = urllib.parse.urlsplit(url)
    try:
        parts.port  # raises on a bad port
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    path = parts.path.rstrip("/") or "/api/public/v1"
    return "%s://%s%s" % (parts.scheme, parts.netloc, path)


def base_url():
    return normalise_url(CFG.get("WATCHTOWER_URL", ""))


def api_key():
    return str(CFG.get("WATCHTOWER_API_KEY", "")).strip()


def ssl_context():
    if truthy(CFG.get("WATCHTOWER_INSECURE", "")):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    if CFG.get("WATCHTOWER_CA"):
        return ssl.create_default_context(cafile=CFG["WATCHTOWER_CA"])
    return ssl.create_default_context()


def public_config():
    """What the page may see. The API key itself is never sent back."""
    return {"url": base_url(), "hasKey": bool(api_key()),
            "insecure": truthy(CFG.get("WATCHTOWER_INSECURE", ""))}


def update_config(new):
    """Apply settings from the page and save them. Returns an error string or None."""
    url = normalise_url(new.get("url"))
    if not url:
        return "Enter the Watchtower address, e.g. 192.168.1.50 or https://192.168.1.50:8443."
    key = str(new.get("apiKey") or "").strip()
    with LOCK:
        # A saved key is only reused for the address it was saved for, so changing the
        # address can't be used to send the key somewhere else.
        if not key and url == base_url():
            key = api_key()
        if not key:
            return "Enter the API key (it has to be re-entered when the address changes)."
        CFG["WATCHTOWER_URL"] = url
        CFG["WATCHTOWER_API_KEY"] = key
        CFG["WATCHTOWER_INSECURE"] = "1" if new.get("insecure") else ""
        saved = {}
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, encoding="utf-8") as f:
                saved = json.load(f)
        saved.update({k: CFG[k] for k in SAVED_KEYS if k in CFG})
        tmp = CONFIG_PATH + ".tmp"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w",
                  encoding="utf-8") as f:
            json.dump(saved, f, indent=2)
        os.replace(tmp, CONFIG_PATH)
    return None


def upstream(method, path, body=None):
    """Call Watchtower. Returns (status, body bytes, retry_after or None)."""
    with LOCK:
        BASE, KEY = base_url(), api_key()
        try:
            ctx = ssl_context()
        except Exception as e:
            return 502, json.dumps({"error": "Can't load the CA certificate file: %s." % e}
                                   ).encode(), None
    if not BASE or not KEY:
        return 503, json.dumps({"error": "Not set up yet: open Settings and enter the "
                                         "Watchtower address and API key."}).encode(), None
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + KEY)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
            return r.status, r.read(), None
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get("Retry-After")
    except Exception as e:  # DNS, refused, timeout, TLS
        reason = getattr(e, "reason", e)
        hint = ""
        if isinstance(reason, ssl.SSLError) and "WRONG_VERSION_NUMBER" in str(reason):
            hint = " That address doesn't use https: put http:// in front of it in Settings."
        elif isinstance(reason, ssl.SSLError):
            hint = (" Watchtower uses its own certificate authority: tick 'Skip the "
                    "certificate check' in Settings, or set WATCHTOWER_CA to its CA "
                    "certificate file.")
        return 502, json.dumps({"error": "Can't reach Watchtower at %s: %s.%s"
                                         % (BASE, reason, hint)}).encode(), None


class Handler(BaseHTTPRequestHandler):
    server_version = "WatchtowerSetReady/1.0"

    def send(self, status, body, ctype="application/json", retry_after=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if retry_after:
            self.send_header("Retry-After", retry_after)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                self.send(200, f.read(), "text/html; charset=utf-8")
        elif path == "/api/config":
            self.send(200, json.dumps(public_config()).encode())
        elif path == "/api/printers":
            status, body, ra = upstream("GET", "/printers")
            self.send(status, body, retry_after=ra)
        else:
            self.send(404, b'{"error":"Not found"}')

    def do_POST(self):
        # A page on another site must not be able to press buttons through the
        # visitor's browser: only accept JSON posts that come from this page.
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlparse(origin).netloc != self.headers.get("Host"):
            self.send(403, b'{"error":"Cross-origin request refused"}')
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self.send(415, b'{"error":"Content-Type must be application/json"}')
            return
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        parts = self.path.split("?", 1)[0].split("/")
        if parts == ["", "api", "config"]:
            try:
                new = json.loads(raw or b"{}")
                err = update_config(new) if isinstance(new, dict) else "Bad request."
            except ValueError:
                err = "Bad request."
            except OSError as e:
                err = "Couldn't save config.json: %s." % e
            if err:
                self.send(400, json.dumps({"error": err}).encode())
            else:
                self.send(200, json.dumps(public_config()).encode())
            return
        # /api/printers/{name}/set-ready ; {name} arrives URL-encoded and is passed on as-is
        if len(parts) == 5 and parts[:3] == ["", "api", "printers"] and parts[3] \
                and parts[4] == "set-ready":
            name = urllib.parse.quote(urllib.parse.unquote(parts[3]), safe="")
            status, body, ra = upstream("POST", "/printers/%s/commands" % name,
                                        {"command": "set_ready"})
            self.send(status, body or b"{}", retry_after=ra)
        else:
            self.send(404, b'{"error":"Not found"}')

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))


def main():
    host = str(CFG.get("HOST", "127.0.0.1"))
    port = int(CFG.get("PORT", 8080))
    print("Watchtower SetReady on http://%s:%d  ->  %s"
          % (host, port, base_url() or "(not set up: open the page and use Settings)"))
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass

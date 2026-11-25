#!/usr/bin/env python3
"""MakeReady: virtual "Set Ready" buttons for a Watchtower printer farm.

Serves index.html and forwards a short fixed list of calls to Watchtower's public API, so the
API key stays on this machine and the browser has no CORS or certificate problems:

    GET  /api/printers                   -> GET  {base}/printers
    POST /api/printers/{name}/set-ready  -> POST {base}/printers/{name}/commands
                                            {"command": "set_ready"}
    POST /api/printers/{name}/resume     -> the same call with {"command": "resume"}
    POST /api/printers/{name}/pause      -> the same call with {"command": "pause"}
    POST /api/printers/{name}/stop       -> the same call with {"command": "cancel",
                                            "confirm": true}

The API calls are the same ones the ClearCue hardware button makes
(https://github.com/ThePrintBench/ClearCue).

The Watchtower address and API key can be set from the page (Settings), which saves them
to config.json next to this file. They can also be given as environment variables or
written into config.json by hand:

    WATCHTOWER_URL       Watchtower address or API base URL (/api/public/v1 is added)
    WATCHTOWER_API_KEY   wt_... key with the control scope + set_ready
    WATCHTOWER_USERNAME  optional Watchtower login, used for camera snapshots and the
    WATCHTOWER_PASSWORD  queue / filament view (not part of the public API the key covers)
    WATCHTOWER_CA        path to Watchtower's CA certificate (PEM), for HTTPS
    WATCHTOWER_INSECURE  "1" to skip HTTPS certificate checks (not recommended)
    HOST                 address to listen on (default 127.0.0.1; 0.0.0.0 for the LAN)
    PORT                 port to listen on (default 8080)
    PAGE_PASSWORD        password for the page itself (can also be set in Settings, where
                         it is stored hashed); without one, anyone who can reach the
                         page can use it
    REREAD_GAP           seconds to wait between AMS slots when re-reading tags (default 15;
                         environment only)
    CONFIG_DIR           folder holding config.json (environment only; default: this folder)

Python 3.8+, standard library only.

Copyright (C) 2026 M4 Consulting Ltd. Licensed under the GNU GPL version 3; see LICENSE.
"""
import base64
import hashlib
import hmac
import http.cookiejar
import json
import os
import secrets
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
# config.json lives next to this file, or in CONFIG_DIR (the Docker image uses /config)
CONFIG_PATH = os.path.join(os.environ.get("CONFIG_DIR") or HERE, "config.json")
TIMEOUT = 10


def load_config():
    cfg = {}
    path = CONFIG_PATH
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    for key in ("WATCHTOWER_URL", "WATCHTOWER_API_KEY", "WATCHTOWER_CA",
                "WATCHTOWER_INSECURE", "WATCHTOWER_USERNAME", "WATCHTOWER_PASSWORD",
                "PAGE_PASSWORD", "HOST", "PORT"):
        if os.environ.get(key):
            cfg[key] = os.environ[key]
    return cfg


CFG = load_config()
LOCK = threading.Lock()
SAVED_KEYS = ("WATCHTOWER_URL", "WATCHTOWER_API_KEY", "WATCHTOWER_CA", "WATCHTOWER_INSECURE",
              "WATCHTOWER_USERNAME", "WATCHTOWER_PASSWORD", "PAGE_PASSWORD_HASH",
              "SESSION_SECRET", "DISPLAY_TOKENS", "DISPLAY_SETTINGS")


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
    user, password = cam_creds()
    return {"url": base_url(), "hasKey": bool(api_key()),
            "insecure": truthy(CFG.get("WATCHTOWER_INSECURE", "")),
            "username": user, "hasPassword": bool(password),
            "hasPagePassword": auth_required()}


def update_config(new):
    """Apply settings from the page and save them. Returns an error string or None."""
    url = normalise_url(new.get("url"))
    if not url:
        return "Enter the Watchtower address, e.g. 192.168.1.50 or https://192.168.1.50:8443."
    key = str(new.get("apiKey") or "").strip()
    user = str(new.get("username") or "").strip()
    password = str(new.get("password") or "")
    page_password = str(new.get("pagePassword") or "")
    if page_password and len(page_password) < 8:
        return "The page password must be at least 8 characters."
    with LOCK:
        # Same rule for the camera login: a saved password is only reused for the same
        # address and username.
        if user and not password and url == base_url() and user == cam_creds()[0]:
            password = cam_creds()[1]
        if user and not password:
            return "Enter the password for the camera login."
        # A saved key is only reused for the address it was saved for, so changing the
        # address can't be used to send the key somewhere else.
        if not key and url == base_url():
            key = api_key()
        if not key:
            return "Enter the API key (it has to be re-entered when the address changes)."
        CFG["WATCHTOWER_URL"] = url
        CFG["WATCHTOWER_API_KEY"] = key
        CFG["WATCHTOWER_INSECURE"] = "1" if new.get("insecure") else ""
        CFG["WATCHTOWER_USERNAME"] = user
        CFG["WATCHTOWER_PASSWORD"] = password if user else ""
        if new.get("removePagePassword"):
            CFG["PAGE_PASSWORD_HASH"] = CFG["SESSION_SECRET"] = ""
        elif page_password:
            CFG["PAGE_PASSWORD_HASH"] = hash_password(page_password)
            CFG["SESSION_SECRET"] = secrets.token_hex(32)   # logs out every other browser
        save_config()
    cam_reset()
    return None


def save_config():
    """Write the saved settings to config.json. Call with LOCK held."""
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


# ---- Page login ------------------------------------------------------------------------
# Optional password for the page itself. The password is stored as a salted PBKDF2 hash;
# a logged-in browser holds a cookie signed with SESSION_SECRET, so sessions survive a
# restart and are all ended by changing the password.
SESSION_DAYS = 365
COOKIE = "wsr_session"
PBKDF2_ROUNDS = 200000
LOGIN_FAILS = {}      # client address -> times of recent wrong passwords
MAX_FAILS, FAIL_WINDOW = 5, 60


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return "pbkdf2$%d$%s$%s" % (PBKDF2_ROUNDS, salt.hex(), digest.hex())


def check_password(password):
    try:
        _, rounds, salt, digest = str(CFG.get("PAGE_PASSWORD_HASH", "")).split("$")
        got = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(got.hex(), digest)
    except ValueError:
        return False


def auth_required():
    return bool(CFG.get("PAGE_PASSWORD_HASH"))


def _sign(expiry):
    return hmac.new(str(CFG.get("SESSION_SECRET", "")).encode(), str(expiry).encode(),
                    hashlib.sha256).hexdigest()


def session_cookie(clear=False):
    """Set-Cookie value for a new session (or one that removes it)."""
    if clear:
        return "%s=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict" % COOKIE
    expiry = int(time.time()) + SESSION_DAYS * 86400
    return "%s=%d.%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Strict" % (
        COOKIE, expiry, _sign(expiry), SESSION_DAYS * 86400)


def session_valid(cookie_header):
    for part in str(cookie_header or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name != COOKIE:
            continue
        expiry, _, sig = value.partition(".")
        if expiry.isdigit() and int(expiry) > time.time() and CFG.get("SESSION_SECRET") \
                and hmac.compare_digest(sig, _sign(expiry)):
            return True
    return False


def login_allowed(client):
    """False while this address has too many recent wrong passwords."""
    now = time.time()
    with LOCK:
        recent = [t for t in LOGIN_FAILS.get(client, []) if now - t < FAIL_WINDOW]
        LOGIN_FAILS[client] = recent
        return len(recent) < MAX_FAILS


def login_failed(client):
    with LOCK:
        LOGIN_FAILS.setdefault(client, []).append(time.time())


# A password given as PAGE_PASSWORD (environment or config.json) is hashed at start-up and
# only kept in memory; it takes precedence over one saved from the page.
if CFG.get("PAGE_PASSWORD"):
    CFG["PAGE_PASSWORD_HASH"] = hash_password(str(CFG.pop("PAGE_PASSWORD")))
if auth_required() and not CFG.get("SESSION_SECRET"):
    CFG["SESSION_SECRET"] = secrets.token_hex(32)


# ---- Cameras -------------------------------------------------------------------------
# Watchtower's public API has no camera call, so snapshots come from the private web API
# its own pages use: log in for a session cookie, list the streams, fetch single JPEGs.
# That API is undocumented and may change between Watchtower versions.
STREAMS_TTL = 30      # seconds to reuse the stream list
FRAME_TTL = 2         # seconds to reuse a frame, so several open pages share one fetch
CAM_RETRY = 60        # seconds to wait after a failed login / stream list
CAM_LOCK = threading.Lock()
CAM = {}


def cam_reset():
    with CAM_LOCK:
        CAM.update(opener=None, streams={}, streams_at=0, retry_at=0, error="", frames={},
                   queue=None, queue_at=0, names=None, names_at=0)


def cam_creds():
    return str(CFG.get("WATCHTOWER_USERNAME", "")).strip(), str(CFG.get("WATCHTOWER_PASSWORD", ""))


def root_url():
    parts = urllib.parse.urlsplit(base_url())
    return "%s://%s" % (parts.scheme, parts.netloc) if parts.netloc else ""


def cam_opener():
    """A logged-in opener (session cookie). Call with CAM_LOCK held."""
    if CAM["opener"] is None:
        user, password = cam_creds()
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl_context()),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        req = urllib.request.Request(
            root_url() + "/api/auth/login", method="POST",
            data=json.dumps({"username": user, "password": password,
                             "remember_me": True}).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with opener.open(req, timeout=TIMEOUT) as r:
                ok = json.loads(r.read() or b"{}").get("success")
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403):
                raise RuntimeError("Watchtower rejected the camera username or password")
            raise
        if not ok:
            raise RuntimeError("Watchtower rejected the camera username or password")
        CAM["opener"] = opener
    return CAM["opener"]


def cam_fetch(path, timeout=TIMEOUT):
    """GET a private-API path with the session, logging in again once if it expired."""
    for attempt in (0, 1):
        with CAM_LOCK:
            opener = cam_opener()
        try:
            with opener.open(root_url() + path, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403) and attempt == 0:
                with CAM_LOCK:
                    if CAM["opener"] is opener:
                        CAM["opener"] = None
                continue
            raise


def watchtower_post(path, body, timeout=TIMEOUT):
    """POST to Watchtower's own web interface, the way its page does: logged in, with the
    anti-forgery token it hands out. Returns the reply as a dict."""
    token = (_unwrap(json.loads(cam_fetch("/api/auth/csrf-token"))) or {}).get("token") or ""
    with CAM_LOCK:
        opener = cam_opener()
    req = urllib.request.Request(
        root_url() + path, method="POST", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "X-CSRF-Token": token, "Origin": root_url()})
    with opener.open(req, timeout=timeout) as r:
        reply = json.loads(r.read() or b"{}")
    return reply if isinstance(reply, dict) else {}


def queue_next(job_id):
    """(status, body): move a waiting job to the top of Watchtower's queue, so it is the
    next one sent out. Jobs queued together (plates of one file) move together."""
    if not (all(cam_creds()) and root_url()):
        return 400, {"error": "This needs the Watchtower login in Settings."}
    try:
        queue = _unwrap(json.loads(cam_fetch(QUEUE_PATH)))
        jobs = [j for j in (queue.get("jobs") or []) if isinstance(j, dict) and j.get("id") is not None]
        # Watchtower's current order, top first.
        jobs.sort(key=lambda j: j["sort_order"] if isinstance(j.get("sort_order"), (int, float)) else 1e9)
        target = next((j for j in jobs if str(j["id"]) == str(job_id)), None)
        if target is None:
            return 404, {"error": "That job is no longer waiting."}
        group = target.get("row_group")
        first = [j for j in jobs if j is target or (group and j.get("row_group") == group)]
        rest = [j for j in jobs if j not in first]
        order = [j["id"] for j in first + rest]
        if order != [j["id"] for j in jobs]:
            reply = watchtower_post("/api/queue/reorder", {"job_order": order})
            if not reply.get("success"):
                return 502, {"error": str(reply.get("error") or reply.get("message")
                                          or "Watchtower didn't move the job.")[:200]}
    except urllib.error.HTTPError as e:
        return 502, {"error": "Watchtower refused to move the job (HTTP %d)." % e.code}
    except Exception as e:
        return 502, {"error": "Couldn't reach Watchtower: %s." % (getattr(e, "reason", None) or e)}
    with CAM_LOCK:
        CAM["queue_at"] = 0                       # show the new order straight away
    return 200, {"ok": True}


def cam_streams():
    """({printer name: stream name}, error). Chamber cameras first; online streams only."""
    user, password = cam_creds()
    if not (user and password and root_url()):
        return {}, ""
    now = time.time()
    with CAM_LOCK:
        if now < CAM["retry_at"]:
            return {}, CAM["error"]
        if CAM["streams_at"] and now - CAM["streams_at"] < STREAMS_TTL:
            return CAM["streams"], ""
    try:
        reply = json.loads(cam_fetch("/api/system/streams"))
        data = reply.get("data", reply) if isinstance(reply, dict) else {}
        streams = [s for s in data.get("streams") or []
                   if isinstance(s, dict) and s.get("enabled", True)
                   and str(s.get("state", "")).upper() == "ONLINE"
                   and s.get("printer_name") and (s.get("stream_name") or s.get("name"))]
        streams.sort(key=lambda s: "chamber" not in str(s.get("camera_name") or "").lower())
        found = {}
        for s in streams:
            found.setdefault(s["printer_name"], s.get("stream_name") or s.get("name"))
        with CAM_LOCK:
            CAM.update(streams=found, streams_at=time.time(), error="")
        return found, ""
    except Exception as e:
        err = "Cameras unavailable: %s." % (getattr(e, "reason", None) or e)
        with CAM_LOCK:
            CAM.update(retry_at=time.time() + CAM_RETRY, error=err, streams_at=0)
        return {}, err


def cam_frame(printer):
    """One JPEG for a printer, or None."""
    stream = cam_streams()[0].get(printer)
    if not stream:
        return None
    with CAM_LOCK:
        at, jpeg = CAM["frames"].get(printer, (0, None))
    if jpeg and time.time() - at < FRAME_TTL:
        return jpeg
    try:
        jpeg = cam_fetch("/api/go2rtc/frame.jpeg?src=" + urllib.parse.quote(stream, safe=""),
                         timeout=15)
    except Exception:
        return None
    if jpeg[:2] != b"\xff\xd8":
        return None
    with CAM_LOCK:
        CAM["frames"][printer] = (time.time(), jpeg)
    return jpeg


# ---- Queue and filament ----------------------------------------------------------------
# Also private web API, same login as the cameras: the waiting jobs with the filaments
# each needs, and what every printer has loaded slot by slot. From those we work out
# which printers could run each job and what would have to be loaded where.
QUEUE_TTL = 10        # seconds to reuse the answer
NAMES_TTL = 300       # seconds to reuse the printer id -> name table
QUEUE_PATH = ("/api/queue?status=queued&status=waiting_filament&status=assigned"
              "&status=held&limit=200")


# Jobs Watchtower is sending to a printer right now (its "dispatching" status).
SENDING_PATH = "/api/queue?status=dispatching&limit=50"


def _progress(src, loose=False):
    """A 0-100 progress figure from a job (or its metadata), if it carries one."""
    for key, value in (src or {}).items():
        k = str(key).lower()
        if (("progress" in k or "percent" in k)
                and (loose or any(w in k for w in ("upload", "dispatch", "transfer", "send")))
                and isinstance(value, (int, float)) and not isinstance(value, bool)
                and 0 <= value <= 100):
            return round(value)
    return None


def sending_jobs(queue, table):
    """{printer name: {"name": job, "pct": 0-100 or None}} for jobs being sent to a printer.

    Watchtower's queue reply has a "dispatching" list beside "jobs"; jobs can also carry
    the status "dispatching". Both are read, since neither has been seen filled in.
    """
    out = {}

    def title(j, md):
        """The job's name wherever this entry keeps it, or "" if it has none."""
        for src in (md, j):
            for key in ("original_filename", "display_name", "job_name", "filename",
                        "file_name", "gcode_filename", "subtask_name", "name", "title", "file"):
                if isinstance(src.get(key), str) and src[key].strip():
                    return _job_name({"name": os.path.basename(src[key].strip())},
                                     md if key == "original_filename" else {})
        return ""

    def add(j, loose):
        md = j.get("metadata") if isinstance(j.get("metadata"), dict) else {}
        who = j.get("printer_name") or j.get("printer_id") or j.get("printer")
        if not who:
            return
        who = (table.get(str(who)) or {}).get("name") or str(who)
        pct = _progress(j, loose)
        if pct is None:
            pct = _progress(md, loose)
        old = out.get(who) or {}
        out[who] = {"name": title(j, md) or old.get("name") or "",
                    "pct": pct if pct is not None else old.get("pct")}

    by_id = {str(j.get("id")): j for j in queue.get("jobs") or [] if isinstance(j, dict)}
    for item in queue.get("dispatching") or []:
        if isinstance(item, dict):
            keys = tuple(sorted(str(k) for k in item))
            if keys not in SENDING_SEEN:             # once per shape: helps if a name is missing
                SENDING_SEEN.add(keys)
                print("Watchtower 'dispatching' entry has fields: %s" % ", ".join(keys),
                      file=sys.stderr)
            known = next((by_id[str(item[k])] for k in ("job_id", "queue_job_id", "queue_id", "id")
                          if item.get(k) is not None and str(item[k]) in by_id), {})
            add({**known, **item, "metadata": {
                **(known.get("metadata") if isinstance(known.get("metadata"), dict) else {}),
                **(item.get("metadata") if isinstance(item.get("metadata"), dict) else {})}}, True)
        elif str(item) in by_id:
            add(by_id[str(item)], False)
    for j in queue.get("jobs") or []:
        if isinstance(j, dict) and str(j.get("status") or "").lower() == "dispatching":
            add(j, False)
    return out


SENDING_SEEN = set()


def _unwrap(reply):
    return reply.get("data", reply) if isinstance(reply, dict) else reply


def _hex(value):
    """'#C12E1F' / 'c12e1f' -> '#C12E1F', else ''."""
    v = str(value or "").strip().lstrip("#").upper()[:6]
    return "#" + v if len(v) == 6 and all(c in "0123456789ABCDEF" for c in v) else ""


def _lab(hexcolor):
    def lin(c):
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(int(hexcolor[i:i + 2], 16)) for i in (1, 3, 5))
    xyz = ((0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047,
           0.2126 * r + 0.7152 * g + 0.0722 * b,
           (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883)
    fx, fy, fz = (t ** (1 / 3.0) if t > 0.008856 else 7.787 * t + 16 / 116.0 for t in xyz)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def color_distance(a, b):
    """Perceptual difference between two '#RRGGBB' colours (CIE76 delta E)."""
    la, lb = _lab(a), _lab(b)
    return sum((x - y) ** 2 for x, y in zip(la, lb)) ** 0.5


def printer_table():
    """{printer id: {"name", "model"}} from the private printer list (shape not documented)."""
    now = time.time()
    with CAM_LOCK:
        if CAM["names"] is not None and now - CAM["names_at"] < NAMES_TTL:
            return CAM["names"]
    table = {}
    try:
        data = _unwrap(json.loads(cam_fetch("/api/printers")))
        if isinstance(data, dict) and isinstance(data.get("printers"), (list, dict)):
            data = data["printers"]
        items = data.items() if isinstance(data, dict) else ((None, p) for p in data or [])
        for key, p in items:
            if not isinstance(p, dict):
                continue
            pid = next((p[k] for k in ("id", "printer_id", "uuid") if p.get(k)), key)
            name = next((p[k] for k in ("name", "printer_name", "display_name") if p.get(k)), None)
            model = next((p[k] for k in ("normalized_printer_model", "model", "printer_model")
                          if p.get(k)), "")
            if pid and name:
                table[str(pid)] = {"name": str(name), "model": str(model)}
    except Exception:
        pass
    with CAM_LOCK:
        CAM.update(names=table, names_at=time.time() if table else 0)
    return table


def _job_name(job, md):
    name = str(md.get("original_filename") or job.get("name") or "Job %s" % job.get("id"))
    for ext in (".gcode.3mf", ".3mf", ".gcode"):
        if name.lower().endswith(ext):
            name = name[:-len(ext)]
    if md.get("plate_name"):
        name += " \u2014 %s" % md["plate_name"]
    elif (md.get("plate_count") or 0) > 1 and md.get("plate_index"):
        name += " \u2014 Plate %s" % md["plate_index"]
    return name


# ---- Order numbers ---------------------------------------------------------------------
# A job made from a shop order carries the order's internal id; the number a person knows
# it by ("#1042") is on the order itself. Looked up once per order and remembered.
ORDER_KEYS = ("order_number", "order_name", "number", "name", "external_order_number",
              "external_order_id", "external_id", "reference", "order_ref")
ORDER_NUMBERS = {}            # order id -> number as text, "" if it has none we can find
ORDER_API = {"ok": True}      # set False once Watchtower says it has no such address
ORDER_SEEN = set()


def _order_text(source):
    """An order's number from a dict that may describe one, or ""."""
    if not isinstance(source, dict):
        return ""
    for key in ORDER_KEYS:
        value = source.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).strip():
            text = str(value).strip()
            # Not the internal id again (a long run of hex and dashes).
            if not (len(text) > 20 and all(c in "0123456789abcdefABCDEF-" for c in text)):
                return text[:24]
    return ""


def _note_shape(what, source):
    """Log an unfamiliar reply's field names once (names only), to help find the number."""
    if isinstance(source, dict):
        keys = (what,) + tuple(sorted(str(k) for k in source))
        if keys not in ORDER_SEEN:
            ORDER_SEEN.add(keys)
            print("Watchtower %s has fields: %s" % (what, ", ".join(keys[1:])), file=sys.stderr)


def order_number(job, md, lookups):
    """The order number for a job, or "". lookups: a one-item list counting calls allowed."""
    for entry in job.get("linked_orders") or []:
        if isinstance(entry, dict):
            text = _order_text(entry) or _order_text(entry.get("order"))
            if text:
                return text
            _note_shape("linked order", entry)
        elif isinstance(entry, (str, int)) and str(entry).strip() and len(str(entry)) <= 20:
            return str(entry).strip()
    text = _order_text(md)
    if text:
        return text
    ident = str(md.get("order_id") or "").strip()
    if not ident:
        return ""
    with LOCK:
        if ident in ORDER_NUMBERS:
            return ORDER_NUMBERS[ident]
    if not ORDER_API["ok"] or lookups[0] <= 0:
        return ""
    lookups[0] -= 1
    found = ""
    try:
        reply = _unwrap(json.loads(cam_fetch("/api/orders/" + urllib.parse.quote(ident, safe=""), timeout=4)))
        order = reply.get("order") if isinstance(reply, dict) and isinstance(reply.get("order"), dict) else reply
        found = _order_text(order)
        if not found:
            _note_shape("order", order)
    except urllib.error.HTTPError as e:
        if e.code in (404, 405):
            ORDER_API["ok"] = False              # no such address here: stop asking
        return ""
    except Exception:
        return ""                                # try again another time
    with LOCK:
        if len(ORDER_NUMBERS) > 2000:
            ORDER_NUMBERS.clear()
        ORDER_NUMBERS[ident] = found
    return found


def _matches(need, slot, tolerance):
    if need["type"] and slot["type"] and need["type"] != slot["type"]:
        return False
    if need["variant"] and slot["variant"] and need["variant"].lower() != slot["variant"].lower():
        return False
    if not (need["color"] and slot["color"]):
        return True
    return color_distance(need["color"], slot["color"]) <= tolerance


# ---- Colour names ----------------------------------------------------------------------
# A job only gives each filament's colour as a code. People need a word, so a name is
# found in this order: a spool loaded on the farm, a spool in Watchtower's inventory,
# Bambu Lab's own colour chart, and finally a plain description ("Orange", "Dark grey").

# Bambu Lab's published colours, by sub-type. Used only when the code matches closely.
BAMBU_COLOURS = {
    "basic": {
        "FFFFFF": "Jade White", "000000": "Black", "C12E1F": "Red", "FF6A13": "Orange",
        "F4EE2A": "Yellow", "00AE42": "Bambu Green", "3F8E43": "Mistletoe Green",
        "0A2989": "Blue", "0086D6": "Cyan", "5E43B7": "Purple", "EC008C": "Magenta",
        "F55A74": "Pink", "E4BD68": "Gold", "A6A9AA": "Silver", "8E9089": "Gray",
        "F7E6DE": "Beige", "9D432C": "Brown", "6F5034": "Cocoa Brown", "847D48": "Bronze",
        "D1D3D5": "Light Gray", "545454": "Dark Gray", "F5547C": "Hot Pink",
        "FF9016": "Pumpkin Orange", "FEC600": "Sunflower Yellow", "00B1B7": "Turquoise",
        "482960": "Indigo Purple", "5B6579": "Blue Grey", "BECF00": "Bright Green",
        "9D2235": "Maroon Red", "0056B8": "Cobalt Blue",
    },
    "matte": {
        "FFFFFF": "Ivory White", "000000": "Charcoal", "DE4343": "Scarlet Red",
        "F7D959": "Lemon Yellow", "F99963": "Mandarin Orange", "E8AFCF": "Sakura Pink",
        "AE96D4": "Lilac Purple", "61C680": "Grass Green", "A3D8E1": "Ice Blue",
        "0078BF": "Marine Blue", "E8DBB7": "Desert Tan", "D3B7A7": "Latte Brown",
        "9B9EA0": "Ash Gray", "042F56": "Dark Blue", "7D6556": "Dark Brown",
        "68724D": "Dark Green", "BB3D43": "Dark Red", "757575": "Nardo Gray",
        "CBC6B8": "Bone White", "AE835B": "Caramel", "B15533": "Terracotta",
        "950051": "Plum", "4D3324": "Dark Chocolate", "C2E189": "Apple Green",
        "56B7E6": "Sky Blue",
    },
}
SPOOLS_TTL = 300
SPOOLS = {"at": 0, "list": []}


def inventory_spools():
    """[{color, name, type, variant}] from Watchtower's spool inventory (public API).
    The reply's exact shape isn't documented, so several field names are accepted."""
    now = time.time()
    with LOCK:
        if now - SPOOLS["at"] < SPOOLS_TTL:
            return SPOOLS["list"]
    found = []
    try:
        status, body, _ = upstream("GET", "/spools")
        data = json.loads(body) if status == 200 else {}
        data = data.get("data", data) if isinstance(data, dict) else data
        data = data.get("spools") if isinstance(data, dict) else data
        for sp in data or []:
            if not isinstance(sp, dict):
                continue
            hexc = next((h for h in (_hex(sp.get(k)) for k in
                                     ("color_hex", "colour_hex", "hex", "color", "colour")) if h), "")
            name = next((str(sp[k]) for k in ("color_name", "colour_name", "color", "colour", "name")
                         if sp.get(k) and not _hex(sp.get(k))), "")
            if hexc and name:
                found.append({"color": hexc, "name": name[:40],
                              "type": str(sp.get("material_type") or sp.get("material")
                                          or sp.get("type") or "").upper(),
                              "variant": str(sp.get("variant") or sp.get("subtype")
                                             or sp.get("sub_type") or "")})
    except Exception:
        found = []
    with LOCK:
        SPOOLS.update(at=time.time(), list=found)
    return found


def plain_colour(hexcolor):
    """A plain description of a colour: 'Orange', 'Dark grey', 'Light blue'..."""
    r, g, b = (int(hexcolor[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
    hi, lo = max(r, g, b), min(r, g, b)
    light, spread = (hi + lo) / 2, hi - lo
    sat = 0 if spread == 0 else spread / (1 - abs(2 * light - 1))
    if sat < 0.15 or spread < 0.08:
        return ("Black" if light < 0.12 else "Dark grey" if light < 0.4 else "Grey"
                if light < 0.7 else "Light grey" if light < 0.92 else "White")
    if hi == r:
        hue = (60 * ((g - b) / spread)) % 360
    elif hi == g:
        hue = 60 * ((b - r) / spread) + 120
    else:
        hue = 60 * ((r - g) / spread) + 240
    if (hue < 15 or hue >= 345) and light < 0.45 and sat < 0.65:
        return "Brown"                                       # dull dark red
    warm = 15 <= hue < 70                                    # orange to yellow
    if warm and (light < 0.45 if hue < 50 else light < 0.3):
        return "Brown"
    if warm and sat < 0.5:                                   # washed-out warm colours
        return "Beige" if light > 0.6 else "Brown"
    if warm and light > 0.85:
        return "Beige"
    name = ("Red" if hue < 15 or hue >= 345 else "Orange" if hue < 45 else "Yellow" if hue < 70
            else "Green" if hue < 165 else "Turquoise" if hue < 195 else "Blue" if hue < 245
            else "Purple" if hue < 290 else "Pink")
    if name == "Red" and light > 0.6:
        name = "Pink"
    if name == "Pink":
        return "Dark pink" if light < 0.3 else name
    return ("Dark " + name.lower()) if light < 0.3 else ("Light " + name.lower()) \
        if light > 0.75 else name


def colour_name_for(need, loaded):
    """The best name for a job filament's colour. `loaded` is {printer: [slots]}."""
    if not need.get("color"):
        return ""
    for slots in loaded.values():                            # 1. a spool loaded on the farm
        for sl in slots:
            if sl["name"] and sl["color"] and _matches(need, sl, 3):
                return sl["name"]
    for sp in inventory_spools():                            # 2. a spool in the inventory
        if _matches(need, sp, 3):
            return sp["name"]
    variant = (need.get("variant") or "").lower()            # 3. Bambu's colour chart
    charts = [BAMBU_COLOURS[variant]] if variant in BAMBU_COLOURS else list(BAMBU_COLOURS.values())
    for chart in charts:
        for hexc, name in chart.items():
            if color_distance(need["color"], "#" + hexc) <= 3:
                return name
    return plain_colour(need["color"])                       # 4. a plain description


def _grams_left(a):
    """Grams left on a loaded spool, or None. The printer's own percentage (read from the
    spool's tag) times the spool's full weight; Watchtower's remaining-weight figure is only
    used when there is no percentage, because it is often 0 for spools it doesn't track."""
    pct, full = a.get("remaining_percentage"), a.get("spool_initial_weight")
    if isinstance(pct, (int, float)):
        return round(pct / 100.0 * (full if isinstance(full, (int, float)) and full > 0 else 1000))
    left = a.get("spool_remaining_weight")
    return round(left) if isinstance(left, (int, float)) and left > 0 else None


def build_queue(queue, assignments, table):
    """Combine Watchtower's queue and filament assignments into what the page shows."""
    loaded, models = {}, {}
    for pid, entry in (assignments.get("printers") or {}).items():
        info = table.get(str(pid)) or {"name": "Printer %s" % str(pid)[:8], "model": ""}
        models[info["name"]] = info["model"]
        slots = []
        for a in (entry or {}).get("assignments") or []:
            if a.get("is_empty") or not a.get("material_type"):
                continue
            ams = a.get("slot_type") == "ams"
            slots.append({
                "slot": ("AMS %d" % ((a.get("slot_index") or 0) + 1)) if ams else "External",
                "order": (0 if ams else 1, a.get("system_index") or 0, a.get("slot_index") or 0),
                "type": str(a["material_type"]).upper(),
                "variant": str(a.get("variant") or ""),
                "color": _hex(a.get("spool_color_hex") or a.get("color_hex")),
                "name": str(a.get("spool_color") or a.get("color_name") or ""),
                "left": _grams_left(a)})
        slots.sort(key=lambda x: x.pop("order"))
        loaded[info["name"]] = slots

    def colour_name(need):
        return colour_name_for(need, loaded)

    jobs, needs = [], {}
    lookups = [12]                     # order numbers to fetch per refresh, at most
    for j in queue.get("jobs") or []:
        md = j.get("metadata") or {}
        fils = []
        for f in j.get("filaments") or md.get("filaments") or []:
            need = {"type": str(f.get("type") or j.get("material_type") or "").upper(),
                    "variant": str(f.get("variant") or ""), "color": _hex(f.get("color")),
                    "grams": f.get("used_g")}
            need["name"] = colour_name(need)
            fils.append(need)
        # Watchtower's exact mode wants the same colour; closest mode allows a near one.
        exact = md.get("color_match_mode") == "exact"
        tolerance = 3 if exact else float(md.get("color_tolerance") or 25)
        model = str(md.get("normalized_printer_model") or "")

        def compatible(name):
            m = models.get(name) or ""
            if not model:
                return True
            if m:
                return m.lower() == model.lower()
            return name.lower().startswith(model.lower())   # no model known: go by the name

        missing = {name: [i for i, need in enumerate(fils)
                          if not any(_matches(need, sl, tolerance) for sl in slots)]
                   for name, slots in loaded.items() if compatible(name)}
        for i, need in enumerate(fils):
            need["on"] = sorted(name for name, miss in missing.items() if i not in miss)
        # Where a printer has the filament but not enough of it: the job needs more grams
        # than the fullest matching spool holds (only when that amount is known).
        short = {}
        for name, miss in missing.items():
            for i, need in enumerate(fils):
                if i in miss or not isinstance(need["grams"], (int, float)):
                    continue
                have = [sl["left"] for sl in loaded[name]
                        if sl["left"] is not None and _matches(need, sl, tolerance)]
                if have and max(have) < need["grams"]:
                    short.setdefault(name, []).append({"i": i, "have": max(have)})
        assigned = j.get("printer_id") or None
        ready = sorted(name for name, miss in missing.items() if not miss)
        best = None
        if assigned and missing.get(assigned):
            best = {"printer": assigned, "missing": missing[assigned]}
        elif not assigned and not ready and missing:
            name = min(missing, key=lambda n: (len(missing[n]), n))
            best = {"printer": name, "missing": missing[name]}
        if best:
            for i in best["missing"]:
                want = {k: fils[i][k] for k in ("type", "variant", "color", "name")}
                if want not in needs.setdefault(best["printer"], []):
                    needs[best["printer"]].append(want)
        order = order_number(j, md, lookups)
        jobs.append({"id": j.get("id"), "status": j.get("status"),
                     # the order number, when the job belongs to one, leads the name
                     "name": ("(%s) " % order if order else "") + _job_name(j, md),
                     # made for a shop order: these lead the lists
                     "for_order": bool(order or md.get("order_id") or j.get("linked_orders")),
                     "printer": assigned, "ready": ready, "best": best, "filaments": fils,
                     # soft = Watchtower's own pick, which it may move to another printer
                     "soft": j.get("assignment_type") == "soft",
                     "exact": exact, "model": model,
                     # per printer of the right model: which filaments (by index) it lacks
                     "missing_by": missing, "short_by": short,
                     # position in Watchtower's queue (its ORDER column), top first
                     "order": j["sort_order"] if isinstance(j.get("sort_order"), (int, float))
                     else len(jobs),
                     "created": str(j.get("created_at") or ""),
                     "copies": md.get("quantity_remaining"),
                     "minutes": round(md["print_time_seconds"] / 60)
                     if md.get("print_time_seconds") else None,
                     "reason": str(md.get("filament_rejection") or "")[:300]})
    # Jobs for orders first. Within each group, jobs that can't run anywhere lead, then
    # Watchtower's own order.
    jobs.sort(key=lambda x: (not x["for_order"], not (x["best"] and not x["printer"]),
                             x["order"], x["created"]))
    return {"jobs": jobs, "loaded": loaded, "needs": needs}


# ---- Re-read filament tags --------------------------------------------------------------
# Watchtower's own page asks a Bambu printer to read an AMS slot's RFID tag again by sending
#   printer_command {"printer_name", "command": "read_slot_rfid",
#                    "args": {"ams_index", "tray_index"}}
# over its Socket.IO connection, and gets printer_command_response {"success", "message"}.
# We do the same with the logged-in session, using Socket.IO's plain-HTTP polling transport
# so no extra library is needed. One slot at a time, with a pause for the AMS to finish.
REREAD_GAP = float(os.environ.get("REREAD_GAP") or 15)     # seconds between slots
REREAD_BUSY = set()                                        # printers being re-read now


def _sio(opener, url, data=None, timeout=TIMEOUT):
    req = urllib.request.Request(url + "&t=" + secrets.token_hex(4), data=data)
    if data is not None:
        req.add_header("Content-Type", "text/plain;charset=UTF-8")
    with opener.open(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def reread_slots(printer, slots):
    """Ask the printer to re-read each (ams_index, tray_index). Returns a result per slot."""
    with CAM_LOCK:
        opener = cam_opener()
    base = root_url() + "/socket.io/?EIO=4&transport=polling"
    hello = _sio(opener, base)                               # '0{"sid": ...}'
    sid = json.loads(hello[hello.index("{"):].split("\x1e")[0])["sid"]
    url = base + "&sid=" + urllib.parse.quote(sid)

    def emit(event, payload):
        _sio(opener, url, ("42" + json.dumps([event, payload])).encode())

    def listen(seconds, want=None):
        """Read packets for a while; return the response to `want` (a slot's command) if seen."""
        end = time.time() + seconds
        while time.time() < end:
            try:
                text = _sio(opener, url, timeout=max(1, min(8, end - time.time() + 1)))
            except OSError:                                  # long-poll timed out: nothing new
                continue
            for packet in text.split("\x1e"):
                if packet == "2":
                    _sio(opener, url, b"3")                  # answer the server's ping
                elif packet.startswith("44"):
                    raise RuntimeError("Watchtower refused the live connection")
                elif packet.startswith("42") and want:
                    try:
                        event, body = json.loads(packet[2:])[:2]
                    except (ValueError, TypeError):
                        continue
                    if event == "printer_command_response" and isinstance(body, dict) \
                            and body.get("printer_name") == printer \
                            and body.get("command") == "read_slot_rfid":
                        return body
        return None

    results = []
    try:
        _sio(opener, url, b"40")                             # join the default namespace
        emit("subscribe_printer", {"printer_name": printer})
        for i, (ams, tray) in enumerate(slots):
            emit("printer_command", {"printer_name": printer, "command": "read_slot_rfid",
                                     "args": {"ams_index": ams, "tray_index": tray}})
            reply = listen(6, want=True)
            results.append({"ams": ams, "tray": tray,
                            "success": bool(reply and reply.get("success")),
                            "message": str((reply or {}).get("message")
                                           or "No answer from Watchtower")[:200]})
            # A refused slot (typically the one whose filament is loaded in the printer)
            # doesn't stop the others, and needs no wait since nothing is being read.
            if results[-1]["success"] and i < len(slots) - 1:
                listen(REREAD_GAP)                           # let the AMS finish this slot
    finally:
        try:
            _sio(opener, url, b"41")                         # leave politely
        except Exception:
            pass
    return results


def _printer_now(printer):
    """The printer as the public API reports it right now, or {}."""
    status, body, _ = upstream("GET", "/printers/" + urllib.parse.quote(printer, safe=""))
    try:
        data = json.loads(body).get("data") or {}
    except (ValueError, AttributeError):
        data = {}
    return data if status == 200 and isinstance(data, dict) else {}


def set_not_ready(printer):
    """(status, body) for POST /api/printers/{name}/not-ready: take back a Set Ready.

    The public API has no command for this, so it goes the way Watchtower's own page
    does it: the "printer_set_ready" message with ready false, over the live connection.
    """
    if not (all(cam_creds()) and root_url()):
        return 400, {"error": "Not ready needs the Watchtower login in Settings."}
    now = _printer_now(printer)
    if not now.get("online"):
        return 409, {"error": "%s is offline or unknown." % printer}
    if now.get("busy"):
        return 409, {"error": "%s has already started a job." % printer}
    if not now.get("ready"):
        return 200, {"ok": True}                             # nothing to take back
    if printer in (queue_data().get("sending") or {}):
        return 409, {"error": "A job is already being sent to %s." % printer}
    try:
        with CAM_LOCK:
            opener = cam_opener()
        base = root_url() + "/socket.io/?EIO=4&transport=polling"
        hello = _sio(opener, base)
        sid = json.loads(hello[hello.index("{"):].split("\x1e")[0])["sid"]
        url = base + "&sid=" + urllib.parse.quote(sid)
        try:
            _sio(opener, url, b"40")
            _sio(opener, url, ("42" + json.dumps(["printer_set_ready", {
                "printer_name": printer, "ready": False, "source": "setready"}])).encode())
            for _ in range(1):                               # let it land; spot a refusal
                try:
                    text = _sio(opener, url, timeout=1.5)
                except OSError:
                    break
                if any(pk.startswith("44") for pk in text.split("\x1e")):
                    return 502, {"error": "Watchtower refused the live connection."}
                if "2" in text.split("\x1e"):
                    _sio(opener, url, b"3")
        finally:
            try:
                _sio(opener, url, b"41")
            except Exception:
                pass
    except Exception as e:
        cam_reset()
        return 502, {"error": "Couldn't reach Watchtower: %s." % (getattr(e, "reason", None) or e)}
    with LOCK:
        PRINTERS["at"] = 0
    for _ in range(4):                                       # did Watchtower take it?
        time.sleep(0.7)
        now = _printer_now(printer)
        if now and not now.get("ready"):
            return 200, {"ok": True}
    if now.get("busy"):
        return 409, {"error": "%s has already started a job." % printer}
    return 502, {"error": "Watchtower still shows %s as ready." % printer}


def reread_filament(printer):
    """(status, body) for POST /api/printers/{name}/reread-filament."""
    if not (all(cam_creds()) and root_url()):
        return 400, {"error": "Re-reading filament needs the Watchtower login in Settings."}
    # Never while printing: check with the public API first.
    status, body, _ = upstream("GET", "/printers/" + urllib.parse.quote(printer, safe=""))
    try:
        data = json.loads(body).get("data") or {}
    except (ValueError, AttributeError):
        data = {}
    if status != 200 or not data.get("online"):
        return 409, {"error": "%s is offline or unknown." % printer}
    if data.get("busy"):
        return 409, {"error": "%s is printing; filament can only be re-read when it is idle." % printer}
    with LOCK:
        if printer in REREAD_BUSY:
            return 409, {"error": "%s is already re-reading its filament." % printer}
        REREAD_BUSY.add(printer)
    try:
        pid = next((k for k, v in printer_table().items() if v["name"] == printer), None)
        entry = (_unwrap(json.loads(cam_fetch("/api/filament/assignments"))).get("printers")
                 or {}).get(pid) or {}
        ams = [a for a in entry.get("assignments") or [] if a.get("slot_type") == "ams"]
        if not ams:
            return 404, {"error": "%s has no AMS slots to re-read." % printer}
        # Only the slots Watchtower shows as empty are re-read: that is how a spool put in
        # while the printer was busy looks until its tag is read. Slots that already have a
        # reading are left alone, which also avoids the slot feeding the printer (the printer
        # can't read that one) and keeps the whole thing quick.
        slots = sorted({(a.get("system_index") or 0, a.get("slot_index") or 0) for a in ams
                        if a.get("is_empty") or not a.get("material_type")})
        active = entry.get("active_source") or {}
        if active.get("source_type") == "ams":
            slots = [sl for sl in slots
                     if sl != (active.get("system_index") or 0, active.get("slot_index"))]
        with CAM_LOCK:
            CAM["queue_at"] = 0                              # next queue read is fresh
        if not slots:
            return 200, {"ok": True, "reread": 0, "results": [],
                         "note": "No empty slots to re-read."}
        results = reread_slots(printer, slots)
        notes = ["Slot %d: %s" % (r["tray"] + 1, r["message"]) for r in results if not r["success"]]
        done = sum(1 for r in results if r["success"])
        if not done:
            return 502, {"error": " ".join(notes), "results": results}
        return 200, {"ok": True, "reread": done, "note": " ".join(notes), "results": results}
    except Exception as e:
        return 502, {"error": "Couldn't re-read filament: %s." % (getattr(e, "reason", None) or e)}
    finally:
        with LOCK:
            REREAD_BUSY.discard(printer)


def queue_data(fresh=False):
    """fresh: skip the short cache (the page's Refresh button), but not more than every 2 s."""
    if not (all(cam_creds()) and root_url()):
        return {"enabled": False, "error": "", "jobs": [], "loaded": {}, "needs": {},
                "sending": {}}
    now = time.time()
    with CAM_LOCK:
        if CAM["queue"] is not None and now - CAM["queue_at"] < (2 if fresh else QUEUE_TTL):
            return CAM["queue"]
        if now < CAM["retry_at"]:
            return {"enabled": True, "error": CAM["error"], "jobs": [], "loaded": {}, "needs": {},
                    "sending": {}}
    try:
        queue = _unwrap(json.loads(cam_fetch(QUEUE_PATH)))
        assignments = _unwrap(json.loads(cam_fetch("/api/filament/assignments")))
        out = build_queue(queue if isinstance(queue, dict) else {},
                          assignments if isinstance(assignments, dict) else {}, printer_table())
        out.update(enabled=True, error="", sending={})
        try:                                     # extra: never let this break the queue view
            main = queue if isinstance(queue, dict) else {}
            sending = _unwrap(json.loads(cam_fetch(SENDING_PATH)))
            sending = sending if isinstance(sending, dict) else {}
            # One pass over both replies, so an entry that only carries a job id can
            # find its name among the jobs of either.
            out["sending"] = sending_jobs({
                "jobs": (main.get("jobs") or []) + (sending.get("jobs") or []),
                "dispatching": (main.get("dispatching") or []) + (sending.get("dispatching") or [])},
                printer_table())
        except Exception:
            pass
    except Exception as e:
        out = {"enabled": True, "jobs": [], "loaded": {}, "needs": {}, "sending": {},
               "error": "Queue unavailable: %s." % (getattr(e, "reason", None) or e)}
    with CAM_LOCK:
        CAM.update(queue=out, queue_at=time.time())
    return out


cam_reset()


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


# ---- Printer displays -------------------------------------------------------------------
# A small screen on the front of a printer (an ESP32 with a display, see display/) asks
# for a ready-to-draw summary of its own printer and can press Set Ready. Each display has
# its own token, valid for one printer only, so it needs no page login and can't touch
# anything else. Only a hash of the token is stored.
PRINTERS_TTL = 5
PRINTERS = {"at": 0, "list": None, "error": ""}
STATUS_TEXT = {"ready": "Ready for the next job", "plate": "Plate needs clearing",
               "printing": "Printing", "paused": "Paused or error", "offline": "Offline"}


def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def display_tokens():
    tokens = CFG.get("DISPLAY_TOKENS")
    return tokens if isinstance(tokens, dict) else {}


# What each display last told us about itself, and which ones have been asked to update.
# Kept in memory only: every board reports again within seconds of a restart.
DISPLAY_SEEN = {}            # token id -> {"at", "version", "screen", "name", "ip"}
DISPLAY_UPDATES = {}         # token id -> {"from": version when asked, "at": start time or None}
DISPLAY_ONLINE = 45          # seconds since its last call for a display to count as connected
UPDATE_AT_ONCE = 3           # boards told to update at the same time; the rest wait their turn
UPDATE_TIMEOUT = 600         # give up on a board that hasn't come back on a new version
FIRMWARE_URL = "https://github.com/M4-Consulting-Ltd/MakeReady/releases/download/display-latest"
FIRMWARE_FILES = {"ESP32-2432S028": "", "ESP32-2432S028-7789": "-st7789",
                  "ESP32-2432S028-9342": "-ili9342"}
FIRMWARE = {}                # screen type -> {"at", "version"}
FIRMWARE_TTL = 1800


def latest_firmware(screen):
    """Newest published firmware version for a screen type, or "" if it can't be found out."""
    if screen not in FIRMWARE_FILES:
        return ""
    now = time.time()
    with LOCK:
        got = FIRMWARE.get(screen)
        if got and now - got["at"] < (FIRMWARE_TTL if got["version"] else 300):
            return got["version"]
        FIRMWARE[screen] = {"at": now, "version": (got or {}).get("version", "")}   # one fetch at a time
    version = ""
    try:
        url = "%s/printer-display%s.manifest.json" % (FIRMWARE_URL, FIRMWARE_FILES[screen])
        with urllib.request.urlopen(url, timeout=8) as r:
            version = str(json.loads(r.read(65536)).get("version") or "")[:40]
    except Exception:
        pass
    with LOCK:
        FIRMWARE[screen] = {"at": time.time(), "version": version}
    return version


def _clean(value, limit=40):
    """Text a board sent about itself: printable ASCII only, cut short."""
    return "".join(c for c in str(value or "") if 32 <= ord(c) < 127)[:limit]


def display_seen(ident, headers, address):
    """Note a display's call. Returns True if it should update its firmware now."""
    now = time.time()
    info = {"at": now, "version": _clean(headers.get("X-Display-Version")),
            "screen": _clean(headers.get("X-Display-Screen")),
            "name": _clean(headers.get("X-Display-Name")),
            "ip": _clean(headers.get("X-Display-Ip")) or _clean(address)}
    with LOCK:
        DISPLAY_SEEN[ident] = info
        job = DISPLAY_UPDATES.get(ident)
        if not job:
            return False
        if info["version"] != job["from"]:                  # it came back on new firmware
            del DISPLAY_UPDATES[ident]
            return False
        if job["at"] is None:                               # waiting its turn
            busy = sum(1 for j in DISPLAY_UPDATES.values()
                       if j["at"] is not None and now - j["at"] < UPDATE_TIMEOUT)
            if busy >= UPDATE_AT_ONCE:
                return False
            job["at"] = now
        elif now - job["at"] > UPDATE_TIMEOUT:              # never changed version: give up
            del DISPLAY_UPDATES[ident]
            DISPLAY_SEEN[ident]["failed"] = True
            return False
        return True


# Settings that apply to every display, sent to each one with its printer's summary.
DISPLAY_DEFAULTS = {"dim": True, "dim_level": 50,     # dim after 5 minutes idle, to this %
                    "server": ""}                     # address displays should move to, if any
def app_version():
    """This server's version, like the displays': commit date and short commit hash.
    The Docker image is built with it; a git checkout is asked; otherwise unknown."""
    v = (os.environ.get("MAKEREADY_VERSION") or "").strip()
    if not v:
        try:
            v = subprocess.run(
                ["git", "-C", HERE, "-c", "safe.directory=*", "log", "-1", "--abbrev=7",
                 "--date=format-local:%Y.%m.%d", "--format=%cd-%h"],
                capture_output=True, text=True, timeout=5, env=dict(os.environ, TZ="UTC"),
            ).stdout.strip()
        except Exception:
            v = ""
    return v[:40]


VERSION = app_version()
INSTANCE = secrets.token_hex(8)                       # lets this server recognise itself


def tidy_address(text):
    """'192.168.2.2:8080/' -> 'http://192.168.2.2:8080', as the displays do it. '' if unusable."""
    text = str(text or "").strip().rstrip("/")
    if not text:
        return ""
    if "://" not in text:
        text = "http://" + text
    parts = urllib.parse.urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.netloc or len(text) > 100 \
            or any(ord(c) < 33 or ord(c) > 126 for c in text):
        return ""
    return text


def reaches_this_server(address):
    """True if that address answers as this very server (asked from here, not from a display)."""
    try:
        with urllib.request.urlopen(address + "/api/session", timeout=4) as r:
            return json.loads(r.read(4096)).get("instance") == INSTANCE
    except Exception:
        return False


def display_settings():
    saved = CFG.get("DISPLAY_SETTINGS")
    out = dict(DISPLAY_DEFAULTS)
    if isinstance(saved, dict):
        out["dim"] = bool(saved.get("dim", out["dim"]))
        try:
            out["dim_level"] = max(5, min(90, int(saved.get("dim_level", out["dim_level"]))))
        except (TypeError, ValueError):
            pass
        out["server"] = tidy_address(saved.get("server"))
    return out


def display_settings_save(data):
    """Store what the page sent (unknown or bad values keep what was there)."""
    now = display_settings()
    if isinstance(data.get("dim"), bool):
        now["dim"] = data["dim"]
    if isinstance(data.get("dim_level"), (int, float)) and not isinstance(data.get("dim_level"), bool):
        now["dim_level"] = max(5, min(90, int(data["dim_level"])))
    if isinstance(data.get("server"), str):
        now["server"] = tidy_address(data["server"])
    with LOCK:
        CFG["DISPLAY_SETTINGS"] = now
        save_config()
    return now


def display_list():
    now = time.time()
    out = []
    for h, name in sorted(display_tokens().items(), key=lambda x: x[1]):
        ident = h[:8]
        with LOCK:
            seen = dict(DISPLAY_SEEN.get(ident) or {})
            job = dict(DISPLAY_UPDATES.get(ident) or {})
        latest = latest_firmware(seen.get("screen", "")) if seen else ""
        version = seen.get("version", "")
        out.append({
            "id": ident, "printer": name,
            "seen": round(now - seen["at"]) if seen else None,     # seconds ago, None = never
            "online": bool(seen) and now - seen["at"] < DISPLAY_ONLINE,
            "version": version, "screen": seen.get("screen", ""),
            "name": seen.get("name", ""), "ip": seen.get("ip", ""),
            "latest": latest,
            # can it be told to update? Only firmware that reports its version obeys.
            "outdated": bool(version and latest and version != latest),
            "updating": "waiting" if job and job.get("at") is None else "now" if job else "",
            "failed": bool(seen.get("failed"))})
    return out


def display_update(idents):
    """Ask these displays (token ids) to update. Returns how many were asked."""
    asked = 0
    for d in display_list():
        if d["id"] in idents and d["online"] and d["outdated"] and not d["updating"]:
            with LOCK:
                DISPLAY_UPDATES[d["id"]] = {"from": d["version"], "at": None}
                DISPLAY_SEEN.get(d["id"], {}).pop("failed", None)
            asked += 1
    return asked


def display_create(printer):
    token = secrets.token_urlsafe(24)
    with LOCK:
        tokens = dict(display_tokens())
        tokens[_token_hash(token)] = printer
        CFG["DISPLAY_TOKENS"] = tokens
        save_config()
    return token


def display_revoke(ident):
    with LOCK:
        tokens = {h: n for h, n in display_tokens().items() if h[:8] != ident}
        DISPLAY_SEEN.pop(ident, None)
        DISPLAY_UPDATES.pop(ident, None)
        CFG["DISPLAY_TOKENS"] = tokens
        save_config()


def display_printer(headers):
    """The printer a request's token is for, or None."""
    auth = str(headers.get("Authorization") or "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not token:
        return None
    want = _token_hash(token)
    for h, name in display_tokens().items():
        if hmac.compare_digest(h, want):
            return name
    return None


def display_ident(headers):
    """The token id (as shown in Settings) for a request already known to be a display's."""
    auth = str(headers.get("Authorization") or "")
    return _token_hash(auth[7:].strip())[:8]


def printers_cached():
    """(list of printers from the public API, error). Shared by every display for 5 s."""
    now = time.time()
    with LOCK:
        if PRINTERS["list"] is not None and now - PRINTERS["at"] < PRINTERS_TTL:
            return PRINTERS["list"], PRINTERS["error"]
    status, body, _ = upstream("GET", "/printers")
    found, err = [], ""
    try:
        data = json.loads(body)
        if status != 200:
            err = str(data.get("error") or "Watchtower returned HTTP %d" % status)[:120]
        else:
            data = data.get("data", data) if isinstance(data, dict) else data
            data = data.get("printers") if isinstance(data, dict) else data
            found = [p for p in data or [] if isinstance(p, dict) and p.get("name")]
    except (ValueError, AttributeError):
        err = "Watchtower returned HTTP %d" % status
    with LOCK:
        PRINTERS.update(at=time.time(), list=found, error=err)
    return found, err


def printer_state(p):
    """Same rules as the page (and ClearCue)."""
    if not p.get("online"):
        return "offline"
    if p.get("busy"):
        words = " ".join(str(p.get(k) or "") for k in ("status", "state", "print_state",
                                                       "printStatus", "job_status",
                                                       "gcode_state")).upper()
        return "paused" if any(w in words for w in ("PAUSE", "ERROR", "FAULT", "HALT")) \
            else "printing"
    return "ready" if p.get("ready") else "plate"


def _fil_text(f):
    """Display wording: colour name and weight first (a small screen cuts off the end)."""
    grams = f.get("grams")
    weight = "%sg" % ("<1" if 0 < grams < 1 else round(grams)) \
        if isinstance(grams, (int, float)) and grams > 0 else ""
    kind = " ".join(x for x in (f.get("type"), f.get("variant")) if x)
    return " ".join(x for x in (f.get("name"), weight, kind) if x) or "Filament"


def display_summary(name, max_jobs=8):
    """Everything a printer's display shows, already worded and trimmed for a small screen."""
    out = {"title": name, "state": "unknown", "status": "", "sub": "", "head": "", "good": False,
           "job": "", "fil": [], "warn": "", "button": "",
           "pct": -1, "left": "", "done": "",             # progress bar and big times
           "bar": "",                                     # wording for the bar, when not printing
           "resume": False,                               # offer "Resume" (paused)
           "undo": False,                                 # offer "Not ready" (takes Set Ready back)
           "queue": [], "more": 0,                        # second page: waiting jobs
           "jobs": [], "jmore": 0}                        # the same, longer, with each job's filaments
    printers, err = printers_cached()
    p = next((x for x in printers if x.get("name") == name), None)
    if p is None:
        out["status"] = err or "Not found in Watchtower"
        return out
    state = printer_state(p)
    out["state"], out["status"] = state, STATUS_TEXT[state]
    if state == "printing" and isinstance(p.get("progress"), (int, float)):
        out["pct"] = max(0, min(100, round(p["progress"])))   # Watchtower reports 0-100
        out["status"] += " %d%%" % out["pct"]
    mins = p.get("eta_minutes")
    if state in ("printing", "paused") and isinstance(mins, (int, float)) and mins >= 0:
        mins = round(mins)
        out["left"] = ("%dh %dm" % divmod(mins, 60)) if mins >= 60 else "%dm" % mins
        out["sub"] = out["left"] + " left"
        if state == "printing":
            out["done"] = time.strftime("%H:%M", time.localtime(time.time() + mins * 60))
            out["sub"] += ", done " + out["done"]
    if state == "plate":
        out["button"] = "SET READY"

    q = queue_data()
    # The jobs this printer could take: the display's second page. Jobs for orders come
    # first, then Watchtower's order. `queued` keeps Watchtower's order alone, for saying
    # which job Set Ready will actually pick up.
    def listed(j):
        return (not j.get("for_order"), j.get("order") or 0, j.get("created") or "")
    queued = sorted((j for j in q.get("jobs") or []
                     if name in (j.get("missing_by") or {})
                     and (not j.get("printer") or j["printer"] == name)),
                    key=lambda j: (j.get("order") or 0, j.get("created") or ""))
    mine = sorted(queued, key=listed)
    for j in mine[:4]:
        mins = j.get("minutes")
        out["queue"].append({
            "n": j["name"],
            "t": ("%dh %dm" % divmod(mins, 60)) if isinstance(mins, int) and mins >= 60
                 else ("%dm" % mins if isinstance(mins, int) else ""),
            "ok": not j["missing_by"][name],                 # this printer has every filament
            "c": [(f.get("color") or "").lstrip("#") for f in j["filaments"]][:4]})
    out["more"] = max(0, len(mine) - 4)
    # Newer displays list more jobs and open one to show its filaments: what the job
    # needs, and whether this printer has each one loaded.
    # This list is wider than the advice above: every waiting job for this printer's
    # model that could still end up here. That is queued jobs, jobs assigned to this
    # printer, and jobs soft-assigned to another one (Watchtower may move those once the
    # right filament is loaded here). Jobs fixed to another printer are left out.
    wide = sorted((j for j in q.get("jobs") or []
                   if name in (j.get("missing_by") or {})
                   and (not j.get("printer") or j["printer"] == name or j.get("soft"))),
                  key=listed)
    for j in wide[:max_jobs]:
        mins = j.get("minutes")
        lacking = j["missing_by"][name]
        out["jobs"].append({
            "i": j.get("id"),                                # for "print this next"
            # where the job stands: "Queued", "Assigned here", "Soft: P1S-3"
            "a": "Queued" if not j.get("printer")
                 else ("Soft: here" if j.get("soft") else "Assigned here") if j["printer"] == name
                 else "Soft: %s" % j["printer"],
            "n": j["name"][:60],
            "t": ("%dh %dm" % divmod(mins, 60)) if isinstance(mins, int) and mins >= 60
                 else ("%dm" % mins if isinstance(mins, int) else ""),
            "ok": not lacking,
            "c": [(f.get("color") or "").lstrip("#") for f in j["filaments"]][:4],
            "f": [{"t": _fil_text(f)[:40], "ok": i not in lacking}
                  for i, f in enumerate(j["filaments"])][:4],
            "x": max(0, len(j["filaments"]) - 4)})          # filaments that didn't fit
    out["jmore"] = max(0, len(wide) - max_jobs)
    if state in ("plate", "ready"):
        runnable = [j for j in queued if not j["missing_by"][name]]
        if runnable:
            j = runnable[0]
            out.update(good=True, job=j["name"],
                       head="Set Ready picks up:" if state == "plate" else "Next job:")
            short = (j.get("short_by") or {}).get(name) or []
            if short:
                f = j["filaments"][short[0]["i"]]
                out["warn"] = "Low: %s, %dg left" % (_fil_text(f), short[0]["have"])
        elif mine:
            j = mine[0]                                   # the job highest in the queue
            out.update(head="Load for next job:", job=j["name"],
                       fil=[{"t": _fil_text(j["filaments"][i]),
                             "c": (j["filaments"][i].get("color") or "").lstrip("#")}
                            for i in j["missing_by"][name]][:4])
    # A job on its way to this printer replaces any advice: nothing to do but wait.
    got = (q.get("sending") or {}).get(name)
    if got and state != "offline":
        pct = got.get("pct")
        out.update(head="Receiving job:", good=True, job=got["name"],
                   fil=[], warn="", button="", pct=-1 if pct is None else pct,
                   bar="RECEIVING" if pct is None else "RECEIVING %d%%" % pct)
    # Ready with no job on its way yet: Set Ready can still be taken back.
    out["undo"] = bool(state == "ready" and not got and all(cam_creds()) and root_url())
    out["resume"] = state == "paused"
    # While the printer is busy the display shows nothing about waiting jobs: what to load
    # only matters once the print is done. (The web page still shows it on busy printers.)
    # The display's font only has plain ASCII: swap the common extras, drop the rest.
    def plain(text):
        text = str(text).replace("\u2014", "-").replace("\u2013", "-").replace("\u2026", "...")
        return text.encode("ascii", "replace").decode()
    for key in ("title", "status", "sub", "head", "job", "warn"):
        out[key] = plain(out[key])
    for f in out["fil"]:
        f["t"] = plain(f["t"])
    for row in out["queue"] + out["jobs"]:
        row["n"] = plain(row["n"])
        if "a" in row:
            row["a"] = plain(row["a"])
        for f in row.get("f") or []:
            f["t"] = plain(f["t"])
    # The display reads at most 6 KB: drop jobs off the end of the long list if need be.
    # (Firmware that lists more than eight jobs reads up to 16 KB.)
    while out["jobs"] and len(json.dumps(out)) > (5500 if max_jobs <= 8 else 15000):
        out["jobs"].pop()
        out["jmore"] += 1
    return out


# The only printer commands the page can send: URL suffix -> Watchtower command.
# "stop" cancels the job; Watchtower wants confirm:true with it. The page asks first.
COMMANDS = {"set-ready": {"command": "set_ready"},
            "resume": {"command": "resume"},
            "pause": {"command": "pause"},
            "stop": {"command": "cancel", "confirm": True}}


# ---- Home-screen icon ---------------------------------------------------------------------
# So "Add to Home Screen" on a phone or tablet gives a proper icon and opens the page full
# screen. The pictures are kept in this file (base64 PNG) so there is still nothing to
# deploy but server.py and index.html. They are public: a browser fetches them without the
# page's login. The drawing is docs/icon.svg on a full square, with room for round masks.
MANIFEST = json.dumps({
    "name": "MakeReady", "short_name": "MakeReady", "start_url": ".", "scope": ".",
    "display": "standalone", "background_color": "#111317", "theme_color": "#1b1e24",
    "icons": [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
              {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}],
}).encode()
ICONS = {
    "/apple-touch-icon.png": (
        "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"
    ),
    "/icon-192.png": (
        "iVBORw0KGgoAAAANSUhEUgAAAMAAAADACAIAAADdvvtQAAAMRUlEQVR4Aeyca2wU1xXHZx9+7Jr6ucYuuC1u"
        "UnBMwRg7SdVWbfK5REqUD00jEaKCGzW4FalxKtGWKFUKjXgTKYEImkZNolRNmxRQk/ZLqpZKEEHAOAHjBkcQ"
        "geMnfq8f+8gZjIDF9u7eO7Mz9/FfnTV3Zu49557/+THcHa7Xu/Crd8KgALcCXgMvKGBBAQBkQTwMNQwABAos"
        "KQCALMmHwQAIDFhSAABZkg+DuQGCdFDAVAAAmSrgza0AAOKWDgNNBQCQqQLe3AoAIG7pMNBUAACZKuDNrQAA"
        "4pYOA00FnAfIjIq3MgoAIGVK6U4iAMgd3ZWJCoCUKaU7iQAgd3RXJioAUqaU7iQCgNzRXZmoEgGkjOZKJQKA"
        "lCqn88kAIOc1VyoiAFKqnM4nA4Cc11ypiABIqXI6nwwAcl5zpSLqAJBSBRMtGQAkWkUkmw8Akqxgok0XAIlW"
        "EcnmA4AkK5ho0wVAolVEsvkAIMkKJtp0AVCSiuBSagUAUGqN0COJAgAoiTi4lFoBAJRaI/RIogAASiIOLqVW"
        "AACl1gg9kigAgJKIg0upFQBAqTVi76HRCC0Aqq+rq3f8VbdypQ4caQHQvn0vHjzwssNGQQGQDgogR0sKaHEH"
        "sqQQBidVAAAllQcXUykAgFIphOtJFQBASeVx+qJ88QCQfDUTasYASKhyyDcZACRfzYSaMQASqhzyTQYAyVcz"
        "oWYMgIQqh3yTAUDy1Wy2Gbt2DgC5Jr0agQGQGnV0LQsA5Jr0agQGQGrU0bUsAJBr0qsRGACpUUfXsgBArkkv"
        "RmCrs9ACoN2791jViX38nj172QfJN0ILgF57/Y333/+3k8WhcBTUyYhuxdICIBL3N5s3X75yhRoOGAWicA4E"
        "EiGELgAND480Nz89NTWVadEpBAWicJkOJIh/XQAiuT/++KwDiyEKQYEonCamEUBUUVqX0OqEGhkyck4hMuRc"
        "TLd6AUQ1oNUJrVGoYbuRW3Juu1tRHV6fl3YA0eqE1ii0UrkugE1/kENyS85t8ieNG+0AosrQGoVWKtSw0Z5/"
        "fhu5tdGhLK50BIhqQysVWq9QwxY7dOjwX956yxZX0jnRFCCqE61XaNVCDYt24ULH77ZstehE3uH6AkTrFVq1"
        "0NrFSvHC4fDG5ubx8XErTqQeqy9AVDZatVhcDG3Z+vuOjk/JlbamNUBUdSuLIVr6kJETnY0dIOXU4lsMab70"
        "uUEBADI4FkNY+gCgGwqYDdbFEJY+pmrX3rgDXZPBMGgxtG3b9pf27U9pzz23hXXpk1s/v3Bddekzdy/Yf3/p"
        "s/dQO3BP2fXAkv8BgG4WkBjat29/SmN7Zuj3Fjy2pLhhafDusqwF8yhYVnketYvWVpdsqPEWZNMZqQ0AZbJ8"
        "Ob5Qc23edxbMGiPnruLQL+uMXN+sV2U5CYAyVim/N7ShJntRfpIA/pLcojVVSTqIf8lBgMQXw8YZej0ljcuy"
        "v16Q0mVg5XxfeTBlN2E7AKAMlMZjFD2xlP6FStN1bnVxmj0F7AaA7C9K4eN3BVaUpu83u7oo/c6i9QRANlek"
        "4JFvBL9VzuTUG8hi6i9UZwBkZznmrVqUd38Fq8dI5yjrEHH6AyDbahG8b2H+A5Uc7gAQh2iqDQncW1b4o8Uc"
        "WcXCkfAHXRwDBRkiwx1IEKnmnkZuTYgWznNfT3Zl4M322HDGf90x2QysXQNA1vQzjJylxcVPLvN4PRyOxo5/"
        "Pn5M4tsPpQyASAR+y6rML/7pN/nGj7f2Dbxyjm+sOKMAEH8t/AvzSjbUeLJ8HC4m2gf6X2o14hxDxRoCgDjr"
        "4ZsfCDXVenP9HOMnLw73720xovLjYxgAiAMAw1eSG9pY683jeQA41Tnat/NUfCrGE1i8MQCIuSbe/Gyix1eQ"
        "wzzSMCK94b7tp+LjUY6xYg5RGqAMSO4J+k16inM5fEcHJ3q3n4qNSPyhfWbWAGimJnOe8eT4aN3jL+PZfREb"
        "nTLpuToxp3c5LygCUPD7Cwsbquc/e2/53u+FNtUVrF6SvaTQ5opkeYp/vjyrwtyWyuo5Nhbp3XU62h1mHSh+"
        "f+kB8pcHS39dX/jo4mB9GbW9Ob7sr+XnfXdB6Be1hJEnwPMZe5ayeT0l65fn3MkDZWwy2rfrVOSzkVncyn9K"
        "boB8RTmhTfVZX/nSrIUgjEo31dOad9arDCcZN4jd6jkeidEn9qlLatJDmcoNUFHDUrrlUBpzmX9+MNRca5Gh"
        "QsYNYjcmE4/F6Wnh5P8Hb5xRryExQFl35GffkXrTsUWGODaITVMSj8evHjg78VH/9KGqPyUGKB16psvGzND0"
        "MMOY98Aijg1i06MHXzs/frJ7uq3wTy0AovpxMBS8b2H+Kp4NYhRu6O0LY0c7qaG8SQyQrzDHYHmZDG2k9VBa"
        "//8Q+HY53wYxmtHwexdH3rtEDR1MYoAiV5i3EtMzwFDzypRranOD2GrO3/cbO/b58NsdOqAznaPEAE1xPVkx"
        "70NJP5dlVxUVPbGUb4NY+ETXwB+l3+IzTUaaPyUGiP6uR4cn08zz1m5JGMqqzC9pXObx8cgy3tpHH7sMFfZo"
        "3KpWijaPUilcOnU5PhYZOHiWL9qsDGGDGIeYEgNE2U6cuzr0twvU4DCToVvW1L4vB0NNK/g2iE18Otj3wrUN"
        "YhzzkHyI3ACR+CP/vMTPUBk9pzbX1OYGsadWePOyySGrTXWO9u9uMSYV2SDGmr70AFHCJkPvWLgPPV1b0lTr"
        "49ogNtU91rtDqQ1ipCeTqQAQJTzyroX7UGnQX8K5Qaxv5+m4zL/VRdJZNEUAIhXM+xDveoiGs5qqG8RYdVAH"
        "IMrcMYZi4Qj9y6XkBjGSkcmUAogyd4Ch2GS0b+epyGXm5+A0PfVMNYCoQsTQ4N8z+J8J/S+22r9BjOYtpykI"
        "EBVi9B8XuT/b0/C5LB6L0/OeyXNX5+qg4Xk1AaJC0n3IXoY02SBG0jGZsgCRCvYypMkGMdKNyVQGiISwi6Eh"
        "bTaIkWhMpjhApIXJEO9zahpOptUGMcqXydQHiOSw8px69L9XhnXaIEZyMZkWAJEi5n2I/Tl1+EQXLX1ouLjm"
        "9sx0AYh0ZmXo+gYxGgmbWwGNACIR0mdImW8Qo6wzanoBRFKmw5BK3yBGKWfUtAOI1CSG+l44Ex2c5ZtW4pPR"
        "wb9+0rP1hDLfIEb5ZtR0BIgEnfior2vz8aF3L9LNhg7JIr3hkf9c7v7tB6P/+syj2cZ4Sp/bNAXI1Gs8OvJO"
        "R++WE51NR7ueOd79q2NDr7dHe8bNS3inrYDoAFVVVdVn+FVXVVNbUZ3hIJzuKf20S8nY0abuggJUU7P8T6++"
        "2nL6wz+/+cbBAy9ra5Q+iUBSkCA2VdxmN8IB5Pf7GxvXv/KHg8trltmcq7TuSAoSpHH9kySOaEkIB9BDDz7Y"
        "sG6tz2fTV9OJpjfvfEiQhoZ1JA6vg0yNEwugioqKpqanMpWr/H5JHJJIqDzEAujhhx8KBAJCCSTUZEicVat+"
        "INSUxAKochHnFzoJpWlGJ1NZuSij/lmdiwaQWOqwqulA/6rFSxyIkixE4jWxAGprP584PRzdroBoEokF0Pm2"
        "9tsFw3GiAqJJJBZAR44cGRgYSFQMRzcVGBwcPHzk8M1jAVpiAdTd07Nj5y4BZBF0Ctt37Ozp6RVqcmIBRNIc"
        "OnT45MkPqQG7TYGjR/9H4tx20vVD4QAiRX68dt1jax4/09JKbRgpQFKQIOsbf0Zt0UxEgEijlpYzq9esqVmx"
        "8oePPLp23U+0NUqfRCApSBCSRUBjAMiV2be1tZ3Q+EXpuyJ7+kFFByj9TNDTFQUAkCuyqxMUAKlTS1cyAUCu"
        "yK5OUACkTi1dyQQAuSK7OkGdAEgdtZDJDAUA0AxJcIJFAQDEohb6zlAAAM2QBCdYFABALGqh7wwFANAMSXCC"
        "RQEAxKIW+s5QQGiAZswWJ4RTAAAJVxK5JgSA5KqXcLMFQMKVRK4JASC56iXcbAGQcCWRa0IASK56CTdbNQES"
        "TmZ1JwSA1K2tI5kBIEdkVjcIAFK3to5kBoAckVndIABI3do6khkAckRmdYMAoITa4oBVAQDEqhj6JygAgBLk"
        "wAGrAgCIVTH0T1AAACXIgQNWBQAQq2Lon6AAAEqQAwesCgAgVsVm76/tWQCkbentSRwA2aOjtl4AkLaltydx"
        "AGSPjtp6AUDalt6exAGQPTpq6wUAuVx62cN/AQAA//9lt91xAAAABklEQVQDABJzzIyZhHU6AAAAAElFTkSu"
        "QmCC"
    ),
    "/icon-512.png": (
        "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"
    ),
}
ICONS = {path: base64.b64decode(data) for path, data in ICONS.items()}


class Handler(BaseHTTPRequestHandler):
    server_version = "MakeReady/1.0"

    def send(self, status, body, ctype="application/json", retry_after=None, cookie=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # Don't let another site frame the page (to trick someone into pressing a button),
        # sniff content types, or learn the address from links.
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if retry_after:
            self.send_header("Retry-After", retry_after)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def authed(self):
        return not auth_required() or session_valid(self.headers.get("Cookie"))

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                self.send(200, f.read(), "text/html; charset=utf-8")
        elif path in ICONS:
            self.send(200, ICONS[path], "image/png")
        elif path == "/manifest.webmanifest":
            self.send(200, MANIFEST, "application/manifest+json")
        elif path == "/api/session":
            authed = self.authed()
            self.send(200, json.dumps({"required": auth_required(), "authed": authed,
                                       "instance": INSTANCE,
                                       # not shown to someone who hasn't logged in
                                       "version": VERSION if authed or not auth_required() else "",
                                       }).encode())
        elif path == "/api/display":
            # A printer display, identified by its own token instead of a page login.
            name = display_printer(self.headers)
            if name is None:
                self.send(401, b'{"error":"Unknown display token"}')
            else:
                # Newer firmware says how many jobs its list can hold; older lists eight.
                try:
                    most = max(8, min(40, int(self.headers.get("X-Display-Jobs") or 8)))
                except ValueError:
                    most = 8
                out = display_summary(name, most)
                # The farm-wide display settings: how far to dim when idle, -1 for never.
                conf = display_settings()
                out["dim"] = conf["dim_level"] if conf["dim"] else -1
                if conf["server"]:
                    out["server"] = conf["server"]         # where displays should call from now on
                # Tell the server's list this display is alive; pass on a request to update.
                if display_seen(display_ident(self.headers), self.headers, self.client_address[0]):
                    out["update"] = True
                self.send(200, json.dumps(out).encode())
        elif not self.authed():
            self.send(401, b'{"error":"Log in to use this page.","login":true}')
        elif path == "/api/displays":
            # fresh=1 (Settings just opened, or "Check now"): look for new firmware again,
            # but not more than twice a minute.
            if "fresh=1" in self.path.partition("?")[2].split("&"):
                with LOCK:
                    for got in FIRMWARE.values():
                        if time.time() - got["at"] > 30:
                            got["at"] = 0
            self.send(200, json.dumps({"displays": display_list(),
                                       "settings": display_settings()}).encode())
        elif path == "/api/config":
            self.send(200, json.dumps(public_config()).encode())
        elif path == "/api/queue":
            fresh = "fresh=1" in self.path.partition("?")[2].split("&")
            self.send(200, json.dumps(queue_data(fresh)).encode())
        elif path == "/api/cameras":
            streams, err = cam_streams()
            self.send(200, json.dumps({"enabled": all(cam_creds()), "error": err,
                                       "printers": sorted(streams)}).encode())
        elif path.startswith("/api/camera/"):
            jpeg = cam_frame(urllib.parse.unquote(path[len("/api/camera/"):]))
            if jpeg:
                self.send(200, jpeg, "image/jpeg")
            else:
                self.send(404, b'{"error":"No camera frame"}')
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
        if parts == ["", "api", "login"]:
            client = self.client_address[0]
            if not login_allowed(client):
                self.send(429, b'{"error":"Too many wrong passwords. Wait a minute and try again."}',
                          retry_after=str(FAIL_WINDOW))
                return
            try:
                password = str((json.loads(raw or b"{}") or {}).get("password") or "")
            except (ValueError, AttributeError):
                password = ""
            if not auth_required():
                self.send(200, b'{"ok":true}')
            elif check_password(password):
                self.send(200, b'{"ok":true}', cookie=session_cookie())
            else:
                login_failed(client)
                self.send(401, b'{"error":"Wrong password."}')
            return
        if parts == ["", "api", "logout"]:
            self.send(200, b'{"ok":true}', cookie=session_cookie(clear=True))
            return
        if parts == ["", "api", "display", "set-ready"]:
            # From a printer display: only Set Ready, only for the token's own printer.
            name = display_printer(self.headers)
            if name is None:
                self.send(401, b'{"error":"Unknown display token"}')
                return
            status, body, ra = upstream("POST", "/printers/%s/commands"
                                        % urllib.parse.quote(name, safe=""), COMMANDS["set-ready"])
            with LOCK:
                PRINTERS["at"] = 0                           # show the new state straight away
            self.send(status, body or b"{}", retry_after=ra)
            return
        if parts == ["", "api", "display", "next"]:
            # From a printer display: put one of the jobs on its own list at the top of
            # Watchtower's queue.
            name = display_printer(self.headers)
            if name is None:
                self.send(401, b'{"error":"Unknown display token"}')
                return
            want = urllib.parse.parse_qs(self.path.partition("?")[2]).get("job", [""])[0]
            listed = [j for j in queue_data().get("jobs") or []
                      if name in (j.get("missing_by") or {})
                      and (not j.get("printer") or j["printer"] == name or j.get("soft"))]
            if not want or not any(str(j.get("id")) == want for j in listed):
                self.send(404, b'{"error":"That job is not on this display\'s list."}')
                return
            status, out = queue_next(want)
            # Someone standing at a printer that is waiting for Set Ready, picking a job
            # it has the filament for, wants that job on this printer: set it ready too.
            # Without the filament Set Ready would only bring some other job, so then the
            # job just moves up.
            job = next(j for j in listed if str(j.get("id")) == want)
            printers, _ = printers_cached()
            p = next((x for x in printers if x.get("name") == name), None)
            if (status == 200 and p is not None and printer_state(p) == "plate"
                    and not job["missing_by"][name]):
                code, _body, _ra = upstream("POST", "/printers/%s/commands"
                                            % urllib.parse.quote(name, safe=""),
                                            COMMANDS["set-ready"])
                out["ready"] = code < 300
                with LOCK:
                    PRINTERS["at"] = 0
            self.send(status, json.dumps(out).encode())
            return
        if parts == ["", "api", "display", "resume"]:
            # From a printer display: resume a paused print, only on the token's own printer.
            name = display_printer(self.headers)
            if name is None:
                self.send(401, b'{"error":"Unknown display token"}')
                return
            status, body, ra = upstream("POST", "/printers/%s/commands"
                                        % urllib.parse.quote(name, safe=""), COMMANDS["resume"])
            with LOCK:
                PRINTERS["at"] = 0
            self.send(status, body or b"{}", retry_after=ra)
            return
        if parts == ["", "api", "display", "not-ready"]:
            # From a printer display: take Set Ready back, only for the token's own printer.
            name = display_printer(self.headers)
            if name is None:
                self.send(401, b'{"error":"Unknown display token"}')
                return
            status, out = set_not_ready(name)
            self.send(status, json.dumps(out).encode())
            return
        if not self.authed():
            self.send(401, b'{"error":"Log in to use this page.","login":true}')
            return
        if parts[:3] == ["", "api", "displays"]:
            try:
                data = json.loads(raw or b"{}")
            except ValueError:
                data = {}
            if len(parts) == 4 and parts[3] == "settings":
                # A new address for the displays is checked first: sending a wrong one would
                # send every display looking for a server that isn't there.
                want = data.get("server")
                if isinstance(want, str) and want.strip():
                    address = tidy_address(want)
                    if not address:
                        self.send(400, b'{"error":"That is not an address a display can use."}')
                        return
                    if not data.get("force") and not reaches_this_server(address):
                        self.send(409, json.dumps({"unverified": True, "error":
                            "This server couldn't reach itself at %s." % address}).encode())
                        return
                self.send(200, json.dumps({"settings": display_settings_save(data)}).encode())
            elif len(parts) == 4 and parts[3] == "update":
                want = data.get("ids")
                want = {str(i) for i in want} if isinstance(want, list) \
                    else {d["id"] for d in display_list()}          # no list = every display
                asked = display_update(want)
                self.send(200, json.dumps({"asked": asked, "displays": display_list()}).encode())
            elif len(parts) == 4 and parts[3] == "revoke":
                display_revoke(str(data.get("id") or ""))
                self.send(200, json.dumps({"displays": display_list()}).encode())
            elif len(parts) == 3 and str(data.get("printer") or "").strip():
                token = display_create(str(data["printer"]).strip())
                self.send(200, json.dumps({"token": token, "displays": display_list()}).encode())
            else:
                self.send(400, b'{"error":"Choose a printer."}')
            return
        if len(parts) == 5 and parts[:3] == ["", "api", "queue"] and parts[4] == "next":
            status, out = queue_next(urllib.parse.unquote(parts[3]))
            self.send(status, json.dumps(out).encode())
            return
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
                # Setting or changing the page password ends every session; keep this one.
                self.send(200, json.dumps(public_config()).encode(),
                          cookie=session_cookie() if auth_required() else None)
            return
        if len(parts) == 5 and parts[:3] == ["", "api", "printers"] and parts[3] \
                and parts[4] == "reread-filament":
            status, out = reread_filament(urllib.parse.unquote(parts[3]))
            self.send(status, json.dumps(out).encode())
            return
        if len(parts) == 5 and parts[:3] == ["", "api", "printers"] and parts[3] \
                and parts[4] == "not-ready":
            status, out = set_not_ready(urllib.parse.unquote(parts[3]))
            self.send(status, json.dumps(out).encode())
            return
        # /api/printers/{name}/<one of COMMANDS> ; {name} arrives URL-encoded
        if len(parts) == 5 and parts[:3] == ["", "api", "printers"] and parts[3] \
                and parts[4] in COMMANDS:
            name = urllib.parse.quote(urllib.parse.unquote(parts[3]), safe="")
            status, body, ra = upstream("POST", "/printers/%s/commands" % name,
                                        COMMANDS[parts[4]])
            self.send(status, body or b"{}", retry_after=ra)
        else:
            self.send(404, b'{"error":"Not found"}')

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))


def main():
    host = str(CFG.get("HOST", "127.0.0.1"))
    port = int(CFG.get("PORT", 8080))
    print("MakeReady on http://%s:%d  ->  %s"
          % (host, port, base_url() or "(not set up: open the page and use Settings)"))
    if not auth_required() and host not in ("127.0.0.1", "localhost", "::1"):
        print("WARNING: no page password is set, so anyone who can reach this address can "
              "use the page. Set one under Settings.", file=sys.stderr)
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass

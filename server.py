#!/usr/bin/env python3
"""Watchtower SetReady: virtual "Set Ready" buttons for a Watchtower printer farm.

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
import hashlib
import hmac
import http.cookiejar
import json
import os
import secrets
import ssl
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
              "SESSION_SECRET")


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
    cam_reset()
    return None


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


def _matches(need, slot, tolerance):
    if need["type"] and slot["type"] and need["type"] != slot["type"]:
        return False
    if need["variant"] and slot["variant"] and need["variant"].lower() != slot["variant"].lower():
        return False
    if not (need["color"] and slot["color"]):
        return True
    return color_distance(need["color"], slot["color"]) <= tolerance


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
        for slots in loaded.values():
            for sl in slots:
                if sl["name"] and sl["color"] and need["color"] and _matches(need, sl, 3):
                    return sl["name"]
        return ""

    jobs, needs = [], {}
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
        jobs.append({"id": j.get("id"), "name": _job_name(j, md), "status": j.get("status"),
                     "printer": assigned, "ready": ready, "best": best, "filaments": fils,
                     "exact": exact, "model": model,
                     # per printer of the right model: which filaments (by index) it lacks
                     "missing_by": missing, "short_by": short, "order": len(jobs),
                     "created": str(j.get("created_at") or ""),
                     "copies": md.get("quantity_remaining"),
                     "minutes": round(md["print_time_seconds"] / 60)
                     if md.get("print_time_seconds") else None,
                     "reason": str(md.get("filament_rejection") or "")[:300]})
    # Jobs that can't run anywhere first, then in Watchtower's own order.
    jobs.sort(key=lambda x: not (x["best"] and not x["printer"]))
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
        slots = sorted({(a.get("system_index") or 0, a.get("slot_index") or 0)
                        for a in entry.get("assignments") or [] if a.get("slot_type") == "ams"})
        if not slots:
            return 404, {"error": "%s has no AMS slots to re-read." % printer}
        # The printer can't read the tag of the slot whose filament is fed into it, so that
        # slot is left out. Its spool can't have been swapped without unloading anyway.
        active = entry.get("active_source") or {}
        loaded = ((active.get("system_index") or 0, active.get("slot_index"))
                  if active.get("source_type") == "ams" else None)
        results = reread_slots(printer, [sl for sl in slots if sl != loaded])
        with CAM_LOCK:
            CAM["queue_at"] = 0                              # next queue read is fresh
        notes = []
        if loaded in slots:
            notes.append("Slot %d wasn't re-read because its filament is loaded in the printer."
                         % (loaded[1] + 1))
        for r in results:
            if not r["success"]:
                notes.append("Slot %d: %s" % (r["tray"] + 1, r["message"]))
        done = sum(1 for r in results if r["success"])
        if not done and results:
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
        return {"enabled": False, "error": "", "jobs": [], "loaded": {}, "needs": {}}
    now = time.time()
    with CAM_LOCK:
        if CAM["queue"] is not None and now - CAM["queue_at"] < (2 if fresh else QUEUE_TTL):
            return CAM["queue"]
        if now < CAM["retry_at"]:
            return {"enabled": True, "error": CAM["error"], "jobs": [], "loaded": {}, "needs": {}}
    try:
        queue = _unwrap(json.loads(cam_fetch(QUEUE_PATH)))
        assignments = _unwrap(json.loads(cam_fetch("/api/filament/assignments")))
        out = build_queue(queue if isinstance(queue, dict) else {},
                          assignments if isinstance(assignments, dict) else {}, printer_table())
        out.update(enabled=True, error="")
    except Exception as e:
        out = {"enabled": True, "jobs": [], "loaded": {}, "needs": {},
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


# The only printer commands the page can send: URL suffix -> Watchtower command.
# "stop" cancels the job; Watchtower wants confirm:true with it. The page asks first.
COMMANDS = {"set-ready": {"command": "set_ready"},
            "resume": {"command": "resume"},
            "pause": {"command": "pause"},
            "stop": {"command": "cancel", "confirm": True}}


class Handler(BaseHTTPRequestHandler):
    server_version = "WatchtowerSetReady/1.0"

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
        elif path == "/api/session":
            self.send(200, json.dumps({"required": auth_required(),
                                       "authed": self.authed()}).encode())
        elif not self.authed():
            self.send(401, b'{"error":"Log in to use this page.","login":true}')
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
        if not self.authed():
            self.send(401, b'{"error":"Log in to use this page.","login":true}')
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
    print("Watchtower SetReady on http://%s:%d  ->  %s"
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

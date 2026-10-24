"""Panel data sources. Each returns RAW data (see library.py raw_shape); the library then validates it.
Credentials are read from environment variables by NAME at fetch time and are never stored or logged."""
import datetime
import hashlib
import csv
import json
import os
import random
import time
import urllib.error
import urllib.request

from .library import LIBRARY

MAX_BYTES = 2 * 1024 * 1024   # per source file / response
CHUNK = 64 * 1024


class SourceError(Exception):
    """Message is safe to put in output (never contains URLs, headers or secret values)."""


def _fix(msg, hint):
    return "%s Fix: %s" % (msg.rstrip(". ") + ".", hint)


def _num(v):
    if isinstance(v, str):
        t = v.strip().replace(",", "")
        try:
            return int(t)
        except ValueError:
            try:
                return float(t)
            except ValueError:
                return v.strip()
    return v


def csv_to_raw(text, list_key):
    """CSV with a header row -> {list_key: [row, ...]}; numeric-looking cells become numbers, empty cells are dropped."""
    rows = []
    for r in csv.DictReader(text.splitlines()):
        row = {}
        for k, v in r.items():
            if k is None or v is None or v.strip() == "":
                continue
            row[k.strip()] = _num(v)
        if row:
            rows.append(row)
    if not rows:
        raise ValueError("no data rows")
    return {list_key: rows}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # never follow: credentials must not be forwarded to another host
        return None


def sample_raw(panel, options, today=None):
    today = today or datetime.date.today()
    seed = int(hashlib.sha256((panel["id"] + today.isoformat()).encode()).hexdigest()[:12], 16)
    return LIBRARY[panel["type"]]["sample"](random.Random(seed), options, today)


def file_raw(panel, home):
    lib = LIBRARY[panel["type"]]
    base = os.path.realpath(home)
    path = os.path.realpath(os.path.join(base, panel["source"]["path"]))
    if os.path.commonpath([base, path]) != base:
        raise SourceError(_fix("source.path resolves outside the dashboard folder", "put the file inside the dashboard's data/ folder and use a relative path"))
    try:
        size = os.path.getsize(path)
    except OSError:
        raise SourceError(_fix("source file not found: %s" % panel["source"]["path"],
                               "check the file really is in the dashboard's data/ folder under that name (re-run the export that creates it), or say 'switch <panel> back to sample data'"))
    if size > MAX_BYTES:
        raise SourceError(_fix("source file is larger than %d MB" % (MAX_BYTES >> 20), "export a shorter time range or fewer rows"))
    asof = datetime.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
    try:
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        raise SourceError(_fix("source file could not be read as UTF-8 text", "save the export as UTF-8 JSON or CSV"))
    if path.lower().endswith(".csv"):
        try:
            return csv_to_raw(text, lib["csv_list_key"]), asof
        except (ValueError, csv.Error):
            raise SourceError(_fix("source CSV has no usable rows", "the first line must be column names (see list_panel_library raw_shape for this panel), followed by data rows"))
    try:
        return json.loads(text), asof
    except (json.JSONDecodeError, RecursionError):
        raise SourceError(_fix("source file is not valid JSON", "re-run the export; open the file to check it is complete JSON, not an error message"))


def http_raw(panel, env, deadline=None):
    """One GET, no retries. s['timeout'] is the TOTAL time allowed for the request (connect + download), not per read."""
    s = panel["source"]
    t0 = time.monotonic()
    budget = float(s["timeout"])
    if deadline is not None:
        budget = min(budget, deadline - t0)
        if budget <= 0:
            raise SourceError(_fix("the build's total time budget ran out before this panel was fetched", "fix or disconnect the slow source (say 'switch <panel> back to sample data'), then refresh"))
    end = t0 + budget
    headers = {"Accept": "application/json", "User-Agent": "agent-dashboard/0.1"}
    if s.get("auth_env"):
        val = env.get(s["auth_env"])
        if not val:
            raise SourceError(_fix("environment variable %s is not set" % s["auth_env"],
                                   "set %s on your computer (or put a line %s=your-key in the dashboard folder's secrets.env file), restart the bot if you set it in the system, then say 'refresh my dashboard'. Never paste the key into chat"
                                   % (s["auth_env"], s["auth_env"])))
        if any(c in val for c in "\r\n"):
            raise SourceError(_fix("environment variable %s contains a line break" % s["auth_env"], "re-enter the key on a single line with no trailing newline"))
        headers[s["auth_header"]] = (s["auth_scheme"] + " " + val).strip()
    req = urllib.request.Request(s["url"], headers=headers, method="GET")
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=budget) as r:
            chunks, got = [], 0
            while got <= MAX_BYTES:
                if time.monotonic() > end:
                    raise TimeoutError
                c = r.read1(min(CHUNK, MAX_BYTES + 1 - got))  # read1: returns what has arrived, so the deadline is checked even on a slow drip
                if not c:
                    break
                chunks.append(c)
                got += len(c)
            body = b"".join(chunks)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise SourceError(_fix("source returned HTTP %d: the key was rejected" % e.code,
                                   "the key in %s is wrong, expired or lacks permission; create a fresh one at the service, update it on your computer (never in chat), then refresh" % (s.get("auth_env") or "the source settings")))
        if 300 <= e.code < 400:
            raise SourceError(_fix("source returned HTTP %d (redirects are not followed)" % e.code, "use the final URL directly"))
        if e.code == 404:
            raise SourceError(_fix("source returned HTTP 404 (not found)", "check the URL is right"))
        raise SourceError(_fix("source returned HTTP %d" % e.code, "the service had a problem or the URL is wrong; try again later or check the URL"))
    except Exception:
        raise SourceError(_fix("could not reach the source (network error, or slower than its %g s time limit)" % s["timeout"],
                               "check your internet connection and that the URL is right, then say 'refresh my dashboard'. The other panels still work. Nothing is retried automatically"))
    if len(body) > MAX_BYTES:
        raise SourceError(_fix("source response is larger than %d MB" % (MAX_BYTES >> 20), "use a URL that returns a shorter time range"))
    try:
        return json.loads(body.decode("utf-8")), datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise SourceError(_fix("source response is not valid JSON", "the URL should return JSON in this panel's raw shape (see list_panel_library)"))

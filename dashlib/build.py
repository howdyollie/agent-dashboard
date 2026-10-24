"""Build: config -> data.json + dashboard.html. Plain code, no LLM, no network except configured http_json sources.

Pipeline: load+validate config -> fetch each panel's raw data -> normalize -> assemble DashboardData ->
render template -> SECRET-LEAK GUARD over the exact bytes -> atomic write. A guard hit aborts with nothing written.
"""
import base64
import datetime
import html
import json
import os
import re
import time
import urllib.parse

from . import config as cfgmod
from .config import ConfigError
from .library import LIBRARY
from .sources import SourceError, file_raw, http_raw, sample_raw

HERE = os.path.dirname(os.path.abspath(__file__))
PLACEHOLDER = "/*__DATA_JSON__*/null"
BUILD_BUDGET_DEFAULT = 60      # seconds for the whole build; slow sources cannot hold it longer
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
AUTO_ENV_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|BEARER)", re.I)


class LeakError(Exception):
    """A configured secret value was found in the output. Message lists env var NAMES only, never values."""


def _embed(obj):
    s = json.dumps(obj, ensure_ascii=True)
    return s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def load_env_file(home):
    """Parse the optional local key file (KEY=value lines). Returns ({name: value}, [warnings])."""
    path = cfgmod.env_file_path(home)
    vals, warns = {}, []
    try:
        st = os.stat(path)
        if st.st_size > cfgmod.MAX_ENV_FILE_BYTES:
            return vals, ["the key file is larger than %d KB and was ignored" % (cfgmod.MAX_ENV_FILE_BYTES // 1024)]
        with open(path, encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return vals, warns
    if os.name == "posix" and st.st_mode & 0o077:
        warns.append("the key file is readable by other users on this computer; run: chmod 600 <dashboard folder>/secrets.env")
    for ln in lines:
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        if ln.startswith("export "):
            ln = ln[7:].strip()
        k, eq, v = ln.partition("=")
        k, v = k.strip(), v.strip()
        if not eq or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k):
            continue
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        vals[k] = v
    return vals, warns


def secret_names(cfg, env, extra=()):
    names = set(cfg.get("secrets", [])) | set(extra)
    for p in cfg["panels"]:
        if p["source"].get("auth_env"):
            names.add(p["source"]["auth_env"])
    auto = set()
    if cfg.get("guard", {}).get("auto_env", True):
        auto = {n for n in env if AUTO_ENV_RE.search(n)} - names
    return sorted(names), sorted(auto)


def _variants(v):
    out = {v, html.escape(v), html.escape(v, quote=False), urllib.parse.quote(v), urllib.parse.quote(v, safe=""),
           base64.b64encode(v.encode()).decode().rstrip("=")}
    j = json.dumps(v)[1:-1]
    out |= {j, j.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"), json.dumps(v, ensure_ascii=False)[1:-1]}
    return {x for x in out if len(x) >= 6}


def leak_scan(outputs, cfg, env, extra=()):
    """outputs: {filename: text}. Returns {'leaks': [(ENV_NAME, filename)], 'checked': [...], 'short': [...]}."""
    configured, auto = secret_names(cfg, env, extra)
    leaks, checked, short = [], [], []
    for name in configured + auto:
        val = env.get(name)
        if not val:
            continue
        min_len = 6 if name in configured else 8
        if len(val) < min_len:
            if name in configured:
                short.append(name)
            continue
        checked.append(name)
        for fname, text in outputs.items():
            if any(v in text for v in _variants(val)):
                leaks.append((name, fname))
    return {"leaks": leaks, "checked": checked, "short": short}


def build_budget():
    try:
        return min(600.0, max(5.0, float(os.environ.get("DASHBOARD_BUILD_TIMEOUT", BUILD_BUDGET_DEFAULT))))
    except ValueError:
        return float(BUILD_BUDGET_DEFAULT)


def collect(cfg, home, env=None, now=None):
    env = os.environ if env is None else env
    deadline = time.monotonic() + build_budget()
    now = now or datetime.datetime.now().astimezone()
    panels = []
    for p in cfg["panels"]:
        lib = LIBRARY[p["type"]]
        entry = {"id": p["id"], "type": p["type"], "title": p["title"], "span": p["options"].get("span", lib["default_span"]),
                 "source_mode": p["source"]["mode"], "asof": None}
        try:
            mode = p["source"]["mode"]
            if mode == "sample":
                raw, entry["asof"] = sample_raw(p, p["options"]), "generated sample data"
            elif mode == "file":
                raw, entry["asof"] = file_raw(p, home)
            else:
                raw, entry["asof"] = http_raw(p, env, deadline)
            entry["data"] = lib["normalize"](raw, p["options"], now)
        except (SourceError, ValueError) as e:  # ConfigError is a ValueError
            entry["error"] = str(e)[:600]
            if not isinstance(e, (SourceError, ConfigError)):
                entry["error"] += " Fix: the data must match this panel's shape; call list_panel_library to see it, then re-export."
        except Exception as e:  # never leak internals/paths into the page
            entry["error"] = "unexpected error (%s)" % type(e).__name__
        panels.append(entry)
    slug = re.sub(r"[^a-z0-9]+", "-", cfg["title"].lower()).strip("-")[:40] or "dashboard"
    return {"meta": {"title": cfg["title"], "built": now.strftime("%a %b %d, %Y %I:%M %p"),
                     "tz": now.strftime("%Z") or now.strftime("%z"), "key": slug,
                     "sample": any(p["source_mode"] == "sample" for p in panels)}, "panels": panels}


def render(data):
    with open(os.path.join(HERE, "template.html"), encoding="utf-8") as f:
        tpl = f.read()
    if tpl.count(PLACEHOLDER) != 1:
        raise RuntimeError("template placeholder missing")
    return tpl.replace(PLACEHOLDER, _embed(data))


def build(home=None, env=None, now=None):
    home = os.path.abspath(home or cfgmod.home_dir())
    file_env, warnings = load_env_file(home)
    env = {**file_env, **(os.environ if env is None else env)}  # real environment wins over the key file
    cfg = cfgmod.load(home)
    data = collect(cfg, home, env, now)
    outputs = {"data.json": json.dumps(data, indent=1, ensure_ascii=False) + "\n", "dashboard.html": render(data)}
    if sum(len(t) for t in outputs.values()) > MAX_OUTPUT_BYTES:
        raise ConfigError(cfgmod.fix("the built page would be larger than %d MB" % (MAX_OUTPUT_BYTES >> 20), "lower the days / max_posts / max_items options or remove panels"))
    scan = leak_scan(outputs, cfg, env, file_env)
    if scan["leaks"]:
        raise LeakError("secret leak guard: value of environment variable(s) %s found in output (%s). Nothing was written."
                        % (", ".join(sorted({n for n, _ in scan["leaks"]})), ", ".join(sorted({f for _, f in scan["leaks"]}))))
    out = os.path.join(home, "out")
    for name, text in outputs.items():
        cfgmod.atomic_write(os.path.join(out, name), text)
    # verify what is actually on disk
    disk = {}
    for name in outputs:
        with open(os.path.join(out, name), encoding="utf-8") as f:
            disk[name] = f.read()
    if leak_scan(disk, cfg, env, file_env)["leaks"]:
        for name in outputs:
            os.unlink(os.path.join(out, name))
        raise LeakError("secret leak guard failed on written files; they were deleted")
    return {"ok": True, "html": os.path.join(out, "dashboard.html"), "data_json": os.path.join(out, "data.json"),
            "panels": [{"id": p["id"], "status": "error" if "error" in p else "ok", **({"error": p["error"]} if "error" in p else {})} for p in data["panels"]],
            "warnings": warnings,
            "guard": {"checked_env": scan["checked"][:20], "too_short_to_check": scan["short"], "auto_detect": cfg["guard"]["auto_env"]}}

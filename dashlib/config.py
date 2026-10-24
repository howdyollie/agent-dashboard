"""Config storage + validation. The config is the ONLY thing tools edit; build.py reads it.

DashboardConfig = {"version":1, "title": str, "secrets":[ENV_NAME...], "guard":{"auto_env":bool}, "panels":[Panel...]}
Panel           = {"id": str, "type": <library type>, "title": str, "source": Source, "options": {...}}
Source          = {"mode":"sample"} | {"mode":"file","path":"rel/path.json"}
                | {"mode":"http_json","url":"https://...","auth_env":"ENV_NAME","auth_header":"Authorization","auth_scheme":"Bearer","timeout":10}

Secrets are never stored here: only the NAME of the env var that holds them (auth_env / secrets[]).
"""
import contextlib
import copy
import json
import os
import re
import tempfile
import urllib.parse

from .library import COMMON_OPTIONS, LIBRARY

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,30}$")
HEADER_RE = re.compile(r"^[A-Za-z0-9-]{1,40}$")
DENY_PARTS = {"secrets", "secure", "secret", ".ssh", ".aws", ".gnupg", ".env", "credentials", ".config"}
SECRET_KEY_PARAMS = {"token", "key", "api_key", "apikey", "access_token", "secret", "password", "auth", "bearer", "sig", "signature"}
SOURCE_KEYS = {"sample": {"mode"}, "file": {"mode", "path"},
               "http_json": {"mode", "url", "auth_env", "auth_header", "auth_scheme", "timeout"}}


class ConfigError(ValueError):
    """Raised for any invalid input; message is safe to show to the user/bot."""


def fix(msg, hint):
    """Plain-word error: what went wrong, then what to do."""
    return "%s Fix: %s" % (msg.rstrip(". ") + ".", hint)


MAX_PANELS = 20          # keeps the page, the build time and every tool answer small
MAX_CONFIG_BYTES = 256 * 1024
MAX_ENV_FILE_BYTES = 64 * 1024
HINT_TYPES = "pick one of the listed types, for example say 'add my sales to my dashboard'."


def home_dir():
    h = os.environ.get("DASHBOARD_HOME") or ""
    if "${" in h:  # a client that did not fill in a placeholder: use the default folder instead of a folder named "${...}"
        h = ""
    h = h or os.path.join(os.path.expanduser("~"), ".agent-dashboard")
    return os.path.abspath(h)


def env_file_path(home=None):
    """Optional local key file (KEY=value lines). Lives next to config.json, never copied into the page."""
    return os.environ.get("DASHBOARD_ENV_FILE") or os.path.join(home or home_dir(), "secrets.env")


def config_path(home=None):
    return os.path.join(home or home_dir(), "config.json")


def default_config(title="My dashboard"):
    return {"version": 1, "title": title, "secrets": [], "guard": {"auto_env": True}, "panels": []}


def looks_like_secret(s):
    """Heuristic: refuse values that look like API keys pasted into config."""
    s = str(s).strip()
    if re.match(r"^(sk|pk|rk|xox[abpr]|ghp|gho|github_pat|AKIA|AIza|ya29)[-_A-Za-z0-9]{8,}", s):
        return True
    if re.match(r"(?i)^bearer\s+\S{8,}", s):
        return True
    return bool(re.fullmatch(r"[A-Za-z0-9_\-+=]{32,}", s))


# ---------- validation ----------
def validate_options(ptype, options):
    spec = dict(COMMON_OPTIONS)
    spec.update(LIBRARY[ptype]["options"])
    options = {} if options is None else options
    if not isinstance(options, dict):
        raise ConfigError("options must be an object")
    unknown = set(options) - set(spec)
    if unknown:
        raise ConfigError("unknown option(s) for %s: %s. Allowed: %s" % (ptype, ", ".join(sorted(unknown)), ", ".join(sorted(spec))))
    out = {}
    for k, s in spec.items():
        v = options.get(k, s["default"])
        kind = s["kind"]
        if kind == "int":
            if isinstance(v, bool) or not isinstance(v, int) or not s["min"] <= v <= s["max"]:
                raise ConfigError("option %s must be an integer between %d and %d" % (k, s["min"], s["max"]))
        elif kind == "handle":
            v = str(v).lstrip("@")
            if not HANDLE_RE.match(v):
                raise ConfigError("option %s must be 1-30 letters, digits or underscores" % k)
        elif kind == "currency":
            v = str(v).upper()
            if not re.fullmatch(r"[A-Z]{3}", v):
                raise ConfigError("option %s must be a 3-letter currency code like USD" % k)
        elif kind == "text":
            if not isinstance(v, str) or not v.strip() or len(v) > s["max"]:
                raise ConfigError("option %s must be non-empty text up to %d characters" % (k, s["max"]))
            v = re.sub(r"[\x00-\x1f\x7f]", "", v).strip()
        if isinstance(v, str) and looks_like_secret(v):
            raise ConfigError("option %s looks like a secret. Secrets never go in the config: put it in an environment variable." % k)
        out[k] = v
    return out


def _check_rel_path(path):
    if not isinstance(path, str) or not path.strip() or len(path) > 200:
        raise ConfigError("source.path must be a non-empty relative path")
    if os.path.isabs(path) or re.match(r"^[A-Za-z]:", path) or path.startswith("~"):
        raise ConfigError("source.path must be relative to the dashboard folder (no absolute paths or ~)")
    parts = re.split(r"[\\/]+", path)
    if ".." in parts:
        raise ConfigError("source.path must not contain '..'")
    if parts[-1].lower().endswith((".env", ".key", ".pem")) or parts[-1].lower() in ("secrets.env", ".env"):
        raise ConfigError("source.path must not point at a key file; keys stay out of data files")
    if not parts[-1].lower().endswith((".json", ".csv")):
        raise ConfigError(fix("source.path must be a .json or .csv file.", "export your data as JSON or CSV into the dashboard's data/ folder"))
    if {p.lower() for p in parts} & DENY_PARTS:
        raise ConfigError("source.path points into a protected folder name (%s)" % ", ".join(sorted(DENY_PARTS)))
    return path


def validate_source(source):
    if source is None:
        return {"mode": "sample"}
    if not isinstance(source, dict):
        raise ConfigError("source must be an object")
    mode = source.get("mode")
    if mode not in SOURCE_KEYS:
        raise ConfigError("source.mode must be one of: sample, file, http_json")
    extra = set(source) - SOURCE_KEYS[mode]
    if extra:
        raise ConfigError("unknown source field(s) for mode %s: %s" % (mode, ", ".join(sorted(extra))))
    if mode == "sample":
        return {"mode": "sample"}
    if mode == "file":
        return {"mode": "file", "path": _check_rel_path(source.get("path"))}
    url = source.get("url")
    if not isinstance(url, str) or len(url) > 500:
        raise ConfigError("source.url must be a string")
    u = urllib.parse.urlsplit(url)
    local = u.hostname in ("127.0.0.1", "localhost", "::1")
    if u.scheme != "https" and not (u.scheme == "http" and local):
        raise ConfigError("source.url must be https (plain http only for localhost)")
    if not u.hostname or u.username or u.password:
        raise ConfigError("source.url must have a host and no user:password")
    for k, _ in urllib.parse.parse_qsl(u.query, keep_blank_values=True):
        if k.lower() in SECRET_KEY_PARAMS:
            raise ConfigError("source.url must not carry a credential in the query string (%s). Use auth_env instead." % k)
    if looks_like_secret(u.path.strip("/").split("/")[-1] or "x") and len(u.path) > 40:
        raise ConfigError("source.url path looks like it embeds a secret. Use auth_env instead.")
    out = {"mode": "http_json", "url": url}
    if source.get("auth_env") is not None:
        if not isinstance(source["auth_env"], str) or not ENV_RE.match(source["auth_env"]):
            raise ConfigError("source.auth_env must be an environment variable NAME like MY_API_TOKEN (not the value)")
        out["auth_env"] = source["auth_env"]
        hdr = source.get("auth_header", "Authorization")
        if not isinstance(hdr, str) or not HEADER_RE.match(hdr):
            raise ConfigError("source.auth_header must be a plain header name")
        out["auth_header"] = hdr
        sch = source.get("auth_scheme", "Bearer")
        if not isinstance(sch, str) or len(sch) > 20 or not re.fullmatch(r"[A-Za-z]*", sch):
            raise ConfigError("source.auth_scheme must be letters only (or empty for a raw header value)")
        out["auth_scheme"] = sch
    elif source.get("auth_header") is not None or source.get("auth_scheme") is not None:
        raise ConfigError("auth_header/auth_scheme need source.auth_env")
    t = source.get("timeout", 10)
    if isinstance(t, bool) or not isinstance(t, (int, float)) or not 1 <= t <= 30:
        raise ConfigError("source.timeout must be 1-30 seconds")
    out["timeout"] = t
    return out


def validate_title(t, what="title"):
    if not isinstance(t, str) or not t.strip() or len(t) > 80:
        raise ConfigError("%s must be non-empty text up to 80 characters" % what)
    t = re.sub(r"[\x00-\x1f\x7f]", "", t).strip()
    if looks_like_secret(t):
        raise ConfigError("%s looks like a secret" % what)
    return t


def validate_panel(p):
    if not isinstance(p, dict):
        raise ConfigError("panel must be an object")
    extra = set(p) - {"id", "type", "title", "source", "options"}
    if extra:
        raise ConfigError("unknown panel field(s): %s" % ", ".join(sorted(extra)))
    if p.get("type") not in LIBRARY:
        raise ConfigError(fix("unknown panel type %r (available: %s)" % (p.get("type"), ", ".join(LIBRARY)), HINT_TYPES))
    pid = p.get("id")
    if not isinstance(pid, str) or not ID_RE.match(pid):
        raise ConfigError("panel id must be 1-40 chars of a-z, 0-9, '-' or '_', starting with a letter or digit")
    return {"id": pid, "type": p["type"], "title": validate_title(p.get("title") or LIBRARY[p["type"]]["default_title"]),
            "source": validate_source(p.get("source")), "options": validate_options(p["type"], p.get("options"))}


def validate_config(cfg):
    if not isinstance(cfg, dict):
        raise ConfigError("config must be a JSON object")
    extra = set(cfg) - {"version", "title", "secrets", "guard", "panels"}
    if extra:
        raise ConfigError("unknown config field(s): %s" % ", ".join(sorted(extra)))
    secrets = cfg.get("secrets", [])
    if not isinstance(secrets, list) or not all(isinstance(s, str) and ENV_RE.match(s) for s in secrets):
        raise ConfigError("secrets must be a list of environment variable NAMES like MY_API_TOKEN (never the values)")
    guard = cfg.get("guard", {"auto_env": True})
    if not isinstance(guard, dict) or set(guard) - {"auto_env"} or not isinstance(guard.get("auto_env", True), bool):
        raise ConfigError("guard must be {\"auto_env\": true|false}")
    panels, seen = [], set()
    if not isinstance(cfg.get("panels", []), list):
        raise ConfigError("panels must be a list")
    if len(cfg.get("panels", [])) > MAX_PANELS:
        raise ConfigError(fix("a dashboard can have at most %d panels (this has %d)" % (MAX_PANELS, len(cfg["panels"])), "remove a panel you no longer need, or use a second dashboard folder (DASHBOARD_HOME)"))
    for p in cfg.get("panels", []):
        p = validate_panel(p)
        if p["id"] in seen:
            raise ConfigError("duplicate panel id %r" % p["id"])
        seen.add(p["id"])
        panels.append(p)
    return {"version": 1, "title": validate_title(cfg.get("title", "My dashboard")), "secrets": sorted(set(secrets)),
            "guard": {"auto_env": guard.get("auto_env", True)}, "panels": panels}


# ---------- storage ----------
def _unwritable(d, e):
    return ConfigError(fix("cannot write to the dashboard folder (%s)" % (getattr(e, "strerror", None) or type(e).__name__),
                           "pick a folder you own and set the DASHBOARD_HOME environment variable to it (default is a hidden folder named .agent-dashboard in your home folder), or fix the folder's permissions; then retry"))


def atomic_write(path, text):
    d = os.path.dirname(path)
    try:
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d)
    except OSError as e:
        raise _unwritable(d, e)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError as e:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise _unwritable(d, e)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


@contextlib.contextmanager
def locked(home):
    try:
        os.makedirs(home, exist_ok=True)
        f = open(os.path.join(home, ".lock"), "a+")
    except OSError as e:
        raise _unwritable(home, e)
    try:
        try:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX)
        except ImportError:  # non-POSIX: no lock, writes are still atomic
            pass
        yield
    finally:
        f.close()


def load(home=None):
    path = config_path(home)
    try:
        if os.path.getsize(path) > MAX_CONFIG_BYTES:
            raise ConfigError(fix("config.json is larger than %d KB" % (MAX_CONFIG_BYTES // 1024), "it is not a normal settings file; restore config.json.bak or delete it and say 'set up my dashboard'"))
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        raise ConfigError(fix("no dashboard has been set up yet", "say 'set up my dashboard' (or call the setup tool); it creates the folder and sample panels for you"))
    except (json.JSONDecodeError, RecursionError, UnicodeDecodeError) as e:
        e = e if isinstance(e, json.JSONDecodeError) else type(e).__name__
        bak = path + ".bak"
        raise ConfigError(fix("config.json is not valid JSON (%s)" % e,
                              ("a backup of the last good version exists: copy config.json.bak over config.json, then say 'refresh my dashboard'" if os.path.exists(bak)
                               else "delete config.json and say 'set up my dashboard' to start again with sample panels")))
    except OSError as e:
        raise ConfigError(fix("cannot read config.json (%s)" % (e.strerror or type(e).__name__), "check the folder permissions or set DASHBOARD_HOME to a folder you own"))
    return validate_config(raw)


def save(cfg, home=None):
    cfg = validate_config(cfg)
    path = config_path(home)
    if os.path.exists(path):  # keep the previous version as a backup (only if it was valid JSON)
        try:
            with open(path, encoding="utf-8") as f:
                old = f.read()
            json.loads(old)
            atomic_write(path + ".bak", old)
        except (OSError, ValueError):
            pass
    atomic_write(path, json.dumps(cfg, indent=2) + "\n")
    return cfg


def mutate(fn, home=None):
    """Read-modify-write under a lock. fn(cfg) edits in place and returns a result."""
    home = home or home_dir()
    with locked(home):
        cfg = load(home)
        work = copy.deepcopy(cfg)
        res = fn(work)
        save(work, home)
        return res


# ---------- operations (used by MCP tools and tests) ----------
def init(title="My dashboard", panels=None, force=False, home=None):
    home = home or home_dir()
    with locked(home):
        if os.path.exists(config_path(home)) and not force:
            raise ConfigError(fix("a dashboard already exists here", "to add panels use add_panel (or the setup tool, which never overwrites); to start over pass force=true to init"))
        cfg = default_config(validate_title(title))
        types = list(LIBRARY) if panels is None else panels
        if not isinstance(types, list):
            raise ConfigError("panels must be a list of library types")
        for t in types:
            if t not in LIBRARY:
                raise ConfigError(fix("unknown panel type %r (available: %s)" % (t, ", ".join(LIBRARY)), HINT_TYPES))
            cfg["panels"].append({"id": t.replace("_", "-"), "type": t, "title": LIBRARY[t]["default_title"],
                                  "source": {"mode": "sample"}, "options": {}})
        for p in cfg["panels"]:
            p["options"] = {"span": LIBRARY[p["type"]]["default_span"]}
        return save(cfg, home)


def setup(title=None, types=None, home=None):
    """First run in one call. Creates the folder + config (sample panels) if missing; if a dashboard exists, only
    adds panel types that are not there yet. Never overwrites. Returns (config, created, added_ids)."""
    home = home or home_dir()
    if types is not None:
        if not isinstance(types, list) or not all(isinstance(t, str) for t in types):
            raise ConfigError(fix("panels must be a list of panel types", HINT_TYPES))
        for t in types:
            if t not in LIBRARY:
                raise ConfigError(fix("unknown panel type %r (available: %s)" % (t, ", ".join(LIBRARY)), HINT_TYPES))
    with locked(home):
        exists = os.path.exists(config_path(home))
        os.makedirs(os.path.join(home, "data"), exist_ok=True)
    if not exists:
        cfg = init(title or "My dashboard", types, False, home)
        return cfg, True, [p["id"] for p in cfg["panels"]]
    added = []

    def go(cfg):
        have = {p["type"] for p in cfg["panels"]}
        for t in types or []:
            if t not in have:
                pid = _unique_id(cfg, t.replace("_", "-"))
                cfg["panels"].append(validate_panel({"id": pid, "type": t, "title": None, "source": None, "options": {"span": LIBRARY[t]["default_span"]}}))
                added.append(pid)
                have.add(t)
        if title:
            cfg["title"] = validate_title(title)
    mutate(go, home)
    return load(home), False, added


def _unique_id(cfg, base):
    base = re.sub(r"[^a-z0-9_-]+", "-", base.lower()).strip("-_") or "panel"
    ids = {p["id"] for p in cfg["panels"]}
    cand, n = base[:40], 2
    while cand in ids:
        cand = "%s-%d" % (base[:36], n)
        n += 1
    return cand


def add_panel(ptype, id=None, title=None, source=None, options=None, position=None, home=None):
    if ptype not in LIBRARY:
        raise ConfigError(fix("unknown panel type %r (available: %s)" % (ptype, ", ".join(LIBRARY)), HINT_TYPES))
    if options is not None and not isinstance(options, dict):
        raise ConfigError("options must be an object")
    opts = dict(options or {})
    opts.setdefault("span", LIBRARY[ptype]["default_span"])

    def go(cfg):
        pid = id if id is not None else _unique_id(cfg, ptype.replace("_", "-"))
        if any(p["id"] == pid for p in cfg["panels"]):
            raise ConfigError("panel id %r already exists" % pid)
        p = validate_panel({"id": pid, "type": ptype, "title": title, "source": source, "options": opts})
        if position is None:
            cfg["panels"].append(p)
        else:
            if isinstance(position, bool) or not isinstance(position, int) or not 0 <= position <= len(cfg["panels"]):
                raise ConfigError("position must be an integer from 0 to %d" % len(cfg["panels"]))
            cfg["panels"].insert(position, p)
        return p
    return mutate(go, home)


def update_panel(id, title=None, source=None, options=None, home=None):
    def go(cfg):
        for p in cfg["panels"]:
            if p["id"] == id:
                if title is not None:
                    p["title"] = title
                if source is not None:
                    p["source"] = source
                if options is not None:
                    if not isinstance(options, dict):
                        raise ConfigError("options must be an object")
                    p["options"] = dict(p["options"], **options)
                return validate_panel(p)
        raise ConfigError("no panel with id %r" % id)
    return mutate(go, home)


def remove_panel(id, home=None):
    def go(cfg):
        n = len(cfg["panels"])
        cfg["panels"] = [p for p in cfg["panels"] if p["id"] != id]
        if len(cfg["panels"]) == n:
            raise ConfigError("no panel with id %r" % id)
        return {"removed": id, "remaining": [p["id"] for p in cfg["panels"]]}
    return mutate(go, home)


def reorder_panels(order, home=None):
    if not isinstance(order, list) or not all(isinstance(i, str) for i in order):
        raise ConfigError("order must be a list of panel ids")

    def go(cfg):
        have = [p["id"] for p in cfg["panels"]]
        if len(set(order)) != len(order):
            raise ConfigError("order has duplicate ids")
        unknown = [i for i in order if i not in have]
        if unknown:
            raise ConfigError("unknown panel id(s): %s" % ", ".join(unknown))
        missing = [i for i in have if i not in order]
        if missing:
            raise ConfigError("order must list every panel exactly once; missing: %s" % ", ".join(missing))
        by = {p["id"]: p for p in cfg["panels"]}
        cfg["panels"] = [by[i] for i in order]
        return {"order": order}
    return mutate(go, home)

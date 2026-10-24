"""Open the built page in the user's default browser (local file, no server, no network)."""
import os
import pathlib
import webbrowser

from .build import build


def open_dashboard(home=None, no_open=False, env=None):
    """Rebuild, then open out/dashboard.html. Returns the build result plus {path, url, opened}."""
    r = build(home, env=env)
    url = pathlib.Path(r["html"]).resolve().as_uri()
    opened = False
    # DASHBOARD_BROWSER=none turns launching off (servers, tests); the page is still built.
    if not no_open and os.environ.get("DASHBOARD_BROWSER", "").lower() != "none":
        try:
            opened = bool(webbrowser.open(url))
        except Exception:
            opened = False
    res = dict(r, path=r["html"], url=url, opened=opened)
    if not opened and not no_open:
        res["hint"] = ("No browser could be launched (this looks like a computer without a screen or default browser). "
                       "The page is built: open this file yourself in any browser (double-click it, or paste the url into the address bar): %s" % r["html"])
    return res

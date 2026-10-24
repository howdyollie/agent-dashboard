# Connect a real data source

Every panel starts with fictional **sample** data. To show real numbers, connect the panel to a source. There are three ways. Pick the one that matches what you already have.

| | A. Existing connector | B. A file (JSON or CSV) | C. An endpoint (`http_json`) |
|---|---|---|---|
| Use when | your bot already has a connector for the service | you can export a file, or already have one | the service already returns JSON over https |
| Who fetches | your bot, when you ask | you / your script / cron | the build script |
| Refresh without the bot | no (ask "refresh my dashboard") | yes (cron) | yes (cron) |
| Where the key is | in the connector (nothing to do) | in your export script's environment | env var **name** in the panel settings |

In all cases the panel needs data in its **raw shape** (below), and a key never goes in chat, in the HTML, in `data.json`, or in the settings file.

## A. An existing connector

Say: "connect my sales to my dashboard using my store connector" (or whichever service you have connected to your bot).

1. The bot calls its own connector and reshapes the answer into the panel's raw shape.
2. It writes that JSON into the dashboard's `data/` folder (for example `data/sales.json`). The folder path is returned by the `setup` tool.
3. It switches the panel to `source {"mode":"file","path":"data/sales.json"}`, rebuilds and opens the page.
4. To refresh: say "refresh my dashboard"; the bot calls the connector again and rewrites the file.

No key is needed by the plugin: the connector already holds its own login.

## B. A file

Put a `.json` or `.csv` file inside the dashboard's `data/` folder, then say "use data/sales.csv for my sales panel". The path is always relative to the dashboard folder; absolute paths, `..` and key files are refused. Files over 2 MB are refused.

**JSON** must match the raw shape of the panel type. **CSV** needs a header row; each row is one item of the panel's list (`posts`, `days` or `bots`), numbers may use thousands commas, empty cells are skipped. Top-level fields (handle, site, currency, followers) take their defaults and options in CSV, so use JSON when you need them in the data.

Raw shapes (the live version is `list_panel_library`):

```
x_stats         {"handle":"example_handle","followers":982,"following":120,
                 "posts":[{"time":"2026-10-01 09:00","text":"...","impressions":1200,"likes":30,"reposts":4,"replies":2}]}
website_visits  {"site":"example.test","days":[{"date":"2026-10-01","visits":120,"page_loads":300}]}
sales           {"currency":"USD","days":[{"date":"2026-10-01","orders":3,"revenue":120.5}],
                 "recent":[{"date":"2026-10-01","item":"Starter plan","amount":49.0}]}
ads             {"platform":"Example Ads","currency":"USD","days":[{"date":"2026-10-01","impressions":1000,"clicks":20,"spend":12.5}]}
bot_activity    {"bots":[{"name":"Helper","status":"active","last_update":"2026-10-01T09:00:00","last_action":"did a thing"}]}
```

CSV examples (the first line is the header):

```
date,visits,page_loads          date,orders,revenue          name,status,last_update,last_action
2026-10-01,120,300              2026-10-01,3,120.50          Helper,active,2026-10-01T09:00:00,did a thing
```

Fictional examples live in `examples/data/`.

### Refreshing a file automatically

Write a small script (any language) that calls the service and overwrites the file, then rebuild. Example in Python, key read from the environment, never printed or written:

```python
import json, os, urllib.request
req = urllib.request.Request("https://api.example.test/v1/sales/daily",
                             headers={"Authorization": "Bearer " + os.environ["MY_STORE_TOKEN"]})
rows = json.load(urllib.request.urlopen(req, timeout=20))
out = {"currency": "USD", "days": [{"date": r["day"], "orders": r["count"], "revenue": r["total"]} for r in rows]}
json.dump(out, open("data/sales.json", "w"))   # only the numbers, no keys, no account ids
```

Then cron: `*/30 * * * * cd /path/to/dashboard-folder && python3 export_sales.py && python3 /path/to/plugin/scripts/build.py`

## C. An endpoint (`http_json`)

If you have a URL that already returns the raw shape as JSON, say "point my sales panel at https://example.test/sales.json". If it needs a key, the key's **name** goes in the panel settings (`auth_env`), the value stays on your computer.

Limits, on purpose: `GET` only, https (plain http only for `localhost`), 10 s default total time limit (1 to 30; it covers the whole request, so a slow trickle is cut too), 2 MB maximum, no automatic retries, redirects are never followed, no credentials in the URL. Header defaults to `Authorization: Bearer <value of the env var>`; change `auth_header` / `auth_scheme` for services that want something else (for example `auth_header: "X-Api-Key"`, `auth_scheme: ""`).

## Where keys go

Keys stay **on your own computer**, in one of two places:

1. **An environment variable**, for example `MY_STORE_TOKEN`.
   - macOS / Linux: add `export MY_STORE_TOKEN=your-key` to your shell profile (or set it in your cron line).
   - Windows: `setx MY_STORE_TOKEN your-key`, then restart the app that runs your bot.
2. **A local key file**, `secrets.env` inside the dashboard folder (override the path with `DASHBOARD_ENV_FILE`), one line per key:

   ```
   # secrets.env  (never share this file)
   MY_STORE_TOKEN=your-key
   ```

   On macOS / Linux run `chmod 600` on it; the build warns if other users can read it. A real environment variable wins over the file.

Rules the plugin enforces:

- The settings file stores only the variable **name** (`auth_env`). Values that look like keys are rejected there.
- **Leak guard**: every build scans the finished `dashboard.html` and `data.json` for the *values* of every configured key, every variable in `secrets.env`, and every environment variable whose name contains KEY, TOKEN, SECRET, PASSWORD, PASSWD, CREDENTIAL or BEARER. A hit stops the build and nothing is written; the message names the variable, never the value. Keys shorter than 6 characters cannot be checked.
- Type keys into the file yourself. Don't paste them into chat; your bot never needs them. Use read-only keys where the service offers them.
- Key files (`.env`, `.key`, `.pem`) cannot be used as a data file.

## If it does not work

| Panel says | Plain-words fix |
|---|---|
| "environment variable NAME is not set" | Set it (above), restart the bot or terminal if you set it system-wide, then "refresh my dashboard". |
| "HTTP 401" / "key was rejected" | The key is wrong, expired or lacks read permission. Make a new read-only key, update it on your computer, refresh. |
| "could not reach the source" | Check your internet connection and that the URL is right. Other panels still work. |
| "source file not found" | The file is not in `data/` under that name. Re-run your export, or say "switch the sales panel back to sample data". |
| "not valid JSON" / "no usable rows" | Open the file: it must be complete JSON or a CSV with a header row, in the panel's raw shape. |

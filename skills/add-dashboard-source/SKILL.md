---
name: add-dashboard-source
description: Use when the user wants real numbers in a panel ("connect my X", "use my real sales", "make it live"): wire a connector, a JSON/CSV file or an https endpoint to a panel, and say where keys go (env var or local key file, never chat/HTML/data.json).
---

# Connect real data to a panel

Details and raw shapes: `docs/connect-a-source.md`. Panel types and shapes: `list_panel_library {type}`.

## Pick one way
| way | use when | do | refresh |
|---|---|---|---|
| **A. Existing connector** | you already have a connector for the service | Call it, reshape into the panel's `raw_shape`, write JSON to `<dashboard folder>/data/<name>.json` (`setup` returns `data_folder`), then `update_panel` source `{mode:"file",path:"data/<name>.json"}` | needs a bot turn each time ("refresh my dashboard": call the connector again, rewrite the file, `open_dashboard`). Say so. |
| **B. File** | user/script already makes `.json` or `.csv` | put it in `data/`; JSON in raw shape, or CSV with a header row of the item field names; `update_panel` source `{mode:"file",path:"data/x.csv"}` | cron overwrites the file and runs `scripts/build.py`; no bot |
| **C. Endpoint** | https URL already returns the raw shape | `update_panel` source `{mode:"http_json",url,auth_env:"NAME"}` | cron runs `scripts/build.py`; no bot |

## Keys (all ways)
- A key lives only on the user's computer: an environment variable, or one line `NAME=key` in `secrets.env` in the dashboard folder (`setup` returns `keys_file`; `chmod 600`). The USER types it. You never ask for it in chat and never write a real key anywhere (config, data file, HTML, `data.json`). The config stores only the NAME (`auth_env`). Use read-only keys. Way A needs no key from you.
- Fetching is a separate plain script; do not design anything that needs a bot turn to refresh except way A. Offer to write the export script: it reads the key from the environment, never prints it, writes only the raw shape.

## Procedure
1. Tell the user in plain words which env var name to set (or which `secrets.env` line) and what the file/endpoint must contain.
2. Once confirmed, `update_panel` with the new source, then `open_dashboard`. Keep sample mode until the panel is `ok`.
3. Error card: read its "Fix:" line to the user (key missing/wrong, unreachable, file not found); do not retry in a loop. `SECRET LEAK GUARD` means nothing was written: find where the value entered the data (usually an export script copying a header or URL into text), fix the script.
4. Check one known number against the real system, and that no key, email or private URL shows. Report which panels are live, which are sample, and the cron line.

New kind of panel: only if asked and the plugin folder is writable: add an entry to `dashlib/library.py` (options, fictional `sample`, strict `normalize`), a renderer in `dashlib/template.html` (escape with `E()`), and a test.

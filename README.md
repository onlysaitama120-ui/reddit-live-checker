# Reddit Live Checker

Checks whether Reddit posts and comments are still up.

## What it does

Paste Reddit links (or load them from a `.txt` file), press **Check links**,
and it shows one status per link:

| Status | Meaning |
| --- | --- |
| `LIVE` | Still up |
| `REMOVED` | Removed by moderators |
| `DELETED` | Deleted by the author |
| `NOT_FOUND` | Bad link, or gone |
| `BLOCKED` | Reddit didn't respond (it retries once) |

If you check a comment again later, it also tells you whether the text
changed (`NEW`, `CHANGED`, `EDITED`, or `UNCHANGED`). Snapshots are saved
in `snapshots.json`.

## Requirements

- Python 3.9+
- `pip install camoufox`
- `camoufox fetch` (downloads the browser it uses)

## How to use it

GUI:

```bash
python app.py
```

Paste links, one per line. **Load from file** opens a `.txt` file with one
URL per line and fills the box.

CLI:

```bash
python checker.py "https://www.reddit.com/r/sub/comments/abc123/"
python checker.py --file links.txt
python checker.py --file links.txt --csv results.csv
```

From your phone (same Wi-Fi as the PC):

```bash
python webapp.py
```

Then open the printed `http://<pc-ip>:8000` address in your phone's browser.
Paste links there and check them. The checking still runs on the PC; the
phone is just the screen.

## Output example

```
[1/2] LIVE      comment is live [UNCHANGED]  https://www.reddit.com/r/AskReddit/comments/1vdz0pv/p1d552c/
[2/2] LIVE      post is live                https://www.reddit.com/r/AskReddit/comments/1vdz0pv/

LIVE posts: 1 | LIVE comments: 1 | removed: 0 | deleted: 0 | not found: 0 | blocked: 0
```

## Building the .exe

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name reddit_live_checker \
  --collect-all camoufox \
  --collect-data apify_fingerprint_datapoints \
  --collect-data language_tags \
  app.py
```

The exe lands in `dist/`. The `--collect-*` flags bundle the browser's
data files.

> Note: the app still needs the camoufox browser, installed once with
> `camoufox fetch`.

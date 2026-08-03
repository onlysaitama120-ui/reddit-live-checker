import argparse
import csv
import json
import os
import re
import sys

from camoufox.sync_api import Camoufox

WARM_URL = "https://www.reddit.com/r/AskReddit/top"
SNAPSHOT_FILE = "snapshots.json"

POST_RE = re.compile(r"/comments/([a-z0-9]+)")
COMMENT_RE = re.compile(r"/comments/([a-z0-9]+)/(?:[a-z0-9_-]+/)?([a-z0-9]{7})/?")
SHARE_RE = re.compile(r"reddit\.com/r/[\w-]+/s/[\w-]+", re.IGNORECASE)


class RedditSession:
    def __init__(self):
        self._cf = Camoufox(headless=True, os="windows", humanize=True)
        self.browser = self._cf.__enter__()
        self.page = self.browser.new_page()
        try:
            self._warm()
        except BaseException:
            self.close()
            raise

    def _warm(self):
        self.page.goto(WARM_URL, wait_until="domcontentloaded", timeout=45000)
        self.page.wait_for_timeout(2500)

    def fetch(self, url):
        resp = self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
        if resp is None:
            return None, 0
        return resp.body(), resp.status

    def resolve(self, url):
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
            return self.page.url, 200
        except Exception:
            return None, 0

    def close(self):
        try:
            self.page.close()
        except Exception:
            pass
        try:
            self._cf.__exit__(None, None, None)
        except Exception:
            pass


def classify_post(d):
    if d.get("author") == "[deleted]" or d.get("selftext") == "[deleted]":
        return "DELETED", "post deleted by author"
    rbc = d.get("removed_by_category") or d.get("removed_by")
    if rbc:
        return "REMOVED", f"removed (by {rbc})"
    if d.get("selftext") == "[removed]" or d.get("title") == "[removed]":
        return "REMOVED", "removed (content replaced with [removed])"
    return "LIVE", "post is live"


def classify_comment(d, snapshots):
    cid = d.get("id")
    if d.get("author") == "[deleted]" or d.get("body") == "[deleted]":
        return "DELETED", "comment deleted by author"
    if d.get("body") == "[removed]":
        return "REMOVED", "removed (content replaced with [removed])"
    rbc = d.get("removed_by_category") or d.get("removed_by")
    if rbc:
        return "REMOVED", f"removed (by {rbc})"
    prev = snapshots.get(cid)
    body = d.get("body") or ""
    edited = bool(d.get("edited"))
    if prev is None:
        change = "NEW"
        snapshots[cid] = {"body": body, "edited": edited}
    elif body != prev.get("body"):
        change = "CHANGED"
        snapshots[cid] = {"body": body, "edited": edited}
    elif edited and prev.get("edited") != edited:
        change = "EDITED"
        snapshots[cid]["edited"] = edited
    else:
        change = "UNCHANGED"
    return "LIVE", f"comment is live [{change}]"


def walk(nodes, comment_id):
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        d = node.get("data", {}) or {}
        if d.get("id") == comment_id:
            return d
        replies = d.get("replies")
        if isinstance(replies, dict):
            found = walk(replies.get("data", {}).get("children", []), comment_id)
            if found:
                return found
    return None


def check(url, sess, snapshots, _depth=0):
    if SHARE_RE.search(url):
        if _depth >= 3:
            return "UNKNOWN", "share link did not resolve to a normal url", url
        final_url, code = sess.resolve(url)
        if not final_url or not final_url.startswith("http"):
            return "BLOCKED", "could not resolve share link", url
        return check(final_url, sess, snapshots, _depth + 1)
    m = COMMENT_RE.search(url)
    if m:
        post_id, comment_id = m.group(1), m.group(2)
        body, code = sess.fetch(f"https://www.reddit.com/comments/{post_id}/comment/{comment_id}.json")
        if code != 200:
            return ("BLOCKED" if code in (403, 0) else ("NOT_FOUND" if code == 404 else "UNKNOWN"), f"http {code}", url)
        try:
            data = json.loads(body)
        except Exception:
            return "BLOCKED", "challenge page instead of json", url
        if not isinstance(data, list) or len(data) < 2:
            return "UNKNOWN", "unexpected response shape", url
        children = data[1].get("data", {}).get("children", [])
        c = walk(children, comment_id)
        if c is None:
            if not children:
                return "NOT_FOUND", "comment was removed or deleted (no longer publicly visible)", url
            return "NOT_FOUND", "comment not found (wrong link or comment never existed)", url
        status, reason = classify_comment(c, snapshots)
        return status, reason, url
    m = POST_RE.search(url)
    if m:
        post_id = m.group(1)
        body, code = sess.fetch(f"https://www.reddit.com/comments/{post_id}.json")
        if code != 200:
            return ("BLOCKED" if code in (403, 0) else ("NOT_FOUND" if code == 404 else "UNKNOWN"), f"http {code}", url)
        try:
            data = json.loads(body)
        except Exception:
            return "BLOCKED", "challenge page instead of json", url
        try:
            post = data[0]["data"]["children"][0]["data"]
        except Exception:
            return "UNKNOWN", "unexpected response shape", url
        status, reason = classify_post(post)
        return status, reason, url
    return "INVALID", "not a reddit post/comment url", url


def load_snapshots():
    if os.path.exists(SNAPSHOT_FILE):
        try:
            with open(SNAPSHOT_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_snapshots(snapshots):
    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(snapshots, f, indent=1)


def main():
    parser = argparse.ArgumentParser(description="Check if Reddit posts/comments are live, removed, or deleted")
    parser.add_argument("urls", nargs="*", help="one or more reddit post/comment urls")
    parser.add_argument("--file", help="file with one reddit url per line")
    parser.add_argument("--csv", help="also append results to a csv file")
    args = parser.parse_args()

    urls = list(args.urls)
    if args.file:
        with open(args.file, encoding="utf-8-sig") as f:
            urls += [l.strip() for l in f if l.strip()]
    if not urls:
        print("usage: python checker.py <url> [more urls...]  (or --file links.txt)")
        return 1

    snapshots = load_snapshots()
    out = None
    if args.csv:
        outf = open(args.csv, "a", newline="", encoding="utf-8")
        out = csv.writer(outf)

    print("warming up (solving reddit challenge once)...", flush=True)
    sess = RedditSession()
    try:
        live_posts = live_comments = removed = deleted = not_found = blocked = 0
        for url in urls:
            status, reason, target = check(url, sess, snapshots)
            if status == "BLOCKED":
                sess._warm()
                status, reason, target = check(url, sess, snapshots)
            if status == "LIVE":
                if COMMENT_RE.search(target):
                    live_comments += 1
                else:
                    live_posts += 1
            elif status == "REMOVED":
                removed += 1
            elif status == "DELETED":
                deleted += 1
            elif status in ("NOT_FOUND", "INVALID"):
                not_found += 1
            else:
                blocked += 1
            if target and target != url:
                print(f"{status:<9} {reason}  {url}\n           (points to: {target})", flush=True)
            else:
                print(f"{status:<9} {reason}  {url}", flush=True)
            if out:
                out.writerow([url, target, status, reason])
                outf.flush()
    finally:
        sess.close()

    if out:
        outf.close()
    save_snapshots(snapshots)
    print(f"\nLIVE posts: {live_posts} | LIVE comments: {live_comments} | removed: {removed} | deleted: {deleted} | not found: {not_found} | blocked: {blocked}", flush=True)
    print(f"Result: {live_posts + live_comments} of {len(urls)} links are live.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

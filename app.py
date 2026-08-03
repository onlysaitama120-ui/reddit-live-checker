import queue
import re
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext

from checker import (
    COMMENT_RE,
    POST_RE,
    RedditSession,
    check,
    load_snapshots,
    save_snapshots,
)

APP_RE = re.compile(r"^https?://\S+$")


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Reddit Live Checker")
        self.root.geometry("720x560")
        self.msg_q = queue.Queue()
        self.worker = None

        frame = tk.Frame(root, padx=10, pady=8)
        frame.pack(fill="x")

        tk.Label(frame, text="Paste Reddit post/comment links (one per line):").pack(anchor="w")
        self.input = scrolledtext.ScrolledText(frame, height=8)
        self.input.pack(fill="x", pady=4)

        btns = tk.Frame(frame)
        btns.pack(fill="x", pady=4)
        self.btn_check = tk.Button(btns, text="Check links", command=self.start, bg="#d9ead3", activebackground="#b6d7a8")
        self.btn_check.pack(side="left")
        self.btn_file = tk.Button(btns, text="Load from file", command=self.load_file)
        self.btn_file.pack(side="left", padx=6)
        self.btn_clear = tk.Button(btns, text="Clear", command=lambda: self.input.delete("1.0", "end"))
        self.btn_clear.pack(side="left")
        self.status = tk.Label(frame, text="Idle.", anchor="w")
        self.status.pack(fill="x", pady=(6, 0))

        tk.Label(root, text="Results:").pack(anchor="w", padx=10)
        self.output = scrolledtext.ScrolledText(root, height=12, state="disabled")
        self.output.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        self.root.after(100, self.poll)

    def load_file(self):
        path = filedialog.askopenfilename(filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if path:
            try:
                with open(path, encoding="utf-8-sig") as f:
                    self.input.delete("1.0", "end")
                    self.input.insert("1.0", f.read())
            except Exception as e:
                messagebox.showerror("Error", f"Could not read file:\n{e}")

    def log(self, line):
        self.output.configure(state="normal")
        self.output.insert("end", line + "\n")
        self.output.see("end")
        self.output.configure(state="disabled")

    def start(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Busy", "A check is already running.")
            return
        raw = self.input.get("1.0", "end").strip()
        urls = [l.strip() for l in raw.splitlines() if l.strip() and APP_RE.match(l.strip())]
        if not urls:
            messagebox.showwarning("No links", "Paste at least one valid http(s) link.")
            return
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.configure(state="disabled")
        self.btn_check.config(state="disabled", text="Checking...")
        self.status.config(text="Warming up (solving reddit challenge once)...")
        self.worker = threading.Thread(target=self._run, args=(urls,), daemon=True)
        self.worker.start()

    def _run(self, urls):
        try:
            snapshots = load_snapshots()
            sess = RedditSession()
            try:
                self.msg_q.put(("status", "Checking..."))
                live_posts = live_comments = removed = deleted = not_found = blocked = 0
                for i, url in enumerate(urls, 1):
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
                    show = url if (not target or target == url) else f"{url}  ->  {target}"
                    self.msg_q.put(("log", f"[{i}/{len(urls)}] {status:<9} {reason}  {show}"))
            finally:
                sess.close()
            save_snapshots(snapshots)
            summary = (
                f"\nLIVE posts: {live_posts} | LIVE comments: {live_comments} | "
                f"removed: {removed} | deleted: {deleted} | not found: {not_found} | blocked: {blocked}"
            )
            self.msg_q.put(("log", summary))
            self.msg_q.put(("log", f"Result: {live_posts + live_comments} of {len(urls)} links are live."))
            self.msg_q.put(("status", f"Done. {len(urls)} link(s), {live_posts + live_comments} live, {blocked} blocked."))
        except Exception as e:
            self.msg_q.put(("log", f"ERROR: {e}"))
            self.msg_q.put(("status", "Failed."))
        finally:
            self.msg_q.put(("done", None))

    def poll(self):
        try:
            while True:
                kind, payload = self.msg_q.get_nowait()
                if kind == "log":
                    self.log(payload)
                elif kind == "status":
                    self.status.config(text=payload)
                elif kind == "done":
                    self.btn_check.config(state="normal", text="Check links")
                    if self.status.cget("text") == "Failed.":
                        messagebox.showerror("Error", "Check failed. See results.")
        except queue.Empty:
            pass
        self.root.after(100, self.poll)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()

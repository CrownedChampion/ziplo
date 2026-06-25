"""
Ziplo — One-click GitHub release uploader
Drop a zip → extract → commit → tag → push. Done.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import threading
import subprocess
import zipfile
import tarfile
import os
import sys
import json
import shutil
import re
import webbrowser
from pathlib import Path
import urllib.request
import urllib.error
import ssl
import tempfile
import time

# ── Constants ──────────────────────────────────────────────────────────────
APP_NAME    = "Ziplo"
APP_VERSION = "1.0.0"
CONFIG_FILE = Path.home() / ".ziplo" / "config.json"
LOG_FILE    = Path.home() / ".ziplo" / "ziplo.log"

C = {
    "bg":          "#0e0e14",
    "surface":     "#16161f",
    "surface2":    "#1e1e2a",
    "surface3":    "#26263a",
    "border":      "#2e2e42",
    "border2":     "#3a3a52",
    "accent":      "#4f8cff",
    "accent_dim":  "#2a4e99",
    "accent_hover":"#6ba0ff",
    "green":       "#3ecf8e",
    "green_dim":   "#1a7a50",
    "danger":      "#f06a6a",
    "warn":        "#f0b429",
    "text":        "#e8e8f0",
    "text_dim":    "#8888aa",
    "text_muted":  "#44445a",
    "white":       "#ffffff",
    "navy":        "#1a1a2e",
    "mint":        "#6ee7b7",
}

F = {
    "logo":    ("Segoe UI", 17, "bold"),
    "display": ("Segoe UI", 15, "bold"),
    "heading": ("Segoe UI", 11, "bold"),
    "label":   ("Segoe UI", 9),
    "label_b": ("Segoe UI", 9, "bold"),
    "body":    ("Segoe UI", 10),
    "body_b":  ("Segoe UI", 10, "bold"),
    "small":   ("Segoe UI", 9),
    "mono":    ("Consolas", 9),
    "mono_sm": ("Consolas", 8),
}


# ── Utilities ──────────────────────────────────────────────────────────────
def ensure_config_dir():
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)

def load_config() -> dict:
    ensure_config_dir()
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text("utf-8"))
        except Exception:
            pass
    return {"accounts": [], "recent_projects": [], "default_account": None}

def save_config(cfg: dict):
    ensure_config_dir()
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), "utf-8")

def write_log(msg: str):
    ensure_config_dir()
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass

def run_git(args: list, cwd: str, log_cb=None):
    cmd = ["git"] + args
    if log_cb:
        log_cb(f"$ git {' '.join(args)}")
    try:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True,
                                text=True, timeout=120, env={**os.environ})
        out = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            if log_cb:
                log_cb(f"  ✗ {out}", "error")
            return False, out
        if out and log_cb:
            log_cb(f"  {out}", "dim")
        return True, out
    except subprocess.TimeoutExpired:
        msg = "git timed out"
        if log_cb: log_cb(f"  ✗ {msg}", "error")
        return False, msg
    except FileNotFoundError:
        msg = "git not found — install Git for Windows and add to PATH"
        if log_cb: log_cb(f"  ✗ {msg}", "error")
        return False, msg
    except Exception as e:
        if log_cb: log_cb(f"  ✗ {e}", "error")
        return False, str(e)


def autodetect_project(repo_path):
    """Scan a repo folder and return a dict describing the project type.
    Returns:
        {
          "type":   "pyinstaller" | "electron" | "none",
          "entry":  "main.py" | "ziplo.py" | etc,
          "name":   "appname",
          "args":   "--onefile --windowed",
          "reason": "human readable explanation",
        }
    """
    p = Path(repo_path)
    files = {f.name.lower() for f in p.iterdir() if f.is_file()}
    subdirs = {d.name.lower() for d in p.iterdir() if d.is_dir()}

    result = {
        "type":   "none",
        "entry":  "",
        "name":   p.name.lower().replace(" ", "_"),
        "args":   "--onefile --windowed",
        "reason": "No recognized project type found",
    }

    # ── Electron / Node ───────────────────────────────────────────────────
    if "package.json" in files:
        try:
            pkg = json.loads((p / "package.json").read_text("utf-8", errors="ignore"))
        except Exception:
            pkg = {}
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        scripts = pkg.get("scripts", {})
        app_name = pkg.get("name", result["name"]).replace(" ", "-")

        if "electron" in deps or "electron-builder" in deps or "electron" in str(pkg).lower():
            build_cmd = "npm run build" if "build" in scripts else "npm run compile"
            dist_cmd  = "npm run dist"  if "dist"  in scripts else "npm run package"
            return {
                "type":      "electron",
                "build_cmd": build_cmd,
                "dist_cmd":  dist_cmd,
                "name":      app_name,
                "node_ver":  "22",
                "reason":    f"Found package.json with electron dependency (build={build_cmd})",
            }

    # ── Python ────────────────────────────────────────────────────────────
    py_files = [f for f in p.iterdir() if f.suffix.lower() == ".py" and f.is_file()]
    if py_files:
        # Priority order for entry point detection
        entry = None
        reason_detail = ""

        # 1. Exact name match: <reponame>.py or <reponame>_main.py
        repo_name = p.name.lower()
        for f in py_files:
            stem = f.stem.lower()
            if stem == repo_name or stem == repo_name.replace("-", "_"):
                entry = f.name
                reason_detail = f"matched repo name ({f.name})"
                break

        # 2. __main__.py
        if not entry and "__main__.py" in files:
            entry = "__main__.py"
            reason_detail = "found __main__.py"

        # 3. main.py / app.py / run.py / start.py
        if not entry:
            for candidate in ["main.py", "app.py", "run.py", "start.py",
                              "launcher.py", "gui.py", "ui.py"]:
                if candidate in files:
                    entry = candidate
                    reason_detail = f"found standard entry point ({candidate})"
                    break

        # 4. Single .py file
        if not entry and len(py_files) == 1:
            entry = py_files[0].name
            reason_detail = f"only one .py file ({entry})"

        # 5. Fall back to first .py alphabetically
        if not entry:
            entry = sorted(f.name for f in py_files)[0]
            reason_detail = f"no clear entry — using first .py ({entry})"

        # Detect if it has a GUI (use --windowed) or is CLI (use --onefile only)
        has_gui = False
        try:
            text = (p / entry).read_text("utf-8", errors="ignore").lower()
            gui_hints = ["tkinter", "tk.", "wx.", "pyqt", "pyside", "kivy",
                         "pygame", "pyglet", "customtkinter", "ctk."]
            has_gui = any(h in text for h in gui_hints)
        except Exception:
            pass

        args = "--onefile --windowed" if has_gui else "--onefile"
        app_name = Path(entry).stem.lower().replace(" ", "_")

        return {
            "type":   "pyinstaller",
            "entry":  entry,
            "name":   app_name,
            "args":   args,
            "reason": f"Python project — {reason_detail}" + (" (GUI detected)" if has_gui else " (CLI)"),
        }

    return result


def _run_cmd(args, cwd, log_cb=None):
    """Run an arbitrary shell command, return (success, output)."""
    if log_cb:
        log_cb(f"  $ {' '.join(args)}")
    try:
        result = subprocess.run(
            args, cwd=cwd, capture_output=True,
            text=True, timeout=300, env={**os.environ}
        )
        out = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            if log_cb: log_cb(f"  ✗ {out}", "error")
            return False, out
        if out and log_cb: log_cb(f"  {out}", "dim")
        return True, out
    except FileNotFoundError as e:
        msg = f"Command not found: {args[0]} — is it installed and on PATH?"
        if log_cb: log_cb(f"  ✗ {msg}", "error")
        return False, msg
    except subprocess.TimeoutExpired:
        msg = "Command timed out (5 min)"
        if log_cb: log_cb(f"  ✗ {msg}", "error")
        return False, msg
    except Exception as e:
        if log_cb: log_cb(f"  ✗ {e}", "error")
        return False, str(e)

def github_api(method, endpoint, token, data=None):
    url = f"https://api.github.com{endpoint}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": f"Ziplo/{APP_VERSION}",
    }
    ctx = ssl.create_default_context()
    body = json.dumps(data).encode() if data else None
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            return True, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read())
        except Exception:
            err = {"message": str(e)}
        return False, err
    except Exception as e:
        return False, {"message": str(e)}

def validate_token(token):
    return github_api("GET", "/user", token)

def get_user_repos(token):
    ok, data = github_api("GET", "/user/repos?per_page=100&sort=updated&type=all", token)
    return (True, data) if ok else (False, [])

def extract_archive(archive_path, dest_dir, log_cb=None):
    ap = Path(archive_path)
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    try:
        if ap.suffix.lower() == ".zip" or zipfile.is_zipfile(ap):
            if log_cb: log_cb(f"Extracting ZIP: {ap.name}")
            with zipfile.ZipFile(ap, "r") as z:
                z.extractall(dest)
        elif ap.suffix.lower() in {".gz", ".bz2", ".xz"} or tarfile.is_tarfile(ap):
            if log_cb: log_cb(f"Extracting TAR: {ap.name}")
            with tarfile.open(ap, "r:*") as t:
                t.extractall(dest)
        else:
            return False, "Unsupported format (use .zip or .tar.gz)"
        contents = list(dest.iterdir())
        if len(contents) == 1 and contents[0].is_dir():
            return True, str(contents[0])
        return True, str(dest)
    except Exception as e:
        return False, str(e)

def bump_version(version_str, part="patch"):
    if not version_str:
        return "v1.0.0"
    clean = version_str.lstrip("v")
    parts = clean.split(".")
    while len(parts) < 3:
        parts.append("0")
    try:
        idx = {"major": 0, "minor": 1, "patch": 2}.get(part, 2)
        parts[idx] = str(int(parts[idx]) + 1)
        if idx == 0:
            parts[1] = parts[2] = "0"
        elif idx == 1:
            parts[2] = "0"
    except (ValueError, IndexError):
        parts[-1] = str(int(parts[-1] or 0) + 1)
    return "v" + ".".join(parts)


# ── Helpers for clean tkinter layout ──────────────────────────────────────
def card(parent, bg=None, pady=0, padx=0):
    bg = bg or C["surface"]
    f = tk.Frame(parent, bg=bg,
                 highlightbackground=C["border"], highlightthickness=1)
    f.pack(fill="x", padx=padx, pady=pady)
    inner = tk.Frame(f, bg=bg)
    inner.pack(fill="x", padx=12, pady=10)
    return inner

def sep(parent, color=None, padx=0, pady=4):
    tk.Frame(parent, bg=color or C["border"], height=1).pack(
        fill="x", padx=padx, pady=pady)

def label(parent, text, font=None, color=None, **kw):
    return tk.Label(parent, text=text,
                    font=font or F["body"],
                    fg=color or C["text"],
                    bg=parent.cget("bg"), **kw)

def field_row(parent, lbl_text, widget_builder, lbl_w=90):
    row = tk.Frame(parent, bg=parent.cget("bg"))
    row.pack(fill="x", pady=3)
    tk.Label(row, text=lbl_text, font=F["label"], fg=C["text_dim"],
             bg=parent.cget("bg"), width=lbl_w // 7, anchor="w").pack(side="left")
    w = widget_builder(row)
    return row, w

def entry(parent, var, width=32, mono=False, show=None):
    kw = dict(textvariable=var, font=F["mono_sm"] if mono else F["body"],
              bg=C["surface2"], fg=C["text"],
              insertbackground=C["text"], relief="flat",
              highlightthickness=1,
              highlightbackground=C["border"],
              highlightcolor=C["accent"],
              width=width)
    if show:
        kw["show"] = show
    e = tk.Entry(parent, **kw)
    e.pack(side="left", padx=(6, 0), ipady=3, ipadx=4)
    return e


# ── Main App ───────────────────────────────────────────────────────────────
class ZiploApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("980x680")
        self.minsize(900, 620)
        self.configure(bg=C["bg"])
        self._center()
        self.cfg = load_config()
        self._job_running = False
        self._cancelled   = False
        self._temp_dir    = None
        self._step_labels = []
        self._step_frames = []
        self._build_ui()
        self._refresh_accounts()
        self._load_recent()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _center(self):
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = 980, 680
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

    # ── Top-level layout ───────────────────────────────────────────────────
    def _build_ui(self):
        # Sidebar 190px fixed
        self.sidebar = tk.Frame(self, bg=C["surface"], width=190)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        # Right of sidebar: topbar + content
        self.right = tk.Frame(self, bg=C["bg"])
        self.right.pack(side="left", fill="both", expand=True)

        self._build_sidebar()

        # Topbar
        topbar = tk.Frame(self.right, bg=C["surface"], height=44)
        topbar.pack(fill="x")
        topbar.pack_propagate(False)
        tk.Frame(topbar, bg=C["border"], height=1).pack(side="bottom", fill="x")
        self.page_title = tk.Label(topbar, text="New Release",
                                   font=F["heading"], fg=C["text"],
                                   bg=C["surface"])
        self.page_title.pack(side="left", padx=20)
        self.topbar_right = tk.Frame(topbar, bg=C["surface"])
        self.topbar_right.pack(side="right", padx=14)

        # Page container
        self.pages_frame = tk.Frame(self.right, bg=C["bg"])
        self.pages_frame.pack(fill="both", expand=True)

        self.pages = {}
        for name, builder in [
            ("upload",   self._page_upload),
            ("build",    self._page_build),
            ("accounts", self._page_accounts),
            ("recent",   self._page_recent),
            ("logs",     self._page_logs),
        ]:
            f = tk.Frame(self.pages_frame, bg=C["bg"])
            f.place(relx=0, rely=0, relwidth=1, relheight=1)
            builder(f)
            self.pages[name] = f

        self._show_page("upload")

    # ── Sidebar ────────────────────────────────────────────────────────────
    def _build_sidebar(self):
        sb = self.sidebar

        # Logo block
        logo_f = tk.Frame(sb, bg=C["surface"])
        logo_f.pack(fill="x", padx=14, pady=(18, 12))

        # Logo mark (canvas-drawn arrow)
        mark = tk.Canvas(logo_f, width=26, height=26, bg=C["navy"],
                         highlightthickness=0)
        mark.pack(side="left")
        # Arrow up
        mark.create_polygon(13, 4, 21, 14, 16, 14, 16, 22, 10, 22, 10, 14, 5, 14,
                             fill=C["mint"], outline="")
        # Underline bar
        mark.create_rectangle(5, 23, 21, 25, fill=C["mint"], outline="")

        tk.Label(logo_f, text=APP_NAME, font=F["logo"],
                 fg=C["text"], bg=C["surface"]).pack(side="left", padx=(8, 0))

        sep(sb, padx=10, pady=0)

        # Nav
        self._nav_btns = {}
        nav_items = [
            ("upload",   "↑  Upload",   "New Release"),
            ("recent",   "⏱  Releases", "History"),
            ("build",    "⚙  Build",    "Build Setup"),
            ("accounts", "◎  Accounts", "Accounts"),
            ("logs",     "≡  Logs",     "Activity"),
        ]
        tk.Frame(sb, bg=C["surface"], height=6).pack()
        for key, label_text, page_title in nav_items:
            btn = self._nav_btn(sb, label_text, key, page_title)
            self._nav_btns[key] = btn

        sep(sb, padx=10, pady=8)

        # Account pill at bottom
        self.acct_frame = tk.Frame(sb, bg=C["surface"])
        self.acct_frame.pack(side="bottom", fill="x", padx=10, pady=10)
        self._draw_acct_pill()

    def _nav_btn(self, parent, text, key, page_title):
        f = tk.Frame(parent, bg=C["surface"], cursor="hand2")
        f.pack(fill="x", padx=6, pady=1)
        lbl = tk.Label(f, text=text, font=F["body"],
                       fg=C["text_dim"], bg=C["surface"],
                       anchor="w", padx=10, pady=7)
        lbl.pack(fill="x")

        def click(e=None):
            self._show_page(key)
            self.page_title.configure(text=page_title)
        def enter(e):
            if key != self._current_page:
                f.configure(bg=C["surface2"])
                lbl.configure(bg=C["surface2"])
        def leave(e):
            if key != self._current_page:
                f.configure(bg=C["surface"])
                lbl.configure(bg=C["surface"])

        f.bind("<Button-1>", click)
        lbl.bind("<Button-1>", click)
        f.bind("<Enter>", enter)
        f.bind("<Leave>", leave)
        return (f, lbl)

    def _draw_acct_pill(self):
        for w in self.acct_frame.winfo_children():
            w.destroy()
        accounts = self.cfg.get("accounts", [])
        default = self.cfg.get("default_account")
        acct = next((a for a in accounts if a["nick"] == default), None)
        if acct:
            dot = tk.Canvas(self.acct_frame, width=8, height=8,
                            bg=C["surface"], highlightthickness=0)
            dot.pack(side="left", padx=(4, 6), pady=8)
            dot.create_oval(1, 1, 7, 7, fill=C["green"], outline="")
            info = tk.Frame(self.acct_frame, bg=C["surface"])
            info.pack(side="left")
            tk.Label(info, text=acct["nick"], font=F["label_b"],
                     fg=C["text"], bg=C["surface"]).pack(anchor="w")
            tk.Label(info, text=f"@{acct.get('username','')}", font=F["small"],
                     fg=C["text_muted"], bg=C["surface"]).pack(anchor="w")
        else:
            tk.Label(self.acct_frame, text="No account", font=F["small"],
                     fg=C["text_muted"], bg=C["surface"]).pack(padx=8, pady=8)

    def _show_page(self, name):
        self._current_page = name
        for n, f in self.pages.items():
            f.lower() if n != name else f.lift()
        for key, (f, lbl) in self._nav_btns.items():
            active = key == name
            bg = C["surface2"] if active else C["surface"]
            fg = C["text"]     if active else C["text_dim"]
            f.configure(bg=bg)
            lbl.configure(bg=bg, fg=fg)
        titles = {"upload": "New Release", "recent": "History",
                  "accounts": "Accounts",  "logs": "Activity",
                  "build": "Build Setup"}
        self.page_title.configure(text=titles.get(name, ""))
        if name == "recent":
            self._load_recent()
        if name == "logs":
            self._load_log_display()

    def _show_upload(self):   self._show_page("upload")
    def _show_accounts(self): self._show_page("accounts")

    # ── Upload Page — two-column, no scroll ───────────────────────────────
    def _page_upload(self, parent):
        # Two columns side by side, each independently scrollable
        # Ship button pinned at bottom of right panel — always visible
        right_outer = tk.Frame(parent, bg=C["bg"], width=270)
        right_outer.pack(side="right", fill="y", padx=(0, 14), pady=14)
        right_outer.pack_propagate(False)

        # Buttons pinned to bottom
        btn_frame = tk.Frame(right_outer, bg=C["bg"])
        btn_frame.pack(side="bottom", fill="x", pady=(6, 0))

        self.cancel_btn = tk.Button(
            btn_frame, text="Cancel",
            font=F["small"],
            bg=C["surface2"], fg=C["text_dim"],
            activebackground=C["border"], activeforeground=C["text"],
            relief="flat", pady=6, cursor="hand2",
            command=self._cancel_upload, state="disabled"
        )
        self.cancel_btn.pack(fill="x", pady=(4, 0))

        self.go_btn = tk.Button(
            btn_frame, text="  ZIP IT & SHIP IT  ↑",
            font=("Segoe UI", 11, "bold"),
            bg=C["navy"], fg=C["mint"],
            activebackground=C["surface3"], activeforeground=C["mint"],
            relief="flat", pady=12, cursor="hand2",
            command=self._start_upload
        )
        self.go_btn.pack(fill="x")
        self.go_btn.bind("<Enter>", lambda e: self.go_btn.configure(bg=C["surface3"]))
        self.go_btn.bind("<Leave>", lambda e: self.go_btn.configure(bg=C["navy"]))

        # Scrollable right content
        right_canvas = tk.Canvas(right_outer, bg=C["bg"], highlightthickness=0)
        right_sb = tk.Scrollbar(right_outer, orient="vertical", command=right_canvas.yview)
        right_canvas.configure(yscrollcommand=right_sb.set)
        right_sb.pack(side="right", fill="y")
        right_canvas.pack(side="left", fill="both", expand=True)
        right = tk.Frame(right_canvas, bg=C["bg"])
        right_win = right_canvas.create_window((0, 0), window=right, anchor="nw")
        right.bind("<Configure>", lambda e: right_canvas.configure(
            scrollregion=right_canvas.bbox("all")))
        right_canvas.bind("<Configure>", lambda e: right_canvas.itemconfig(
            right_win, width=e.width))
        right_canvas.bind("<MouseWheel>", lambda e: right_canvas.yview_scroll(
            -1*(e.delta//120), "units"))

        left  = tk.Frame(parent, bg=C["bg"])
        left.pack(side="left", fill="both", expand=True, padx=(14, 6), pady=14)

        # ── LEFT COLUMN ──
        # 1. Drop zone
        self._build_dropzone(left)

        # 2. Repository
        tk.Label(left, text="Repository", font=F["heading"],
                 fg=C["text"], bg=C["bg"]).pack(anchor="w", pady=(10, 4))
        repo_inner = card(left, pady=0)
        bg = repo_inner.cget("bg")

        # Account row
        row_a = tk.Frame(repo_inner, bg=bg)
        row_a.pack(fill="x", pady=(0, 4))
        tk.Label(row_a, text="Account", font=F["label"], fg=C["text_dim"],
                 bg=bg, width=10, anchor="w").pack(side="left")
        self.account_var = tk.StringVar()
        self.account_combo = ttk.Combobox(row_a, textvariable=self.account_var,
                                          font=F["small"], state="readonly", width=22)
        self.account_combo.pack(side="left", padx=(6, 0))
        self.account_combo.bind("<<ComboboxSelected>>", self._on_account_change)
        tk.Button(row_a, text="+ Add", font=F["small"],
                  bg=C["surface3"], fg=C["accent"], relief="flat",
                  padx=8, pady=2, cursor="hand2",
                  activebackground=C["border"], activeforeground=C["accent"],
                  command=self._show_accounts).pack(side="left", padx=(8, 0))

        # Repo row
        row_r = tk.Frame(repo_inner, bg=bg)
        row_r.pack(fill="x", pady=(0, 4))
        tk.Label(row_r, text="Repo", font=F["label"], fg=C["text_dim"],
                 bg=bg, width=10, anchor="w").pack(side="left")
        self.repo_var = tk.StringVar()
        self.repo_combo = ttk.Combobox(row_r, textvariable=self.repo_var,
                                       font=F["mono_sm"], state="readonly", width=30)
        self.repo_combo.pack(side="left", padx=(6, 0))
        tk.Button(row_r, text="↺", font=F["small"],
                  bg=C["surface3"], fg=C["text_dim"], relief="flat",
                  padx=6, pady=2, cursor="hand2",
                  activebackground=C["border"],
                  command=self._refresh_repos).pack(side="left", padx=(6, 0))

        # Branch row
        row_b = tk.Frame(repo_inner, bg=bg)
        row_b.pack(fill="x")
        tk.Label(row_b, text="Branch", font=F["label"], fg=C["text_dim"],
                 bg=bg, width=10, anchor="w").pack(side="left")
        self.branch_var = tk.StringVar(value="main")
        entry(row_b, self.branch_var, width=18, mono=True)

        # 3. Commit message
        tk.Label(left, text="Commit message", font=F["heading"],
                 fg=C["text"], bg=C["bg"]).pack(anchor="w", pady=(10, 4))
        msg_inner = card(left, pady=0)
        self.message_var = tk.StringVar()
        msg_e = tk.Entry(msg_inner, textvariable=self.message_var,
                         font=F["body"], bg=C["surface2"], fg=C["text"],
                         insertbackground=C["text"], relief="flat",
                         highlightthickness=1,
                         highlightbackground=C["border"],
                         highlightcolor=C["accent"])
        msg_e.pack(fill="x", ipady=4, ipadx=6)

        # 4. Options strip
        opts_inner = card(left, bg=C["bg"], pady=(4, 0))
        opts_inner.configure(bg=C["bg"])
        self.create_gh_release = tk.BooleanVar(value=True)
        self.push_tags_var     = tk.BooleanVar(value=True)
        self.pull_first_var    = tk.BooleanVar(value=True)
        self.force_push_var    = tk.BooleanVar(value=False)
        for text, var in [
            ("Create GitHub Release", self.create_gh_release),
            ("Push tag",              self.push_tags_var),
            ("Pull before push",      self.pull_first_var),
            ("Force push",            self.force_push_var),
        ]:
            cb = tk.Checkbutton(opts_inner, text=text, variable=var,
                                font=F["small"], bg=C["bg"],
                                fg=C["text_dim"], selectcolor=C["surface2"],
                                activebackground=C["bg"],
                                activeforeground=C["text"],
                                relief="flat", cursor="hand2")
            cb.pack(side="left", padx=(0, 16))

        # 5. Console output
        tk.Label(left, text="Console", font=F["heading"],
                 fg=C["text"], bg=C["bg"]).pack(anchor="w", pady=(10, 4))
        con_f = tk.Frame(left, bg=C["surface"],
                         highlightbackground=C["border"], highlightthickness=1)
        con_f.pack(fill="both", expand=True)
        self.console = scrolledtext.ScrolledText(
            con_f, font=F["mono"], bg=C["bg"], fg=C["text"],
            insertbackground=C["text"], relief="flat",
            height=8, wrap="word", state="disabled"
        )
        self.console.pack(fill="both", expand=True, padx=2, pady=2)
        self.console.tag_configure("success", foreground=C["green"])
        self.console.tag_configure("error",   foreground=C["danger"])
        self.console.tag_configure("warn",    foreground=C["warn"])
        self.console.tag_configure("dim",     foreground=C["text_muted"])
        self.console.tag_configure("heading", foreground=C["accent"])

        # ── RIGHT COLUMN ──
        # Version card
        ver_f = tk.Frame(right, bg=C["surface"],
                         highlightbackground=C["border"], highlightthickness=1)
        ver_f.pack(fill="x", pady=(0, 10))
        ver_inner = tk.Frame(ver_f, bg=C["surface"])
        ver_inner.pack(fill="x", padx=12, pady=12)

        tk.Label(ver_inner, text="VERSION TAG", font=("Segoe UI", 8),
                 fg=C["text_muted"], bg=C["surface"]).pack(anchor="w")
        self.version_var = tk.StringVar(value="v1.0.0")
        ver_entry = tk.Entry(ver_inner, textvariable=self.version_var,
                             font=("Consolas", 20, "bold"),
                             bg=C["surface"], fg=C["accent"],
                             insertbackground=C["accent"],
                             relief="flat", highlightthickness=0, width=12)
        ver_entry.pack(anchor="w", pady=(2, 8))

        bump_row = tk.Frame(ver_inner, bg=C["surface"])
        bump_row.pack(fill="x")
        for part in ["major", "minor", "patch"]:
            btn = tk.Button(bump_row, text=part, font=F["small"],
                            bg=C["surface2"], fg=C["text_dim"],
                            activebackground=C["accent_dim"],
                            activeforeground=C["white"],
                            relief="flat", padx=0, pady=4, cursor="hand2",
                            command=lambda p=part: self._bump(p))
            btn.pack(side="left", fill="x", expand=True, padx=2)
            btn.bind("<Enter>", lambda e, b=btn: b.configure(bg=C["surface3"], fg=C["accent"]))
            btn.bind("<Leave>", lambda e, b=btn: b.configure(bg=C["surface2"], fg=C["text_dim"]))

        # Progress steps
        sep(right, pady=(0, 8))
        tk.Label(right, text="PROGRESS", font=("Segoe UI", 8),
                 fg=C["text_muted"], bg=C["bg"]).pack(anchor="w", padx=2, pady=(0, 6))

        self._step_frames = []
        self._step_labels = []
        steps = ["Extract", "Stage", "Commit", "Pull", "Push", "Tag", "Release"]
        for i, name in enumerate(steps):
            sf = tk.Frame(right, bg=C["bg"])
            sf.pack(fill="x", pady=1)

            num = tk.Label(sf, text=str(i+1), font=F["mono_sm"],
                           fg=C["text_muted"], bg=C["surface2"],
                           width=2, anchor="center")
            num.pack(side="left")
            # connector line via a 1px frame
            tk.Frame(sf, bg=C["border"], width=6, height=1).pack(side="left")

            sl = tk.Label(sf, text=name, font=F["small"],
                          fg=C["text_muted"], bg=C["bg"], anchor="w")
            sl.pack(side="left", fill="x", expand=True)

            self._step_frames.append(num)
            self._step_labels.append(sl)

        sep(right, pady=8)

        # Progress bar + status
        self.status_var = tk.StringVar(value="Ready")
        tk.Label(right, textvariable=self.status_var, font=F["small"],
                 fg=C["text_dim"], bg=C["bg"], anchor="w").pack(fill="x", padx=2)

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Z.Horizontal.TProgressbar",
                        troughcolor=C["surface2"],
                        background=C["accent"],
                        bordercolor=C["surface"],
                        lightcolor=C["accent"],
                        darkcolor=C["accent"])
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(right, variable=self.progress_var,
                                            maximum=100,
                                            style="Z.Horizontal.TProgressbar")
        self.progress_bar.pack(fill="x", pady=(4, 0))

        sep(right, pady=8)

        # Override folder
        tk.Label(right, text="EXTRACT TO (optional)", font=("Segoe UI", 8),
                 fg=C["text_muted"], bg=C["bg"]).pack(anchor="w", padx=2, pady=(0, 4))

        folder_row = tk.Frame(right, bg=C["bg"])
        folder_row.pack(fill="x")
        self.folder_var = tk.StringVar()
        folder_e = tk.Entry(folder_row, textvariable=self.folder_var,
                            font=F["mono_sm"], bg=C["surface2"], fg=C["text_dim"],
                            insertbackground=C["text"], relief="flat",
                            highlightthickness=1,
                            highlightbackground=C["border"],
                            highlightcolor=C["accent"], width=20)
        folder_e.pack(side="left", ipady=3, ipadx=4, fill="x", expand=True)
        tk.Button(folder_row, text="…", font=F["small"],
                  bg=C["surface2"], fg=C["text_dim"], relief="flat",
                  padx=8, pady=3, cursor="hand2",
                  activebackground=C["border"],
                  command=self._browse_folder).pack(side="left", padx=(4, 0))

        # Spacer at bottom of scrollable right content
        tk.Frame(right, bg=C["bg"], height=8).pack()

    def _build_dropzone(self, parent):
        tk.Label(parent, text="Archive", font=F["heading"],
                 fg=C["text"], bg=C["bg"]).pack(anchor="w", pady=(0, 4))

        drop_f = tk.Frame(parent, bg=C["surface2"],
                          highlightbackground=C["border2"],
                          highlightthickness=1)
        drop_f.pack(fill="x")

        inner = tk.Frame(drop_f, bg=C["surface2"])
        inner.pack(fill="x", padx=14, pady=14)

        top = tk.Frame(inner, bg=C["surface2"])
        top.pack(fill="x")

        # Icon block
        icon_f = tk.Frame(top, bg=C["surface3"], width=40, height=40)
        icon_f.pack(side="left")
        icon_f.pack_propagate(False)
        tk.Label(icon_f, text="↑", font=("Segoe UI", 18, "bold"),
                 fg=C["mint"], bg=C["surface3"]).place(relx=0.5, rely=0.5, anchor="center")

        info = tk.Frame(top, bg=C["surface2"])
        info.pack(side="left", padx=(12, 0))
        tk.Label(info, text="Drop your archive here",
                 font=F["body_b"], fg=C["text"],
                 bg=C["surface2"]).pack(anchor="w")

        self.archive_var = tk.StringVar(value="No file selected")
        self.archive_lbl = tk.Label(info, textvariable=self.archive_var,
                                    font=F["mono_sm"], fg=C["text_muted"],
                                    bg=C["surface2"])
        self.archive_lbl.pack(anchor="w")

        tk.Button(top, text="Browse…", font=F["small"],
                  bg=C["surface3"], fg=C["accent"],
                  activebackground=C["border"], activeforeground=C["accent_hover"],
                  relief="flat", padx=12, pady=5, cursor="hand2",
                  command=self._browse_archive).pack(side="right")

    # ── Accounts Page ──────────────────────────────────────────────────────
    def _page_accounts(self, parent):
        canvas = tk.Canvas(parent, bg=C["bg"], highlightthickness=0)
        sb = tk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(canvas, bg=C["bg"])
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(
            scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win, width=e.width))
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(-1*(e.delta//120), "units"))

        p = inner
        tk.Label(p, text="Add Account", font=F["display"],
                 fg=C["text"], bg=C["bg"]).pack(anchor="w", padx=20, pady=(20, 12))

        add_inner = card(p, padx=14, pady=0)

        link = tk.Label(add_inner,
                        text="Generate a token at github.com → Settings → Developer settings → Personal access tokens",
                        font=F["small"], fg=C["accent"], bg=add_inner.cget("bg"),
                        cursor="hand2", wraplength=560, justify="left")
        link.pack(anchor="w", pady=(0, 10))
        link.bind("<Button-1>", lambda e: webbrowser.open(
            "https://github.com/settings/tokens/new?scopes=repo,workflow&description=Ziplo"))
        tk.Label(add_inner, text="Required scopes: contents, workflow, metadata",
                 font=F["small"], fg=C["warn"],
                 bg=add_inner.cget("bg")).pack(anchor="w", pady=(0, 2))
        tk.Label(add_inner, text="The workflow scope is required to push .github/workflows/ files.",
                 font=F["small"], fg=C["text_dim"],
                 bg=add_inner.cget("bg")).pack(anchor="w", pady=(0, 10))

        r1 = tk.Frame(add_inner, bg=add_inner.cget("bg"))
        r1.pack(fill="x", pady=(0, 6))
        tk.Label(r1, text="Nickname", font=F["label"], fg=C["text_dim"],
                 bg=r1.cget("bg"), width=10, anchor="w").pack(side="left")
        self.new_nick_var = tk.StringVar()
        entry(r1, self.new_nick_var, width=28)

        r2 = tk.Frame(add_inner, bg=add_inner.cget("bg"))
        r2.pack(fill="x", pady=(0, 10))
        tk.Label(r2, text="PAT Token", font=F["label"], fg=C["text_dim"],
                 bg=r2.cget("bg"), width=10, anchor="w").pack(side="left")
        self.new_token_var = tk.StringVar()
        entry(r2, self.new_token_var, width=48, mono=True, show="•")

        btn_row = tk.Frame(add_inner, bg=add_inner.cget("bg"))
        btn_row.pack(fill="x")
        tk.Button(btn_row, text="Verify & Save", font=F["body_b"],
                  bg=C["navy"], fg=C["mint"],
                  activebackground=C["surface3"], activeforeground=C["mint"],
                  relief="flat", padx=16, pady=6, cursor="hand2",
                  command=self._add_account).pack(side="left")
        self.verify_lbl = tk.Label(btn_row, text="", font=F["small"],
                                   fg=C["text_dim"], bg=add_inner.cget("bg"))
        self.verify_lbl.pack(side="left", padx=12)

        tk.Label(p, text="Saved Accounts", font=F["display"],
                 fg=C["text"], bg=C["bg"]).pack(anchor="w", padx=20, pady=(20, 8))
        self.accounts_frame = tk.Frame(p, bg=C["bg"])
        self.accounts_frame.pack(fill="x", padx=14)

    # ── Recent Page ────────────────────────────────────────────────────────
    def _page_recent(self, parent):
        tk.Label(parent, text="Release History", font=F["display"],
                 fg=C["text"], bg=C["bg"]).pack(anchor="w", padx=20, pady=(20, 12))
        self.recent_frame = tk.Frame(parent, bg=C["bg"])
        self.recent_frame.pack(fill="both", expand=True, padx=14)

    # ── Logs Page ──────────────────────────────────────────────────────────
    def _page_logs(self, parent):
        hdr = tk.Frame(parent, bg=C["bg"])
        hdr.pack(fill="x", padx=20, pady=(20, 8))
        tk.Label(hdr, text="Activity Log", font=F["display"],
                 fg=C["text"], bg=C["bg"]).pack(side="left")
        tk.Button(hdr, text="Clear", font=F["small"],
                  bg=C["surface2"], fg=C["danger"],
                  activebackground=C["border"], relief="flat",
                  padx=10, pady=4, cursor="hand2",
                  command=self._clear_log).pack(side="right")
        self.log_display = scrolledtext.ScrolledText(
            parent, font=F["mono_sm"], bg=C["bg"], fg=C["text_dim"],
            insertbackground=C["text"], relief="flat",
            state="disabled", wrap="word"
        )
        self.log_display.pack(fill="both", expand=True, padx=14, pady=(0, 14))


    # ── Build Setup Page ───────────────────────────────────────────────────
    def _page_build(self, parent):
        canvas = tk.Canvas(parent, bg=C["bg"], highlightthickness=0)
        sb = tk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(canvas, bg=C["bg"])
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(
            scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win, width=e.width))
        canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(-1*(e.delta//120), "units"))

        p = inner

        # Header
        tk.Label(p, text="GitHub Actions Build", font=F["display"],
                 fg=C["text"], bg=C["bg"]).pack(anchor="w", padx=20, pady=(20, 4))
        tk.Label(p,
                 text="Ziplo writes a .github/workflows/build.yml into your repo before committing. "
                      "When you push a tag, GitHub Actions builds the .exe on a Windows runner "
                      "and attaches it to the release automatically — no local build tools needed.",
                 font=F["small"], fg=C["text_dim"], bg=C["bg"],
                 wraplength=680, justify="left").pack(anchor="w", padx=20, pady=(0, 14))

        # ── How it works info box ─────────────────────────────────────────
        info_f = tk.Frame(p, bg=C["surface2"],
                          highlightbackground=C["border"], highlightthickness=1)
        info_f.pack(fill="x", padx=14, pady=(0, 12))
        info_inner = tk.Frame(info_f, bg=C["surface2"])
        info_inner.pack(fill="x", padx=14, pady=10)
        # Scope warning banner
        warn_f = tk.Frame(p, bg="#2a1500",
                          highlightbackground=C["warn"], highlightthickness=1)
        warn_f.pack(fill="x", padx=14, pady=(0, 10))
        tk.Label(warn_f,
                 text="Your GitHub token needs the 'workflow' scope to push workflow files. "
                      "If you get a push error saying 'refusing to allow', go to Accounts, "
                      "delete your token and recreate it at github.com/settings/tokens "
                      "with 'workflow' checked.",
                 font=F["small"], fg=C["warn"], bg="#2a1500",
                 justify="left", anchor="w", wraplength=660,
                 padx=12, pady=8).pack(anchor="w")

        for line in [
            "1  You ship a new tag (e.g. v1.2.0) via the Upload tab",
            "2  GitHub Actions triggers on that tag",
            "3  Windows runner installs Python + PyInstaller (or Node + electron-builder)",
            "4  Builds the .exe (or installer)",
            "5  Uploads the artifact directly to the GitHub Release",
        ]:
            tk.Label(info_inner, text=line, font=F["mono_sm"],
                     fg=C["text_dim"], bg=C["surface2"],
                     anchor="w").pack(fill="x", pady=1)

        # ── Workflow type ─────────────────────────────────────────────────
        tk.Label(p, text="Build type", font=F["heading"],
                 fg=C["text"], bg=C["bg"]).pack(anchor="w", padx=20, pady=(10, 6))

        wf_card = tk.Frame(p, bg=C["surface"],
                           highlightbackground=C["border"], highlightthickness=1)
        wf_card.pack(fill="x", padx=14, pady=(0, 10))
        wf_inner = tk.Frame(wf_card, bg=C["surface"])
        wf_inner.pack(fill="x", padx=14, pady=12)

        self.build_type_var = tk.StringVar(value="pyinstaller")
        for val, lbl in [
            ("pyinstaller", "Python / PyInstaller  →  produces .exe"),
            ("electron",    "Electron / electron-builder  →  produces installer"),
            ("none",        "None  —  just push source, no build workflow"),
        ]:
            rb = tk.Radiobutton(wf_inner, text=lbl, value=val,
                                variable=self.build_type_var,
                                font=F["small"], bg=C["surface"],
                                fg=C["text_dim"], selectcolor=C["surface2"],
                                activebackground=C["surface"],
                                activeforeground=C["text"],
                                relief="flat", cursor="hand2",
                                command=self._update_workflow_preview)
            rb.pack(anchor="w", pady=2)

        # ── Python fields ─────────────────────────────────────────────────
        self.py_frame = tk.Frame(p, bg=C["bg"])
        self.py_frame.pack(fill="x", padx=14)

        py_card = tk.Frame(self.py_frame, bg=C["surface"],
                           highlightbackground=C["border"], highlightthickness=1)
        py_card.pack(fill="x")
        py_inner = tk.Frame(py_card, bg=C["surface"])
        py_inner.pack(fill="x", padx=14, pady=10)

        self.py_entry_var  = tk.StringVar(value="ziplo.py")
        self.py_args_var   = tk.StringVar(value="--onefile --windowed")
        self.py_name_var   = tk.StringVar(value="ziplo")
        self.py_req_var    = tk.BooleanVar(value=True)
        self.py_ver_var    = tk.StringVar(value="3.12")

        for label_text, var, w, mono in [
            ("Entry script",          self.py_entry_var, 20, True),
            ("PyInstaller args",      self.py_args_var,  32, True),
            ("Output name (.exe)",    self.py_name_var,  20, False),
            ("Python version",        self.py_ver_var,   8,  True),
        ]:
            row = tk.Frame(py_inner, bg=C["surface"])
            row.pack(fill="x", pady=3)
            tk.Label(row, text=label_text, font=F["label"], fg=C["text_dim"],
                     bg=C["surface"], width=20, anchor="w").pack(side="left")
            e = tk.Entry(row, textvariable=var,
                         font=F["mono_sm"] if mono else F["small"],
                         bg=C["surface2"], fg=C["text"],
                         insertbackground=C["text"], relief="flat",
                         highlightthickness=1,
                         highlightbackground=C["border"],
                         highlightcolor=C["accent"], width=w)
            e.pack(side="left", padx=(6, 0), ipady=3, ipadx=4)
            e.bind("<KeyRelease>", lambda ev: self._update_workflow_preview())

        req_row = tk.Frame(py_inner, bg=C["surface"])
        req_row.pack(fill="x", pady=3)
        tk.Checkbutton(req_row, text="Install requirements.txt",
                       variable=self.py_req_var,
                       font=F["small"], bg=C["surface"], fg=C["text_dim"],
                       selectcolor=C["surface2"],
                       activebackground=C["surface"], relief="flat", cursor="hand2",
                       command=self._update_workflow_preview).pack(anchor="w")

        # ── Electron fields ───────────────────────────────────────────────
        self.el_frame = tk.Frame(p, bg=C["bg"])
        # (packed/unpacked dynamically)

        el_card = tk.Frame(self.el_frame, bg=C["surface"],
                           highlightbackground=C["border"], highlightthickness=1)
        el_card.pack(fill="x")
        el_inner = tk.Frame(el_card, bg=C["surface"])
        el_inner.pack(fill="x", padx=14, pady=10)

        self.el_build_cmd_var = tk.StringVar(value="npm run build")
        self.el_dist_cmd_var  = tk.StringVar(value="npm run dist")
        self.el_node_var      = tk.StringVar(value="20")

        for label_text, var, w in [
            ("Build command",  self.el_build_cmd_var, 28),
            ("Package command",self.el_dist_cmd_var,  28),
            ("Node version",   self.el_node_var,       8),
        ]:
            row = tk.Frame(el_inner, bg=C["surface"])
            row.pack(fill="x", pady=3)
            tk.Label(row, text=label_text, font=F["label"], fg=C["text_dim"],
                     bg=C["surface"], width=20, anchor="w").pack(side="left")
            e = tk.Entry(row, textvariable=var,
                         font=F["mono_sm"], bg=C["surface2"], fg=C["text"],
                         insertbackground=C["text"], relief="flat",
                         highlightthickness=1,
                         highlightbackground=C["border"],
                         highlightcolor=C["accent"], width=w)
            e.pack(side="left", padx=(6, 0), ipady=3, ipadx=4)
            e.bind("<KeyRelease>", lambda ev: self._update_workflow_preview())

        # ── Workflow preview ──────────────────────────────────────────────
        tk.Label(p, text="Generated workflow  (.github/workflows/build.yml)",
                 font=F["heading"], fg=C["text"],
                 bg=C["bg"]).pack(anchor="w", padx=20, pady=(14, 6))

        prev_f = tk.Frame(p, bg=C["surface"],
                          highlightbackground=C["border"], highlightthickness=1)
        prev_f.pack(fill="x", padx=14, pady=(0, 4))
        self.wf_preview = scrolledtext.ScrolledText(
            prev_f, font=F["mono_sm"], bg=C["bg"], fg=C["text_dim"],
            insertbackground=C["text"], relief="flat",
            height=20, wrap="none", state="disabled"
        )
        self.wf_preview.pack(fill="x", padx=2, pady=2)

        # ── Nuke stale workflows now ──────────────────────────────────
        tk.Label(p, text="One-time cleanup", font=F["heading"],
                 fg=C["text"], bg=C["bg"]).pack(anchor="w", padx=20, pady=(14, 4))

        nuke_card = tk.Frame(p, bg=C["surface"],
                             highlightbackground=C["border"], highlightthickness=1)
        nuke_card.pack(fill="x", padx=14, pady=(0, 12))
        nuke_inner = tk.Frame(nuke_card, bg=C["surface"])
        nuke_inner.pack(fill="x", padx=14, pady=12)
        tk.Label(nuke_inner,
                 text="Use this if the old conda/build-linux workflow keeps running.\n"
                      "Deletes every .yml in .github/workflows/ on GitHub except ziplo-build.yml.",
                 font=F["small"], fg=C["text_dim"], bg=C["surface"],
                 justify="left").pack(anchor="w", pady=(0, 8))
        nuke_row = tk.Frame(nuke_inner, bg=C["surface"])
        nuke_row.pack(fill="x")
        tk.Button(nuke_row, text="Delete stale workflows from GitHub now",
                  font=F["body_b"],
                  bg=C["danger"], fg=C["white"],
                  activebackground="#c0392b", activeforeground=C["white"],
                  relief="flat", padx=16, pady=7, cursor="hand2",
                  command=self._nuke_stale_workflows_now).pack(side="left")
        self.nuke_lbl = tk.Label(nuke_row, text="", font=F["small"],
                                 fg=C["text_dim"], bg=C["surface"])
        self.nuke_lbl.pack(side="left", padx=12)

        # ── Save / status ─────────────────────────────────────────────────
        save_f = tk.Frame(p, bg=C["bg"])
        save_f.pack(fill="x", padx=20, pady=(8, 24))
        tk.Button(save_f, text="Save workflow config", font=F["body_b"],
                  bg=C["navy"], fg=C["mint"],
                  activebackground=C["surface3"], activeforeground=C["mint"],
                  relief="flat", padx=18, pady=8, cursor="hand2",
                  command=self._save_build_config).pack(side="left")
        tk.Button(save_f, text="Reset to autodetect", font=F["small"],
                  bg=C["surface2"], fg=C["text_dim"],
                  activebackground=C["border"], activeforeground=C["text"],
                  relief="flat", padx=12, pady=8, cursor="hand2",
                  command=self._reset_build_config).pack(side="left", padx=(8, 0))
        self.build_save_lbl = tk.Label(save_f, text="", font=F["small"],
                                       fg=C["text_dim"], bg=C["bg"])
        self.build_save_lbl.pack(side="left", padx=12)

        tk.Label(save_f,
                 text="The workflow file is written into your repo on every upload.",
                 font=F["small"], fg=C["text_muted"], bg=C["bg"]).pack(side="left", padx=8)

        self._load_build_config()
        self._update_workflow_preview()
        self._update_workflow_preview()

    def _generate_workflow_yaml(self):
        """Generate the GitHub Actions workflow YAML string based on current settings.
        Uses pinned action versions that target Node 24 to avoid deprecation warnings.
        All jobs run on windows-latest only — no linux runners.
        """
        btype = self.build_type_var.get() if hasattr(self, "build_type_var") else "none"

        # Pinned action versions — Node 24 compatible, no deprecation warnings
        CHECKOUT    = "actions/checkout@v4.2.2"
        SETUP_PY    = "actions/setup-python@v5.3.0"
        SETUP_NODE  = "actions/setup-node@v4.2.0"
        GH_RELEASE  = "softprops/action-gh-release@v2.2.1"

        # Common header for all workflow types
        header = (
            "# Generated by Ziplo — do not edit manually\n"
            "# This file is overwritten on every Ziplo upload\n"
            "name: ziplo-build\n"
            "\n"
            "on:\n"
            "  push:\n"
            "    tags:\n"
            "      - 'v*'\n"
            "\n"
            "permissions:\n"
            "  contents: write\n"
            "\n"
        )

        if btype == "pyinstaller":
            entry   = getattr(self, "py_entry_var", None)
            entry   = entry.get()  if entry  else "ziplo.py"
            args    = getattr(self, "py_args_var",  None)
            args    = args.get()   if args   else "--onefile --windowed"
            name    = getattr(self, "py_name_var",  None)
            name    = name.get()   if name   else "ziplo"
            pyver   = getattr(self, "py_ver_var",   None)
            pyver   = pyver.get()  if pyver  else "3.13"
            use_req = getattr(self, "py_req_var",   None)
            use_req = use_req.get() if use_req else True

            # Only add requirements step if user explicitly has a requirements.txt
            # Use a shell conditional so it never hard-fails when file is absent
            req_step = (
                "\n"
                "      - name: Install requirements\n"
                "        run: if exist requirements.txt pip install -r requirements.txt\n"
            ) if use_req else ""

            return (
                header +
                "jobs:\n"
                "  build-windows:\n"
                "    runs-on: windows-latest\n"
                "\n"
                "    defaults:\n"
                "      run:\n"
                "        shell: cmd\n"
                "\n"
                "    steps:\n"
                "      - name: Checkout\n"
                f"        uses: {CHECKOUT}\n"
                "\n"
                f"      - name: Set up Python {pyver}\n"
                f"        uses: {SETUP_PY}\n"
                "        with:\n"
                f"          python-version: '{pyver}'\n"
                "\n"
                "      - name: Install PyInstaller\n"
                "        run: pip install pyinstaller\n"
                + req_step +
                "\n"
                "      - name: Build executable\n"
                f"        run: pyinstaller {args} --name {name} {entry}\n"
                "\n"
                "      - name: Verify build output\n"
                "        run: dir dist\n"
                "\n"
                "      - name: Upload to GitHub Release\n"
                f"        uses: {GH_RELEASE}\n"
                "        with:\n"
                "          files: dist/*.exe\n"
                "          fail_on_unmatched_files: true\n"
            )

        elif btype == "electron":
            build_cmd = getattr(self, "el_build_cmd_var", None)
            build_cmd = build_cmd.get() if build_cmd else "npm run build"
            dist_cmd  = getattr(self, "el_dist_cmd_var",  None)
            dist_cmd  = dist_cmd.get()  if dist_cmd  else "npm run dist"
            node_ver  = getattr(self, "el_node_var",      None)
            node_ver  = node_ver.get()  if node_ver  else "22"

            return (
                header +
                "jobs:\n"
                "  build-windows:\n"
                "    runs-on: windows-latest\n"
                "\n"
                "    defaults:\n"
                "      run:\n"
                "        shell: cmd\n"
                "\n"
                "    steps:\n"
                "      - name: Checkout\n"
                f"        uses: {CHECKOUT}\n"
                "\n"
                f"      - name: Set up Node {node_ver}\n"
                f"        uses: {SETUP_NODE}\n"
                "        with:\n"
                f"          node-version: '{node_ver}'\n"
                "          cache: npm\n"
                "\n"
                "      - name: Install dependencies\n"
                "        run: npm ci\n"
                "\n"
                f"      - name: Build\n"
                f"        run: {build_cmd}\n"
                "\n"
                f"      - name: Package\n"
                f"        run: {dist_cmd}\n"
                "\n"
                "      - name: Verify build output\n"
                "        run: dir dist\n"
                "\n"
                "      - name: Upload to GitHub Release\n"
                f"        uses: {GH_RELEASE}\n"
                "        with:\n"
                "          files: |\n"
                "            dist\\*.exe\n"
                "            dist\\*.msi\n"
                "          fail_on_unmatched_files: false\n"
            )

        else:
            return (
                header +
                "# Build type set to none — no build steps\n"
                "# Change this in Ziplo's Build tab to enable automated builds\n"
                "jobs:\n"
                "  noop:\n"
                "    runs-on: ubuntu-latest\n"
                "    steps:\n"
                "      - run: echo \'No build configured\'\n"
            )

    def _update_workflow_preview(self):
        """Refresh the YAML preview text box."""
        if not hasattr(self, "wf_preview"):
            return
        # Show/hide python vs electron fields
        if hasattr(self, "build_type_var"):
            btype = self.build_type_var.get()
            if hasattr(self, "py_frame"):
                if btype == "pyinstaller":
                    self.py_frame.pack(fill="x", padx=14, pady=(0, 10))
                else:
                    self.py_frame.pack_forget()
            if hasattr(self, "el_frame"):
                if btype == "electron":
                    self.el_frame.pack(fill="x", padx=14, pady=(0, 10))
                else:
                    self.el_frame.pack_forget()
        yaml = self._generate_workflow_yaml()
        self.wf_preview.configure(state="normal")
        self.wf_preview.delete("1.0", "end")
        self.wf_preview.insert("end", yaml)
        self.wf_preview.configure(state="disabled")

    def _write_workflow_to_repo(self, repo_path):
        """Write the generated workflow file into the repo before committing.
        Wipes every other workflow file locally so stale ones don't get committed.
        Also deletes stale workflow files from the remote repo via GitHub API
        so they can't run even if they were previously pushed.
        """
        # Read from live UI var first, fall back to saved cfg, default pyinstaller
        if hasattr(self, "build_type_var"):
            btype = self.build_type_var.get() or "pyinstaller"
        else:
            btype = self.cfg.get("build_config", {}).get("build_type", "pyinstaller")
        wf_dir = Path(repo_path) / ".github" / "workflows"
        try:
            wf_dir.mkdir(parents=True, exist_ok=True)
            # Wipe every local workflow file so nothing stale gets committed
            for old_wf in list(wf_dir.glob("*.yml")) + list(wf_dir.glob("*.yaml")):
                old_wf.unlink(missing_ok=True)
            if btype == "none":
                return True, ""
            yaml_content = self._generate_workflow_yaml()
            (wf_dir / "ziplo-build.yml").write_text(yaml_content, encoding="utf-8")
            return True, ""
        except Exception as e:
            return False, str(e)

    def _delete_remote_stale_workflows(self, token, repo, branch, log_cb):
        """Delete ALL workflow files from the remote repo except ziplo-build.yml.
        Tries both the target branch and main/master so stale files can't hide.
        This must succeed before the tag is pushed so old workflows don't fire.
        """
        deleted_any = False
        # Check both the working branch and main/master — the stale file may be
        # on a different branch than the one we're pushing to
        refs_to_check = list({branch, "main", "master"})

        for ref in refs_to_check:
            ok, data = github_api("GET",
                f"/repos/{repo}/contents/.github/workflows?ref={ref}",
                token)
            if not ok or not isinstance(data, list):
                continue
            for item in data:
                fname = item.get("name", "")
                if not (fname.endswith(".yml") or fname.endswith(".yaml")):
                    continue
                if fname == "ziplo-build.yml":
                    continue
                sha  = item.get("sha", "")
                path = item.get("path", "")
                log_cb(f"  Deleting stale remote workflow: {fname} (ref={ref})", "warn")
                del_ok, del_data = github_api("DELETE",
                    f"/repos/{repo}/contents/{path}",
                    token,
                    {
                        "message": f"ci: remove stale workflow {fname} [ziplo]",
                        "sha": sha,
                        "branch": ref,
                    })
                if del_ok:
                    log_cb(f"  ✓ Deleted {fname}", "success")
                    deleted_any = True
                else:
                    log_cb(f"  ⚠ Could not delete {fname}: {del_data.get('message','unknown')}", "warn")

        if not deleted_any:
            log_cb("  No stale remote workflows found", "dim")

    def _nuke_stale_workflows_now(self):
        """Button handler — delete stale remote workflows immediately."""
        acct = self._find_account(self.account_var.get())
        if not acct:
            if hasattr(self, "nuke_lbl"):
                self.nuke_lbl.configure(text="⚠ No account selected", fg=C["warn"])
            return
        repo = self.repo_var.get().strip()
        if not repo:
            if hasattr(self, "nuke_lbl"):
                self.nuke_lbl.configure(text="⚠ No repo selected", fg=C["warn"])
            return
        if hasattr(self, "nuke_lbl"):
            self.nuke_lbl.configure(text="Deleting…", fg=C["text_dim"])
        self.update_idletasks()

        def do():
            msgs = []
            def log_cb(msg, tag=""):
                msgs.append(msg)
            self._delete_remote_stale_workflows(
                acct["token"], repo,
                self.branch_var.get().strip() or "main",
                log_cb)
            summary = " | ".join(m for m in msgs if "✓" in m or "⚠" in m or "No stale" in m)
            self.after(0, lambda: self.nuke_lbl.configure(
                text=summary or "Done", fg=C["green"]) if hasattr(self, "nuke_lbl") else None)

        threading.Thread(target=do, daemon=True).start()

    def _reset_build_config(self):
        bc = self.cfg.get("build_config", {})
        bc.pop("user_saved", None)
        self.cfg["build_config"] = bc
        save_config(self.cfg)
        if hasattr(self, "build_save_lbl"):
            self.build_save_lbl.configure(
                text="✓ Reset — autodetect on next upload", fg=C["accent"])
            self.after(3000, lambda: self.build_save_lbl.configure(text="")
                if hasattr(self, "build_save_lbl") else None)

    def _save_build_config(self):
        bc = self.cfg.get("build_config", {})
        bc["user_saved"] = True
        if hasattr(self, "build_type_var"):
            bc["build_type"] = self.build_type_var.get()
        for attr in dir(self):
            if attr.startswith("bv_") or attr.startswith("sv_"):
                key = attr[3:]
                try:
                    bc[key] = getattr(self, attr).get()
                except Exception:
                    pass
        self.cfg["build_config"] = bc
        save_config(self.cfg)
        if hasattr(self, "build_save_lbl"):
            self.build_save_lbl.configure(text="✓ Saved", fg=C["green"])
            self.after(2000, lambda: self.build_save_lbl.configure(text="") if hasattr(self, "build_save_lbl") else None)

    def _load_build_config(self):
        bc = self.cfg.get("build_config", {})
        if hasattr(self, "build_type_var"):
            # Default to pyinstaller if never saved
            self.build_type_var.set(bc.get("build_type", "pyinstaller"))
        for key, val in bc.items():
            for prefix in ("bv_", "sv_"):
                attr = prefix + key
                if hasattr(self, attr):
                    try:
                        getattr(self, attr).set(val)
                    except Exception:
                        pass

    def _run_build_steps(self, repo_path, log_cb):
        """Write the Actions workflow file — actual build happens in GitHub Actions."""
        ok, err = self._write_workflow_to_repo(repo_path)
        if not ok:
            return False, f"Failed to write workflow file: {err}"
        if hasattr(self, "build_type_var"):
            btype = self.build_type_var.get() or "pyinstaller"
        else:
            btype = self.cfg.get("build_config", {}).get("build_type", "pyinstaller")
        if btype != "none":
            log_cb(f"  ✓ GitHub Actions workflow written → .github/workflows/ziplo-build.yml", "success")
            log_cb(f"  Actions will build and attach the .exe when the tag is pushed", "dim")
        return True, ""


    # ── Account logic ──────────────────────────────────────────────────────
    def _refresh_accounts(self):
        accounts = self.cfg.get("accounts", [])
        nicks = [a["nick"] for a in accounts]
        if hasattr(self, "account_combo"):
            self.account_combo["values"] = nicks
            if nicks:
                default = self.cfg.get("default_account") or nicks[0]
                self.account_var.set(default if default in nicks else nicks[0])
        if hasattr(self, "accounts_frame"):
            for w in self.accounts_frame.winfo_children():
                w.destroy()
            for acct in accounts:
                self._account_row(acct)
        self._draw_acct_pill()

    def _account_row(self, acct):
        is_default = acct["nick"] == self.cfg.get("default_account")
        row = tk.Frame(self.accounts_frame, bg=C["surface"],
                       highlightbackground=C["border"], highlightthickness=1)
        row.pack(fill="x", pady=4)
        inner = tk.Frame(row, bg=C["surface"])
        inner.pack(fill="x", padx=14, pady=10)

        star = "★ " if is_default else "   "
        tk.Label(inner, text=star, font=F["small"],
                 fg=C["warn"], bg=C["surface"]).pack(side="left")
        tk.Label(inner, text=acct["nick"], font=F["body_b"],
                 fg=C["text"], bg=C["surface"]).pack(side="left")
        tk.Label(inner, text=f"  @{acct.get('username','—')}",
                 font=F["small"], fg=C["text_muted"],
                 bg=C["surface"]).pack(side="left")
        tok = acct["token"][:6] + "••••" + acct["token"][-4:]
        tk.Label(inner, text=tok, font=F["mono_sm"],
                 fg=C["text_muted"], bg=C["surface"]).pack(side="left", padx=8)

        tk.Button(inner, text="Remove", font=F["small"],
                  bg=C["surface2"], fg=C["danger"],
                  activebackground=C["border"], relief="flat",
                  padx=8, pady=3, cursor="hand2",
                  command=lambda a=acct: self._remove_account(a)).pack(side="right")
        tk.Button(inner, text="Set default", font=F["small"],
                  bg=C["surface2"], fg=C["text_dim"],
                  activebackground=C["border"], relief="flat",
                  padx=8, pady=3, cursor="hand2",
                  command=lambda a=acct: self._set_default_account(a)).pack(side="right", padx=(0, 6))

    def _add_account(self):
        nick  = self.new_nick_var.get().strip()
        token = self.new_token_var.get().strip()
        if not nick:
            self.verify_lbl.configure(text="⚠ Enter a nickname", fg=C["warn"])
            return
        if not token:
            self.verify_lbl.configure(text="⚠ Enter a token", fg=C["warn"])
            return
        self.verify_lbl.configure(text="Verifying…", fg=C["text_dim"])
        self.update_idletasks()
        def do():
            ok, info = validate_token(token)
            self.after(0, lambda: self._finish_add(ok, info, nick, token))
        threading.Thread(target=do, daemon=True).start()

    def _finish_add(self, ok, info, nick, token):
        if not ok:
            self.verify_lbl.configure(text=f"✗ {info.get('message','Invalid')}", fg=C["danger"])
            return
        username = info.get("login", "")
        self.cfg["accounts"] = [a for a in self.cfg.get("accounts", []) if a["nick"] != nick]
        self.cfg["accounts"].append({"nick": nick, "token": token, "username": username})
        if not self.cfg.get("default_account"):
            self.cfg["default_account"] = nick
        save_config(self.cfg)
        self.verify_lbl.configure(text=f"✓ Connected as @{username}", fg=C["green"])
        self.new_nick_var.set("")
        self.new_token_var.set("")
        self._refresh_accounts()

    def _set_default_account(self, acct):
        self.cfg["default_account"] = acct["nick"]
        save_config(self.cfg)
        self._refresh_accounts()

    def _remove_account(self, acct):
        if messagebox.askyesno("Remove", f"Remove '{acct['nick']}'?", parent=self):
            self.cfg["accounts"] = [a for a in self.cfg.get("accounts", [])
                                    if a["nick"] != acct["nick"]]
            if self.cfg.get("default_account") == acct["nick"]:
                self.cfg["default_account"] = None
            save_config(self.cfg)
            self._refresh_accounts()

    def _find_account(self, nick):
        return next((a for a in self.cfg.get("accounts", []) if a["nick"] == nick), None)

    def _on_account_change(self, event=None):
        acct = self._find_account(self.account_var.get())
        if acct:
            self._refresh_repos(token=acct["token"])

    def _refresh_repos(self, token=None):
        if token is None:
            acct = self._find_account(self.account_var.get())
            if not acct:
                return
            token = acct["token"]
        def fetch():
            ok, repos = get_user_repos(token)
            self.after(0, lambda: self._set_repos(repos if ok else []))
        threading.Thread(target=fetch, daemon=True).start()

    def _set_repos(self, repos):
        names = [r["full_name"] for r in repos]
        self.repo_combo["values"] = names
        if names and not self.repo_var.get():
            self.repo_var.set(names[0])

    # ── Browse handlers ────────────────────────────────────────────────────
    def _browse_archive(self):
        path = filedialog.askopenfilename(
            title="Select archive",
            filetypes=[("Archives", "*.zip *.tar.gz *.tgz *.tar.bz2 *.tar.xz"),
                       ("All files", "*.*")])
        if path:
            self.archive_var.set(Path(path).name)
            self._full_archive_path = path
            self.archive_lbl.configure(fg=C["green"])
            if not self.message_var.get():
                stem = Path(path).stem.replace("-", " ").replace("_", " ")
                self.message_var.set(f"Release: {stem}")
        else:
            self._full_archive_path = None

    def _browse_folder(self):
        path = filedialog.askdirectory(title="Select extract destination")
        if path:
            self.folder_var.set(path)

    def _bump(self, part):
        new = bump_version(self.version_var.get(), part)
        self.version_var.set(new)

    # ── Recent / Log ───────────────────────────────────────────────────────
    def _load_recent(self):
        if not hasattr(self, "recent_frame"):
            return
        for w in self.recent_frame.winfo_children():
            w.destroy()
        items = self.cfg.get("recent_projects", [])
        if not items:
            tk.Label(self.recent_frame, text="No releases yet.",
                     font=F["body"], fg=C["text_muted"],
                     bg=C["bg"]).pack(anchor="w", pady=20)
            return
        for item in reversed(items[-30:]):
            row = tk.Frame(self.recent_frame, bg=C["surface"],
                           highlightbackground=C["border"], highlightthickness=1)
            row.pack(fill="x", pady=3)
            inner = tk.Frame(row, bg=C["surface"])
            inner.pack(fill="x", padx=14, pady=8)
            ok_color = C["green"] if item.get("success") else C["danger"]
            icon     = "✓" if item.get("success") else "✗"
            tk.Label(inner, text=icon, font=F["body_b"],
                     fg=ok_color, bg=C["surface"]).pack(side="left")
            tk.Label(inner, text=f"  {item.get('repo','?')}",
                     font=F["body_b"], fg=C["text"],
                     bg=C["surface"]).pack(side="left")
            tk.Label(inner, text=f"  {item.get('version','')}",
                     font=F["mono_sm"], fg=C["accent"],
                     bg=C["surface"]).pack(side="left")
            tk.Label(inner, text=item.get("ts", ""),
                     font=F["small"], fg=C["text_muted"],
                     bg=C["surface"]).pack(side="right")

    def _load_log_display(self):
        if not hasattr(self, "log_display"):
            return
        self.log_display.configure(state="normal")
        self.log_display.delete("1.0", "end")
        try:
            if LOG_FILE.exists():
                self.log_display.insert("end", LOG_FILE.read_text("utf-8"))
                self.log_display.see("end")
        except Exception:
            pass
        self.log_display.configure(state="disabled")

    def _clear_log(self):
        if messagebox.askyesno("Clear log", "Clear all log entries?", parent=self):
            try:
                LOG_FILE.write_text("", "utf-8")
            except Exception:
                pass
            self._load_log_display()

    # ── Step progress UI ───────────────────────────────────────────────────
    def _set_step(self, idx, state):
        # state: "done" | "active" | "pending"
        colors = {
            "done":    (C["green"],    C["green"],   C["surface2"]),
            "active":  (C["accent"],   C["accent"],  C["surface2"]),
            "pending": (C["text_muted"], C["text_muted"], C["surface2"]),
        }
        fg, lbl_fg, num_bg = colors.get(state, colors["pending"])
        if idx < len(self._step_frames):
            self._step_frames[idx].configure(fg=fg, bg=num_bg)
            self._step_labels[idx].configure(fg=lbl_fg)

    def _reset_steps(self):
        for i in range(len(self._step_frames)):
            self._set_step(i, "pending")

    # ── Upload flow ────────────────────────────────────────────────────────
    def _start_upload(self):
        if self._job_running:
            return
        archive = getattr(self, "_full_archive_path", None)
        if not archive:
            messagebox.showerror("No archive", "Select an archive file first.", parent=self)
            return
        if not Path(archive).exists():
            messagebox.showerror("Not found", f"File not found:\n{archive}", parent=self)
            return
        acct = self._find_account(self.account_var.get())
        if not acct:
            messagebox.showerror("No account", "Add a GitHub account first.", parent=self)
            self._show_accounts()
            return
        repo = self.repo_var.get().strip()
        if not repo:
            messagebox.showerror("No repo", "Select a repository.", parent=self)
            return
        version = self.version_var.get().strip()
        if not version:
            messagebox.showerror("No version", "Enter a version tag.", parent=self)
            return
        message = self.message_var.get().strip() or f"Release {version}"

        self._job_running = True
        self._cancelled   = False
        self.go_btn.configure(state="disabled", bg=C["surface3"], fg=C["text_muted"])
        self.cancel_btn.configure(state="normal")
        self.progress_var.set(0)
        self._console_clear()
        self._reset_steps()

        opts = {
            "archive":        archive,
            "token":          acct["token"],
            "repo":           repo,
            "branch":         self.branch_var.get().strip() or "main",
            "version":        version if version.startswith("v") else f"v{version}",
            "message":        message,
            "create_release": self.create_gh_release.get(),
            "push_tags":      self.push_tags_var.get(),
            "pull_first":     self.pull_first_var.get(),
            "force_push":     self.force_push_var.get(),
            "dest_folder":    self.folder_var.get().strip(),
        }
        threading.Thread(target=self._upload_worker, args=(opts,), daemon=True).start()

    def _cancel_upload(self):
        self._cancelled = True
        self._log("⚠ Cancellation requested…", "warn")

    def _upload_worker(self, opts):
        success = False
        STEPS = 7

        def step(i, pct, status):
            for j in range(i):
                self.after(0, lambda j=j: self._set_step(j, "done"))
            self.after(0, lambda: self._set_step(i, "active"))
            self._set_status(status, pct)

        try:
            self._log(f"━━ Ziplo {APP_VERSION} ━━", "heading")
            self._log(f"Archive : {opts['archive']}")
            self._log(f"Repo    : {opts['repo']}")
            self._log(f"Version : {opts['version']}\n")

            # Step 0 — Extract
            step(0, 5, "Extracting archive…")
            dest = opts["dest_folder"]
            if dest:
                out_dir = dest
            else:
                self._temp_dir = tempfile.mkdtemp(prefix="ziplo_")
                out_dir = self._temp_dir
            ok, extracted = extract_archive(opts["archive"], out_dir, self._log)
            if not ok:
                raise RuntimeError(f"Extraction failed: {extracted}")
            repo_path = extracted
            self._log(f"  → {repo_path}\n", "dim")
            if self._cancelled: raise RuntimeError("Cancelled")

            # Step 0b — Autodetect project type and update build config
            self._log("\nDetecting project type…", "heading")
            detected = autodetect_project(repo_path)
            self._log(f"  {detected['reason']}", "dim")
            # Apply detected settings to build config (only if user has not
            # manually overridden by saving a specific config)
            bc = self.cfg.get("build_config", {})
            if not bc.get("user_saved"):
                bc["build_type"] = detected["type"]
                if detected["type"] == "pyinstaller":
                    self.after(0, lambda d=detected: [
                        self.py_entry_var.set(d["entry"]) if hasattr(self, "py_entry_var") else None,
                        self.py_name_var.set(d["name"])   if hasattr(self, "py_name_var")  else None,
                        self.py_args_var.set(d["args"])   if hasattr(self, "py_args_var")  else None,
                        self.build_type_var.set("pyinstaller") if hasattr(self, "build_type_var") else None,
                    ])
                elif detected["type"] == "electron":
                    self.after(0, lambda d=detected: [
                        self.el_build_cmd_var.set(d.get("build_cmd","npm run build")) if hasattr(self, "el_build_cmd_var") else None,
                        self.el_dist_cmd_var.set(d.get("dist_cmd","npm run dist"))   if hasattr(self, "el_dist_cmd_var")  else None,
                        self.build_type_var.set("electron") if hasattr(self, "build_type_var") else None,
                    ])
                self.cfg["build_config"] = bc
            else:
                self._log("  Using saved build config (not overriding)", "dim")

            # Step 0c — Delete stale remote workflows BEFORE committing/tagging
            # so they can't fire when the tag is pushed
            self._log("\nCleaning up stale remote workflows…", "heading")
            self._delete_remote_stale_workflows(
                opts["token"], opts["repo"], opts["branch"], self._log)

            # Step 0d — Write fresh GitHub Actions workflow file into repo
            build_ok, build_err = self._run_build_steps(repo_path, self._log)
            if not build_ok:
                raise RuntimeError(build_err)
            if self._cancelled: raise RuntimeError("Cancelled")

            # Step 1 — Stage (git init/setup)
            step(1, 18, "Configuring git…")
            self._log("Setting up repository…", "heading")
            git_dir = Path(repo_path) / ".git"
            if not git_dir.exists():
                ok, out = run_git(["init"], repo_path, self._log)
                if not ok: raise RuntimeError(f"git init failed: {out}")
                run_git(["checkout", "-b", opts["branch"]], repo_path)
                remote_url = f"https://{opts['token']}@github.com/{opts['repo']}.git"
                ok, out = run_git(["remote", "add", "origin", remote_url], repo_path, self._log)
                if not ok: raise RuntimeError(f"git remote add failed: {out}")
            else:
                self._log("  Existing repo detected", "dim")
                remote_url = f"https://{opts['token']}@github.com/{opts['repo']}.git"
                run_git(["remote", "set-url", "origin", remote_url], repo_path, self._log)

            ok, out = run_git(["add", "."], repo_path, self._log)
            if not ok: raise RuntimeError(f"git add failed: {out}")
            if self._cancelled: raise RuntimeError("Cancelled")

            # Step 2 — Commit
            step(2, 35, "Committing…")
            self._log("\nCommitting…", "heading")
            _, status_out = run_git(["status", "--porcelain"], repo_path)
            if status_out.strip():
                commit_msg = f"{opts['version']}: {opts['message']}"
                ok, out = run_git(["commit", "-m", commit_msg], repo_path, self._log)
                if not ok and "nothing to commit" not in out.lower():
                    raise RuntimeError(f"git commit failed: {out}")
            else:
                self._log("  Nothing new to commit", "warn")
            if self._cancelled: raise RuntimeError("Cancelled")

            # Step 3 — Sync with remote
            # Strategy: fetch remote history and merge with --allow-unrelated-histories.
            # This handles the common case of a fresh zip extract with no shared git
            # history against an existing remote repo. Force push bypasses this entirely.
            step(3, 50, "Syncing with remote…")
            self._log("\nSyncing with remote…", "heading")

            # Always fetch first so we know what's up there
            fetch_ok, fetch_out = run_git(
                ["fetch", "origin"], repo_path, self._log)

            if fetch_ok:
                # Check if the remote branch actually exists yet
                _, ref_check = run_git(
                    ["rev-parse", "--verify", f"origin/{opts['branch']}"],
                    repo_path)
                remote_exists = bool(ref_check.strip())
            else:
                remote_exists = False
                self._log("  Could not reach remote — will attempt push anyway", "warn")

            if remote_exists and not opts.get("force_push"):
                # Merge remote history into local so push won't be rejected.
                # --allow-unrelated-histories handles fresh-extract repos that share
                # no commits with the remote.
                self._log("  Merging remote history…", "dim")
                merge_ok, merge_out = run_git(
                    ["merge", f"origin/{opts['branch']}",
                     "--allow-unrelated-histories",
                     "--no-edit",
                     "-X", "ours"],   # on conflict, keep our (new zip) version
                    repo_path, self._log)
                if not merge_ok:
                    self._log(f"  ⚠ Merge note: {merge_out}", "warn")
                    self._log("  Continuing — will use force push to resolve", "warn")
                    opts["force_push"] = True
            elif remote_exists and opts.get("force_push"):
                self._log("  Force push enabled — skipping merge", "warn")
            else:
                self._log("  No remote branch yet — clean first push", "dim")

            if self._cancelled: raise RuntimeError("Cancelled")

            # Step 4 — Push
            step(4, 65, "Pushing to GitHub…")
            self._log("\nPushing…", "heading")
            push_args = ["push", "-u", "origin", opts["branch"]]
            if opts.get("force_push"):
                push_args.append("--force-with-lease")
                self._log("  (force-with-lease active)", "warn")
            ok, out = run_git(push_args, repo_path, self._log)
            if not ok:
                if "workflow" in out.lower() and ("scope" in out.lower() or "refusing" in out.lower()):
                    raise RuntimeError(
                        "Token missing 'workflow' scope. "
                        "Go to github.com/settings/tokens, edit your token, "
                        "enable the 'workflow' checkbox, then re-save in Ziplo Accounts tab.")
                raise RuntimeError(f"git push failed: {out}")

            if self._cancelled: raise RuntimeError("Cancelled")

            # Step 5 — Tag
            step(5, 82, f"Tagging {opts['version']}…")
            if opts["push_tags"]:
                self._log(f"\nTagging {opts['version']}…", "heading")
                run_git(["tag", "-d", opts["version"]], repo_path)
                ok, out = run_git(["tag", opts["version"]], repo_path, self._log)
                if not ok: raise RuntimeError(f"git tag failed: {out}")
                ok, out = run_git(["push", "origin", opts["version"]], repo_path, self._log)
                if not ok: raise RuntimeError(f"git push tag failed: {out}")

            # Step 6 — GitHub Release
            step(6, 93, "Creating GitHub Release…")
            if opts["create_release"]:
                self._log("\nCreating GitHub Release…", "heading")
                ok, data = github_api("POST",
                                      f"/repos/{opts['repo']}/releases",
                                      opts["token"], {
                                          "tag_name":   opts["version"],
                                          "name":       f"{opts['version']} — {opts['message']}",
                                          "body":       opts["message"],
                                          "draft":      False,
                                          "prerelease": False,
                                      })
                if ok:
                    url = data.get("html_url", "")
                    self._log(f"  ✓ {url}", "success")
                else:
                    err = data.get("message", str(data))
                    if "already_exists" in err or "already exists" in err.lower():
                        self._log("  ⚠ Release already exists for this tag", "warn")
                    else:
                        self._log(f"  ⚠ {err}", "warn")

            success = True
            for i in range(7):
                self.after(0, lambda i=i: self._set_step(i, "done"))
            self._set_status("Done!", 100)
            self._log(f"\n✓ {opts['version']} shipped successfully!", "success")

        except RuntimeError as e:
            self._log(f"\n✗ {e}", "error")
            self._set_status(f"Failed: {e}", 0)
            write_log(f"FAIL {opts.get('repo','')} {opts.get('version','')}: {e}")
        except Exception as e:
            self._log(f"\n✗ Unexpected error: {e}", "error")
            self._set_status(f"Error: {e}", 0)
            write_log(f"EXCEPTION: {e}")
        finally:
            if self._temp_dir and not opts["dest_folder"]:
                shutil.rmtree(self._temp_dir, ignore_errors=True)
                self._temp_dir = None
            self.cfg.setdefault("recent_projects", []).append({
                "repo":    opts.get("repo", ""),
                "version": opts.get("version", ""),
                "message": opts.get("message", ""),
                "success": success,
                "ts":      time.strftime("%Y-%m-%d %H:%M"),
            })
            save_config(self.cfg)
            write_log(f"{'OK' if success else 'FAIL'} {opts.get('repo','')} {opts.get('version','')}")
            self.after(0, self._finish_upload)

    def _finish_upload(self):
        self._job_running = False
        self.go_btn.configure(state="normal", bg=C["navy"], fg=C["mint"])
        self.cancel_btn.configure(state="disabled")

    def _set_status(self, msg, pct):
        self.after(0, lambda: [self.status_var.set(msg), self.progress_var.set(pct)])

    def _log(self, msg, tag=""):
        def _do():
            self.console.configure(state="normal")
            self.console.insert("end", msg + "\n", tag if tag else ())
            self.console.see("end")
            self.console.configure(state="disabled")
        self.after(0, _do)

    def _console_clear(self):
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        self.console.configure(state="disabled")

    def _on_close(self):
        if self._job_running:
            if not messagebox.askyesno("Exit", "A release is in progress. Exit anyway?",
                                       parent=self):
                return
        self.destroy()


# ── Entry ──────────────────────────────────────────────────────────────────
def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    root = ZiploApp()

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("TCombobox",
                    fieldbackground=C["surface2"],
                    background=C["surface2"],
                    foreground=C["text"],
                    selectbackground=C["accent"],
                    selectforeground=C["white"],
                    borderwidth=0, relief="flat")
    style.map("TCombobox",
              fieldbackground=[("readonly", C["surface2"])],
              background=[("readonly", C["surface2"])],
              foreground=[("readonly", C["text"])])

    root.mainloop()


if __name__ == "__main__":
    main()

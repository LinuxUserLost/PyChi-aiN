import json
import os
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


class ChiGitBranchLedgerPage:
    def __init__(self, parent=None, app=None, page_key="", page_folder="", *args, **kwargs):
        self.app = kwargs.pop("controller", app)
        self.page_key = kwargs.pop("page_context", page_key)
        self.page_folder = kwargs.pop("page_folder", page_folder)

        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.data_dir = os.path.join(self.base_dir, "chigit_data")
        self.config_path = os.path.join(self.data_dir, "branchledger_config.json")
        os.makedirs(self.data_dir, exist_ok=True)
        self._ensure_json(self.config_path, {"last_repo_root": ""})
        self.config = self._load_config()

        self.parent = parent
        self.frame = ttk.Frame(parent) if parent is not None else ttk.Frame()

        self.repo_var = tk.StringVar(value=self.config.get("last_repo_root", ""))
        self.current_branch_var = tk.StringVar(value="(unknown)")
        self.head_var = tk.StringVar(value="(unknown)")
        self.upstream_var = tk.StringVar(value="(unknown)")
        self.ahead_behind_var = tk.StringVar(value="ahead 0 / behind 0")
        self.branch_filter_var = tk.StringVar(value="")
        self.commit_filter_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="ready")

        self.local_branch_rows = []
        self.remote_branch_rows = []
        self.visible_local_rows = []
        self.visible_remote_rows = []
        self.commit_rows = []
        self.visible_commit_rows = []
        self.commit_oid_map = {}

        self._build_ui(self.frame)
        self._set_status("Ready. Choose a repo and refresh.")
        self.frame.after(150, self.refresh_all)

    def build(self, parent=None):
        return self._embed_into_parent(parent)

    def create_widgets(self, parent=None):
        return self._embed_into_parent(parent)

    def mount(self, parent=None):
        return self._embed_into_parent(parent)

    def render(self, parent=None):
        return self._embed_into_parent(parent)

    def _embed_into_parent(self, parent=None):
        container = parent or self.parent
        self.parent = container
        try:
            self.frame.pack_forget()
        except Exception:
            pass
        try:
            self.frame.pack(in_=container, fill="both", expand=True)
        except Exception:
            try:
                self.frame.grid(row=0, column=0, sticky="nsew")
            except Exception:
                pass
        return self.frame

    def _build_ui(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(3, weight=1)
        parent.rowconfigure(5, weight=1)
        parent.rowconfigure(6, weight=1)

        repo_box = ttk.LabelFrame(parent, text="Repo")
        repo_box.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        repo_box.columnconfigure(1, weight=1)
        ttk.Label(repo_box, text="Repo root:").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        ttk.Entry(repo_box, textvariable=self.repo_var).grid(row=0, column=1, sticky="ew", padx=6, pady=6)
        ttk.Button(repo_box, text="Browse", command=self.choose_repo).grid(row=0, column=2, padx=6, pady=6)
        ttk.Button(repo_box, text="Refresh", command=self.refresh_all).grid(row=0, column=3, padx=6, pady=6)
        ttk.Button(repo_box, text="Fetch", command=self.fetch_all).grid(row=0, column=4, padx=6, pady=6)

        meta = ttk.LabelFrame(parent, text="Branch state")
        meta.grid(row=1, column=0, sticky="ew", padx=8, pady=4)
        for i in range(4):
            meta.columnconfigure(i, weight=1)
        self._meta_label(meta, 0, 0, "Current branch", self.current_branch_var)
        self._meta_label(meta, 0, 1, "HEAD", self.head_var)
        self._meta_label(meta, 0, 2, "Upstream", self.upstream_var)
        self._meta_label(meta, 0, 3, "Ahead/behind", self.ahead_behind_var)

        toolbar = ttk.LabelFrame(parent, text="Actions")
        toolbar.grid(row=2, column=0, sticky="ew", padx=8, pady=4)
        for i in range(7):
            toolbar.columnconfigure(i, weight=1)
        ttk.Button(toolbar, text="Checkout local", command=self.checkout_selected_local).grid(row=0, column=0, sticky="ew", padx=4, pady=6)
        ttk.Button(toolbar, text="Track remote", command=self.checkout_selected_remote).grid(row=0, column=1, sticky="ew", padx=4, pady=6)
        ttk.Button(toolbar, text="Copy branch", command=self.copy_selected_branch).grid(row=0, column=2, sticky="ew", padx=4, pady=6)
        ttk.Button(toolbar, text="Copy commit", command=self.copy_selected_commit).grid(row=0, column=3, sticky="ew", padx=4, pady=6)
        ttk.Button(toolbar, text="Show changed files", command=self.show_selected_commit_files).grid(row=0, column=4, sticky="ew", padx=4, pady=6)
        ttk.Button(toolbar, text="Show commit details", command=self.show_selected_commit_details).grid(row=0, column=5, sticky="ew", padx=4, pady=6)
        ttk.Button(toolbar, text="Copy summary", command=self.copy_summary).grid(row=0, column=6, sticky="ew", padx=4, pady=6)

        middle = ttk.Panedwindow(parent, orient=tk.HORIZONTAL)
        middle.grid(row=3, column=0, sticky="nsew", padx=8, pady=4)

        local_box = ttk.Labelframe(middle, text="Local branches")
        local_box.columnconfigure(0, weight=1)
        local_box.rowconfigure(1, weight=1)
        local_filter = ttk.Frame(local_box)
        local_filter.grid(row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=(6, 2))
        local_filter.columnconfigure(1, weight=1)
        ttk.Label(local_filter, text="Filter:").grid(row=0, column=0, padx=(0, 4), pady=2)
        branch_entry = ttk.Entry(local_filter, textvariable=self.branch_filter_var)
        branch_entry.grid(row=0, column=1, sticky="ew", padx=4, pady=2)
        branch_entry.bind("<KeyRelease>", lambda _event: self._apply_branch_filters())
        self.local_list = tk.Listbox(local_box, exportselection=False)
        self.local_list.grid(row=1, column=0, sticky="nsew", padx=(6, 0), pady=(2, 6))
        self.local_list.bind("<<ListboxSelect>>", lambda _event: self._on_local_branch_selected())
        local_scroll = ttk.Scrollbar(local_box, orient="vertical", command=self.local_list.yview)
        local_scroll.grid(row=1, column=1, sticky="ns", padx=(0, 6), pady=(2, 6))
        self.local_list.configure(yscrollcommand=local_scroll.set)
        middle.add(local_box, weight=1)

        remote_box = ttk.Labelframe(middle, text="Remote branches")
        remote_box.columnconfigure(0, weight=1)
        remote_box.rowconfigure(1, weight=1)
        remote_filter = ttk.Frame(remote_box)
        remote_filter.grid(row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=(6, 2))
        ttk.Label(remote_filter, text="Uses same filter").grid(row=0, column=0, padx=4, pady=2, sticky="w")
        self.remote_list = tk.Listbox(remote_box, exportselection=False)
        self.remote_list.grid(row=1, column=0, sticky="nsew", padx=(6, 0), pady=(2, 6))
        self.remote_list.bind("<<ListboxSelect>>", lambda _event: self._on_remote_branch_selected())
        remote_scroll = ttk.Scrollbar(remote_box, orient="vertical", command=self.remote_list.yview)
        remote_scroll.grid(row=1, column=1, sticky="ns", padx=(0, 6), pady=(2, 6))
        self.remote_list.configure(yscrollcommand=remote_scroll.set)
        middle.add(remote_box, weight=1)

        commits_box = ttk.LabelFrame(parent, text="Recent commits")
        commits_box.grid(row=5, column=0, sticky="nsew", padx=8, pady=4)
        commits_box.columnconfigure(0, weight=1)
        commits_box.rowconfigure(1, weight=1)
        commit_filter = ttk.Frame(commits_box)
        commit_filter.grid(row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=(6, 2))
        commit_filter.columnconfigure(1, weight=1)
        ttk.Label(commit_filter, text="Filter:").grid(row=0, column=0, padx=(0, 4), pady=2)
        commit_entry = ttk.Entry(commit_filter, textvariable=self.commit_filter_var)
        commit_entry.grid(row=0, column=1, sticky="ew", padx=4, pady=2)
        commit_entry.bind("<KeyRelease>", lambda _event: self._apply_commit_filter())
        self.commit_list = tk.Listbox(commits_box, exportselection=False)
        self.commit_list.grid(row=1, column=0, sticky="nsew", padx=(6, 0), pady=(2, 6))
        self.commit_list.bind("<<ListboxSelect>>", lambda _event: self.show_selected_commit_summary())
        commit_scroll = ttk.Scrollbar(commits_box, orient="vertical", command=self.commit_list.yview)
        commit_scroll.grid(row=1, column=1, sticky="ns", padx=(0, 6), pady=(2, 6))
        self.commit_list.configure(yscrollcommand=commit_scroll.set)

        details_box = ttk.LabelFrame(parent, text="Commit details / files")
        details_box.grid(row=6, column=0, sticky="nsew", padx=8, pady=(4, 8))
        details_box.columnconfigure(0, weight=1)
        details_box.rowconfigure(0, weight=1)
        self.details_text = tk.Text(details_box, wrap="word", height=12)
        self.details_text.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        details_scroll = ttk.Scrollbar(details_box, orient="vertical", command=self.details_text.yview)
        details_scroll.grid(row=0, column=1, sticky="ns", padx=(0, 6), pady=6)
        self.details_text.configure(yscrollcommand=details_scroll.set)

        status = ttk.Frame(parent)
        status.grid(row=7, column=0, sticky="ew", padx=8, pady=(0, 8))
        status.columnconfigure(0, weight=1)
        ttk.Label(status, textvariable=self.status_var).grid(row=0, column=0, sticky="w")

    def _ensure_json(self, path, payload):
        if os.path.isfile(path):
            return
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)

    def _load_config(self):
        try:
            with open(self.config_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {}

    def _save_config(self):
        payload = {"last_repo_root": self.repo_var.get().strip()}
        try:
            with open(self.config_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
            self.config = payload
        except Exception:
            pass

    def _meta_label(self, parent, row, col, title, variable):
        box = ttk.Frame(parent)
        box.grid(row=row, column=col, sticky="ew", padx=4, pady=2)
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text=f"{title}:").grid(row=0, column=0, sticky="w")
        ttk.Label(box, textvariable=variable).grid(row=0, column=1, sticky="w")

    def _set_status(self, message):
        self.status_var.set(message)

    def _repo_root(self):
        return (self.repo_var.get() or "").strip()

    def _run_git(self, args, cwd=None):
        cmd = ["git"] + list(args)
        try:
            completed = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
            merged = (stdout + ("\n" if stdout and stderr else "") + stderr).strip()
            return {"code": completed.returncode, "stdout": stdout, "stderr": stderr, "text": merged}
        except FileNotFoundError:
            return {"code": 127, "stdout": "", "stderr": "git not found", "text": "git not found"}
        except Exception as exc:
            return {"code": 1, "stdout": "", "stderr": str(exc), "text": str(exc)}

    def _validate_repo(self, show_error=True):
        root = self._repo_root()
        if not root:
            if show_error:
                messagebox.showwarning("Branch Ledger", "Choose a repo root first.")
            return None
        if not os.path.isdir(root):
            if show_error:
                messagebox.showerror("Branch Ledger", f"Folder does not exist:\n{root}")
            return None
        probe = self._run_git(["rev-parse", "--git-dir"], cwd=root)
        if probe["code"] != 0:
            if show_error:
                messagebox.showerror("Branch Ledger", f"Not a git repo:\n{root}")
            return None
        return root

    def choose_repo(self):
        chosen = filedialog.askdirectory(initialdir=self.repo_var.get() or os.path.expanduser("~"))
        if chosen:
            self.repo_var.set(chosen)
            self._save_config()
            self.refresh_all()

    def refresh_all(self):
        root = self._validate_repo(show_error=False)
        if not root:
            self._set_status("Choose a valid git repo to begin.")
            return
        self._save_config()
        self.refresh_branch_state(root)
        self.refresh_branches(root)
        self.refresh_commits(root)
        self._set_status(f"Refreshed branch ledger for {root}")

    def refresh_branch_state(self, root):
        branch_res = self._run_git(["branch", "--show-current"], cwd=root)
        head_res = self._run_git(["rev-parse", "--short", "HEAD"], cwd=root)
        upstream_res = self._run_git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"], cwd=root)
        ahead_behind_res = self._run_git(["rev-list", "--left-right", "--count", "HEAD...@{upstream}"], cwd=root)
        self.current_branch_var.set((branch_res["stdout"] or "").strip() or "(detached/unknown)")
        self.head_var.set((head_res["stdout"] or "").strip() or "(none)")
        upstream = (upstream_res["stdout"] or "").strip()
        if upstream_res["code"] == 0 and upstream:
            self.upstream_var.set(upstream)
            self.ahead_behind_var.set(self._parse_ahead_behind(ahead_behind_res["stdout"]))
        else:
            self.upstream_var.set("(no upstream)")
            self.ahead_behind_var.set("ahead ? / behind ?")

    def _parse_ahead_behind(self, text):
        try:
            left, right = (text or "").strip().split()
            return f"ahead {left} / behind {right}"
        except Exception:
            return "ahead ? / behind ?"

    def refresh_branches(self, root):
        local_res = self._run_git(["for-each-ref", "--sort=-committerdate", "--format=%(refname:short)|%(committerdate:short)|%(subject)", "refs/heads"], cwd=root)
        remote_res = self._run_git(["for-each-ref", "--sort=-committerdate", "--format=%(refname:short)|%(committerdate:short)|%(subject)", "refs/remotes"], cwd=root)
        self.local_branch_rows = self._parse_ref_rows(local_res["stdout"])
        self.remote_branch_rows = [row for row in self._parse_ref_rows(remote_res["stdout"]) if row["name"] != "origin/HEAD"]
        self._apply_branch_filters()

    def _parse_ref_rows(self, text):
        rows = []
        for line in (text or "").splitlines():
            if not line.strip():
                continue
            parts = line.split("|", 2)
            while len(parts) < 3:
                parts.append("")
            rows.append({"name": parts[0], "date": parts[1], "subject": parts[2]})
        return rows

    def _apply_branch_filters(self):
        needle = (self.branch_filter_var.get() or "").strip().lower()
        if needle:
            self.visible_local_rows = [row for row in self.local_branch_rows if needle in row["name"].lower() or needle in row["subject"].lower()]
            self.visible_remote_rows = [row for row in self.remote_branch_rows if needle in row["name"].lower() or needle in row["subject"].lower()]
        else:
            self.visible_local_rows = list(self.local_branch_rows)
            self.visible_remote_rows = list(self.remote_branch_rows)

        self.local_list.delete(0, "end")
        current = self.current_branch_var.get().strip()
        for row in self.visible_local_rows:
            prefix = "* " if row["name"] == current else "  "
            text = f"{prefix}{row['name']}    {row['date']}    {row['subject']}"
            self.local_list.insert("end", text)

        self.remote_list.delete(0, "end")
        for row in self.visible_remote_rows:
            text = f"{row['name']}    {row['date']}    {row['subject']}"
            self.remote_list.insert("end", text)

    def refresh_commits(self, root, ref=None):
        target = ref or self.current_branch_var.get().strip() or "HEAD"
        fmt = "%H%x1f%h%x1f%ad%x1f%an%x1f%s"
        res = self._run_git(["log", target, "--decorate=short", "--date=short", f"--pretty=format:{fmt}", "-n", "80"], cwd=root)
        self.commit_rows = []
        for line in (res["stdout"] or "").splitlines():
            parts = line.split("\x1f")
            if len(parts) >= 5:
                full_oid, short_oid, date, author, subject = parts[:5]
                self.commit_rows.append({
                    "oid": full_oid,
                    "short_oid": short_oid,
                    "date": date,
                    "author": author,
                    "subject": subject,
                })
        self._apply_commit_filter()

    def _apply_commit_filter(self):
        needle = (self.commit_filter_var.get() or "").strip().lower()
        if needle:
            self.visible_commit_rows = [row for row in self.commit_rows if needle in row["short_oid"].lower() or needle in row["author"].lower() or needle in row["subject"].lower()]
        else:
            self.visible_commit_rows = list(self.commit_rows)
        self.commit_list.delete(0, "end")
        self.commit_oid_map = {}
        for idx, row in enumerate(self.visible_commit_rows):
            text = f"{row['short_oid']}    {row['date']}    {row['author']}    {row['subject']}"
            self.commit_list.insert("end", text)
            self.commit_oid_map[idx] = row["oid"]

    def _selected_local_branch(self):
        sel = self.local_list.curselection()
        if not sel:
            return None
        idx = sel[0]
        if 0 <= idx < len(self.visible_local_rows):
            return self.visible_local_rows[idx]["name"]
        return None

    def _selected_remote_branch(self):
        sel = self.remote_list.curselection()
        if not sel:
            return None
        idx = sel[0]
        if 0 <= idx < len(self.visible_remote_rows):
            return self.visible_remote_rows[idx]["name"]
        return None

    def _selected_commit_oid(self):
        sel = self.commit_list.curselection()
        if not sel:
            return None
        return self.commit_oid_map.get(sel[0])

    def _on_local_branch_selected(self):
        branch = self._selected_local_branch()
        root = self._validate_repo(show_error=False)
        if branch and root:
            self.refresh_commits(root, ref=branch)
            self._set_status(f"Showing recent commits for local branch: {branch}")

    def _on_remote_branch_selected(self):
        branch = self._selected_remote_branch()
        root = self._validate_repo(show_error=False)
        if branch and root:
            self.refresh_commits(root, ref=branch)
            self._set_status(f"Showing recent commits for remote branch: {branch}")

    def checkout_selected_local(self):
        root = self._validate_repo()
        if not root:
            return
        branch = self._selected_local_branch()
        if not branch:
            messagebox.showinfo("Branch Ledger", "Select a local branch first.")
            return
        if not messagebox.askyesno("Branch Ledger", f"Checkout local branch:\n\n{branch}"):
            self._set_status("Checkout cancelled.")
            return
        res = self._run_git(["checkout", branch], cwd=root)
        self._write_details(res["text"])
        self.refresh_all()

    def checkout_selected_remote(self):
        root = self._validate_repo()
        if not root:
            return
        remote_branch = self._selected_remote_branch()
        if not remote_branch:
            messagebox.showinfo("Branch Ledger", "Select a remote branch first.")
            return
        if "/" not in remote_branch:
            messagebox.showinfo("Branch Ledger", "Remote branch format was unexpected.")
            return
        local_name = remote_branch.split("/", 1)[1]
        if not messagebox.askyesno("Branch Ledger", f"Create or checkout tracking branch:\n\n{local_name}\nfrom\n{remote_branch}"):
            self._set_status("Track-remote cancelled.")
            return
        res = self._run_git(["checkout", "-B", local_name, "--track", remote_branch], cwd=root)
        if res["code"] != 0:
            res = self._run_git(["checkout", local_name], cwd=root)
        self._write_details(res["text"])
        self.refresh_all()

    def fetch_all(self):
        root = self._validate_repo()
        if not root:
            return
        res = self._run_git(["fetch", "--all", "--prune"], cwd=root)
        self._write_details(res["text"])
        self.refresh_all()

    def show_selected_commit_summary(self):
        oid = self._selected_commit_oid()
        root = self._validate_repo(show_error=False)
        if oid and root:
            self.show_selected_commit_details()

    def show_selected_commit_details(self):
        root = self._validate_repo()
        if not root:
            return
        oid = self._selected_commit_oid()
        if not oid:
            messagebox.showinfo("Branch Ledger", "Select a commit first.")
            return
        res = self._run_git(["show", "--stat", "--summary", "--format=fuller", oid], cwd=root)
        self._write_details(res["text"])
        self._set_status(f"Showing details for commit {oid[:8]}")

    def show_selected_commit_files(self):
        root = self._validate_repo()
        if not root:
            return
        oid = self._selected_commit_oid()
        if not oid:
            messagebox.showinfo("Branch Ledger", "Select a commit first.")
            return
        res = self._run_git(["show", "--name-status", "--format=fuller", oid], cwd=root)
        self._write_details(res["text"])
        self._set_status(f"Showing changed files for commit {oid[:8]}")

    def _write_details(self, text):
        self.details_text.delete("1.0", "end")
        self.details_text.insert("1.0", (text or "").strip() or "(no output)")

    def copy_selected_branch(self):
        branch = self._selected_local_branch() or self._selected_remote_branch()
        if not branch:
            messagebox.showinfo("Branch Ledger", "Select a branch first.")
            return
        self._copy_text(branch, "branch name")

    def copy_selected_commit(self):
        oid = self._selected_commit_oid()
        if not oid:
            messagebox.showinfo("Branch Ledger", "Select a commit first.")
            return
        self._copy_text(oid, "commit id")

    def copy_summary(self):
        summary = (
            f"repo={self._repo_root()}\n"
            f"current_branch={self.current_branch_var.get()}\n"
            f"head={self.head_var.get()}\n"
            f"upstream={self.upstream_var.get()}\n"
            f"ahead_behind={self.ahead_behind_var.get()}\n"
            f"local_branches={len(self.local_branch_rows)}\n"
            f"remote_branches={len(self.remote_branch_rows)}\n"
            f"visible_commits={len(self.visible_commit_rows)}"
        )
        self._copy_text(summary, "branch summary")

    def _copy_text(self, text, label):
        try:
            self.frame.clipboard_clear()
            self.frame.clipboard_append(text)
            self._set_status(f"Copied {label} to clipboard.")
        except Exception as exc:
            self._write_details(f"Failed to copy {label}: {exc}")

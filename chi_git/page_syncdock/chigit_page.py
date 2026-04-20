import json
import os
import subprocess
from datetime import datetime, timezone
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk


class ChiGitSyncDockPage:
    def __init__(self, parent=None, app=None, page_key="", page_folder="", *args, **kwargs):
        self.app = kwargs.pop("controller", app)
        self.page_key = kwargs.pop("page_context", page_key)
        self.page_folder = kwargs.pop("page_folder", page_folder)

        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.data_dir = os.path.join(self.base_dir, "chigit_data")
        self.config_path = os.path.join(self.data_dir, "chigit_config.json")
        self.log_path = os.path.join(self.data_dir, "sync_log.jsonl")

        os.makedirs(self.data_dir, exist_ok=True)
        self._ensure_file(self.config_path, {
            "last_repo_root": "",
            "last_commit_message": "",
            "last_work_note": "",
            "auto_refresh_ms": 0
        })
        self._ensure_text_file(self.log_path)

        self.config = self._load_config()
        self.parent = parent
        self.frame = ttk.Frame(parent) if parent is not None else ttk.Frame()

        self.repo_var = tk.StringVar(value=self.config.get("last_repo_root", ""))
        self.branch_var = tk.StringVar(value="(unknown)")
        self.head_var = tk.StringVar(value="(unknown)")
        self.remote_var = tk.StringVar(value="(unknown)")
        self.last_commit_var = tk.StringVar(value="(none)")
        self.upstream_var = tk.StringVar(value="(none)")
        self.ahead_behind_var = tk.StringVar(value="ahead 0 / behind 0")
        self.refresh_note_var = tk.StringVar(value="ready")
        self.commit_msg_var = tk.StringVar(value=self.config.get("last_commit_message", ""))
        self.work_note_var = tk.StringVar(value=self.config.get("last_work_note", ""))
        self.branch_entry_var = tk.StringVar(value="")
        self.filter_var = tk.StringVar(value="")
        self.selection_summary_var = tk.StringVar(value="0 selected")

        self.file_rows = []
        self.visible_rows = []

        self._build_ui(self.frame)
        self._set_status("Ready. Pick a repo folder or use the saved one.")
        self.frame.after(150, self.refresh_status)

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
        parent.rowconfigure(7, weight=1)

        top = ttk.LabelFrame(parent, text="Repo")
        top.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 4))
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Repo root:").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        ttk.Entry(top, textvariable=self.repo_var).grid(row=0, column=1, sticky="ew", padx=6, pady=6)
        ttk.Button(top, text="Browse", command=self.choose_repo).grid(row=0, column=2, padx=6, pady=6)
        ttk.Button(top, text="Open repo", command=self.open_repo_folder).grid(row=0, column=3, padx=6, pady=6)
        ttk.Button(top, text="Refresh", command=self.refresh_status).grid(row=0, column=4, padx=6, pady=6)

        meta = ttk.Frame(top)
        meta.grid(row=1, column=0, columnspan=5, sticky="ew", padx=6, pady=(0, 6))
        for i in range(3):
            meta.columnconfigure(i, weight=1)

        self._meta_label(meta, 0, 0, "Branch", self.branch_var)
        self._meta_label(meta, 0, 1, "HEAD", self.head_var)
        self._meta_label(meta, 0, 2, "Upstream", self.upstream_var)
        self._meta_label(meta, 1, 0, "Ahead/behind", self.ahead_behind_var)
        self._meta_label(meta, 1, 1, "Remote", self.remote_var)
        self._meta_label(meta, 1, 2, "Last commit", self.last_commit_var)

        branch_box = ttk.LabelFrame(parent, text="Branch tools")
        branch_box.grid(row=1, column=0, sticky="ew", padx=8, pady=4)
        for i in range(7):
            branch_box.columnconfigure(i, weight=1)

        ttk.Label(branch_box, text="Branch:").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        ttk.Entry(branch_box, textvariable=self.branch_entry_var).grid(row=0, column=1, columnspan=2, sticky="ew", padx=6, pady=6)
        ttk.Button(branch_box, text="Fill current", command=self.fill_current_branch).grid(row=0, column=3, sticky="ew", padx=4, pady=6)
        ttk.Button(branch_box, text="Checkout", command=self.checkout_branch).grid(row=0, column=4, sticky="ew", padx=4, pady=6)
        ttk.Button(branch_box, text="Create branch", command=self.create_branch_from_current).grid(row=0, column=5, sticky="ew", padx=4, pady=6)
        ttk.Button(branch_box, text="Copy status", command=self.copy_status_summary).grid(row=0, column=6, sticky="ew", padx=4, pady=6)

        actions = ttk.LabelFrame(parent, text="Sync actions")
        actions.grid(row=2, column=0, sticky="ew", padx=8, pady=4)
        for i in range(8):
            actions.columnconfigure(i, weight=1)

        ttk.Button(actions, text="Fetch", command=self.fetch_remote).grid(row=0, column=0, sticky="ew", padx=4, pady=6)
        ttk.Button(actions, text="Pull --ff-only", command=self.pull_remote).grid(row=0, column=1, sticky="ew", padx=4, pady=6)
        ttk.Button(actions, text="Push", command=self.push_remote).grid(row=0, column=2, sticky="ew", padx=4, pady=6)
        ttk.Button(actions, text="Stage selected", command=self.stage_selected).grid(row=0, column=3, sticky="ew", padx=4, pady=6)
        ttk.Button(actions, text="Unstage selected", command=self.unstage_selected).grid(row=0, column=4, sticky="ew", padx=4, pady=6)
        ttk.Button(actions, text="Stage all", command=self.stage_all).grid(row=0, column=5, sticky="ew", padx=4, pady=6)
        ttk.Button(actions, text="Refresh log", command=self._refresh_log_view).grid(row=0, column=6, sticky="ew", padx=4, pady=6)
        ttk.Button(actions, text="Commit snapshot", command=self.commit_changes).grid(row=0, column=7, sticky="ew", padx=4, pady=6)

        files = ttk.LabelFrame(parent, text="Changed files")
        files.grid(row=3, column=0, sticky="nsew", padx=8, pady=4)
        files.columnconfigure(0, weight=1)
        files.rowconfigure(1, weight=1)

        file_toolbar = ttk.Frame(files)
        file_toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=(6, 2))
        file_toolbar.columnconfigure(8, weight=1)

        ttk.Button(file_toolbar, text="Select all", command=self.select_all_files).grid(row=0, column=0, padx=(0, 4), pady=2)
        ttk.Button(file_toolbar, text="Clear selection", command=self.clear_file_selection).grid(row=0, column=1, padx=4, pady=2)
        ttk.Button(file_toolbar, text="Select staged", command=self.select_staged_files).grid(row=0, column=2, padx=4, pady=2)
        ttk.Button(file_toolbar, text="Select unstaged", command=self.select_unstaged_files).grid(row=0, column=3, padx=4, pady=2)
        ttk.Button(file_toolbar, text="Tracked only", command=self.select_tracked_changes).grid(row=0, column=4, padx=4, pady=2)
        ttk.Button(file_toolbar, text="Untracked only", command=self.select_untracked_files).grid(row=0, column=5, padx=4, pady=2)
        ttk.Label(file_toolbar, text="Filter:").grid(row=0, column=6, padx=(12, 4), pady=2)
        filter_entry = ttk.Entry(file_toolbar, textvariable=self.filter_var)
        filter_entry.grid(row=0, column=7, sticky="ew", padx=4, pady=2)
        filter_entry.bind("<KeyRelease>", lambda _event: self._apply_file_filter())
        ttk.Label(file_toolbar, textvariable=self.selection_summary_var).grid(row=0, column=8, sticky="e", padx=(8, 0), pady=2)

        self.files_list = tk.Listbox(files, selectmode=tk.EXTENDED)
        self.files_list.grid(row=1, column=0, sticky="nsew", padx=(6, 0), pady=(2, 6))
        self.files_list.bind("<<ListboxSelect>>", lambda _event: self._update_selection_summary())
        files_scroll = ttk.Scrollbar(files, orient="vertical", command=self.files_list.yview)
        files_scroll.grid(row=1, column=1, sticky="ns", padx=(0, 6), pady=(2, 6))
        self.files_list.configure(yscrollcommand=files_scroll.set)

        commit = ttk.LabelFrame(parent, text="Commit + work note")
        commit.grid(row=4, column=0, sticky="ew", padx=8, pady=4)
        commit.columnconfigure(1, weight=1)

        ttk.Label(commit, text="Commit message:").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        ttk.Entry(commit, textvariable=self.commit_msg_var).grid(row=0, column=1, sticky="ew", padx=6, pady=6)
        ttk.Label(commit, text="Work note:").grid(row=1, column=0, sticky="w", padx=6, pady=6)
        ttk.Entry(commit, textvariable=self.work_note_var).grid(row=1, column=1, sticky="ew", padx=6, pady=6)
        ttk.Button(commit, text="Save note only", command=self.save_note_only).grid(row=0, column=2, rowspan=2, sticky="ns", padx=6, pady=6)

        output = ttk.LabelFrame(parent, text="Command output")
        output.grid(row=5, column=0, sticky="nsew", padx=8, pady=4)
        output.columnconfigure(0, weight=1)
        output.rowconfigure(0, weight=1)
        self.output_text = tk.Text(output, wrap="word", height=12)
        self.output_text.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        out_scroll = ttk.Scrollbar(output, orient="vertical", command=self.output_text.yview)
        out_scroll.grid(row=0, column=1, sticky="ns", padx=(0, 6), pady=6)
        self.output_text.configure(yscrollcommand=out_scroll.set)

        output_toolbar = ttk.Frame(output)
        output_toolbar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=6, pady=(0, 6))
        ttk.Button(output_toolbar, text="Clear output", command=self.clear_output).pack(side="left", padx=(0, 4))
        ttk.Button(output_toolbar, text="Copy output", command=self.copy_output).pack(side="left", padx=4)

        history = ttk.LabelFrame(parent, text="Recent sync log")
        history.grid(row=7, column=0, sticky="nsew", padx=8, pady=(4, 8))
        history.columnconfigure(0, weight=1)
        history.rowconfigure(0, weight=1)
        self.log_text = tk.Text(history, wrap="word", height=10)
        self.log_text.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        log_scroll = ttk.Scrollbar(history, orient="vertical", command=self.log_text.yview)
        log_scroll.grid(row=0, column=1, sticky="ns", padx=(0, 6), pady=6)
        self.log_text.configure(yscrollcommand=log_scroll.set)

        log_toolbar = ttk.Frame(history)
        log_toolbar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=6, pady=(0, 6))
        ttk.Button(log_toolbar, text="Copy log", command=self.copy_log).pack(side="left", padx=(0, 4))
        ttk.Button(log_toolbar, text="Open log folder", command=self.open_data_folder).pack(side="left", padx=4)

        status = ttk.Frame(parent)
        status.grid(row=8, column=0, sticky="ew", padx=8, pady=(0, 8))
        status.columnconfigure(0, weight=1)
        ttk.Label(status, textvariable=self.refresh_note_var).grid(row=0, column=0, sticky="w")

    def _meta_label(self, parent, row, col, title, variable):
        box = ttk.Frame(parent)
        box.grid(row=row, column=col, sticky="ew", padx=4, pady=2)
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text=f"{title}:").grid(row=0, column=0, sticky="w")
        ttk.Label(box, textvariable=variable).grid(row=0, column=1, sticky="w")

    def choose_repo(self):
        chosen = filedialog.askdirectory(initialdir=self.repo_var.get() or os.path.expanduser("~"))
        if chosen:
            self.repo_var.set(chosen)
            self._save_config()
            self.refresh_status()

    def open_repo_folder(self):
        root = self._validate_repo()
        if not root:
            return
        self._open_folder(root, "repo")

    def open_data_folder(self):
        self._open_folder(self.data_dir, "page data")

    def _open_folder(self, path, label):
        try:
            subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._set_status(f"Opened {label} folder: {path}")
        except FileNotFoundError:
            self._append_output("open folder", f"xdg-open not found. Open manually: {path}")
        except Exception as exc:
            self._append_output("open folder", f"Could not open {label} folder: {exc}")

    def fill_current_branch(self):
        current = self.branch_var.get().strip()
        if current and current != "(unknown)":
            self.branch_entry_var.set(current)
            self._set_status("Filled branch field with current branch.")

    def save_note_only(self):
        self._save_config()
        self._log_action("note_saved", self._repo_root(), [], {"code": 0, "text": "saved local note"}, extra={
            "work_note": self.work_note_var.get().strip()
        })
        self._set_status("Saved work note to local config/log.")

    def clear_output(self):
        self.output_text.delete("1.0", "end")
        self._set_status("Cleared output panel.")

    def copy_output(self):
        text = self.output_text.get("1.0", "end-1c")
        self._copy_text(text, "command output")

    def copy_log(self):
        text = self.log_text.get("1.0", "end-1c")
        self._copy_text(text, "sync log")

    def copy_status_summary(self):
        root = self._repo_root()
        summary = (
            f"repo={root}\n"
            f"branch={self.branch_var.get()}\n"
            f"head={self.head_var.get()}\n"
            f"upstream={self.upstream_var.get()}\n"
            f"ahead_behind={self.ahead_behind_var.get()}\n"
            f"last_commit={self.last_commit_var.get()}\n"
            f"changed_files={len(self.file_rows)}"
        )
        self._copy_text(summary, "status summary")

    def _copy_text(self, text, label):
        if not text.strip():
            messagebox.showinfo("ChiGit", f"No {label} to copy.")
            return
        try:
            self.frame.clipboard_clear()
            self.frame.clipboard_append(text)
            self._set_status(f"Copied {label} to clipboard.")
        except Exception as exc:
            self._append_output("clipboard", f"Failed to copy {label}: {exc}")

    def _set_status(self, message):
        self.refresh_note_var.set(message)

    def _append_output(self, title, text):
        stamp = self._now_local()
        clean_text = (text or "").strip() or "(no output)"
        self.output_text.insert("end", f"\n[{stamp}] {title}\n{clean_text}\n")
        self.output_text.see("end")

    def _replace_log_view(self, text):
        self.log_text.delete("1.0", "end")
        self.log_text.insert("1.0", text)

    def _repo_root(self):
        return (self.repo_var.get() or "").strip()

    def _validate_repo(self, show_error=True):
        root = self._repo_root()
        if not root:
            if show_error:
                messagebox.showwarning("ChiGit", "Choose a repo root first.")
            return None
        if not os.path.isdir(root):
            if show_error:
                messagebox.showerror("ChiGit", f"Folder does not exist:\n{root}")
            return None
        if not os.path.isdir(os.path.join(root, ".git")):
            check = self._run_git(["rev-parse", "--git-dir"], root, quiet=True)
            if check["code"] != 0:
                if show_error:
                    messagebox.showerror("ChiGit", f"Not a git repo:\n{root}")
                return None
        return root

    def _run_git(self, args, cwd, quiet=False):
        cmd = ["git"] + list(args)
        try:
            completed = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
            )
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
            merged = (stdout + ("\n" if stdout and stderr else "") + stderr).strip()
            if not quiet:
                self._append_output("git " + " ".join(args), merged)
            return {"code": completed.returncode, "stdout": stdout, "stderr": stderr, "text": merged}
        except FileNotFoundError:
            msg = "git executable was not found on this system PATH."
            if not quiet:
                self._append_output("git " + " ".join(args), msg)
            return {"code": 127, "stdout": "", "stderr": msg, "text": msg}
        except Exception as exc:
            msg = f"git command failed to launch: {exc}"
            if not quiet:
                self._append_output("git " + " ".join(args), msg)
            return {"code": 1, "stdout": "", "stderr": msg, "text": msg}

    def refresh_status(self):
        root = self._validate_repo(show_error=False)
        if not root:
            self.branch_var.set("(not set)")
            self.head_var.set("(not set)")
            self.remote_var.set("(not set)")
            self.last_commit_var.set("(not set)")
            self.upstream_var.set("(not set)")
            self.ahead_behind_var.set("ahead 0 / behind 0")
            self.files_list.delete(0, "end")
            self.file_rows = []
            self.visible_rows = []
            self._update_selection_summary()
            self._set_status("Pick a valid git repo to begin.")
            self._refresh_log_view()
            return

        self.config["last_repo_root"] = root
        self._save_config()

        status_res = self._run_git(["status", "--short", "--branch"], root, quiet=True)
        current_branch_res = self._run_git(["branch", "--show-current"], root, quiet=True)
        head_res = self._run_git(["rev-parse", "--short", "HEAD"], root, quiet=True)
        remote_res = self._run_git(["remote", "-v"], root, quiet=True)
        last_res = self._run_git(["log", "-1", "--pretty=%h %ad %s", "--date=short"], root, quiet=True)
        upstream_res = self._run_git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"], root, quiet=True)
        ahead_behind_res = self._run_git(["rev-list", "--left-right", "--count", "HEAD...@{upstream}"], root, quiet=True)

        _, file_rows = self._parse_status(status_res["stdout"])
        current_branch = (current_branch_res["stdout"] or "").strip()
        self.branch_var.set(current_branch or "(detached/unknown)")
        self.head_var.set((head_res["stdout"] or "").strip() or "(none)")
        self.remote_var.set(self._first_remote(remote_res["stdout"]))
        self.last_commit_var.set((last_res["stdout"] or "").strip() or "(none)")

        upstream = (upstream_res["stdout"] or "").strip()
        if upstream_res["code"] == 0 and upstream:
            self.upstream_var.set(upstream)
            self.ahead_behind_var.set(self._parse_ahead_behind(ahead_behind_res["stdout"]))
        else:
            self.upstream_var.set("(no upstream)")
            self.ahead_behind_var.set("ahead ? / behind ?")

        self.file_rows = file_rows
        self._apply_file_filter(preserve_selection=False)
        self._refresh_log_view()
        self._set_status(f"Status refreshed for {root}")

    def _parse_status(self, text):
        branch = ""
        file_rows = []
        for raw_line in (text or "").splitlines():
            line = raw_line.rstrip("\n")
            if not line:
                continue
            if line.startswith("## "):
                branch = line[3:].strip()
                continue
            status = line[:2]
            path = line[3:] if len(line) > 3 else ""
            original_path = path
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            file_rows.append({
                "status": status,
                "path": path,
                "raw_path": original_path,
                "staged": status[0] not in (" ", "?"),
                "unstaged": status[1] not in (" ",),
                "untracked": status == "??",
            })
        return branch, file_rows

    def _parse_ahead_behind(self, text):
        try:
            left, right = (text or "").strip().split()
            return f"ahead {left} / behind {right}"
        except Exception:
            return "ahead ? / behind ?"

    def _first_remote(self, text):
        for line in (text or "").splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
        return "(none)"

    def _selected_visible_rows(self):
        rows = []
        for index in self.files_list.curselection():
            if 0 <= index < len(self.visible_rows):
                rows.append(self.visible_rows[index])
        return rows

    def _selected_paths(self):
        return [row["path"] for row in self._selected_visible_rows()]

    def select_all_files(self):
        self.files_list.selection_set(0, "end")
        self._update_selection_summary()

    def clear_file_selection(self):
        self.files_list.selection_clear(0, "end")
        self._update_selection_summary()

    def select_staged_files(self):
        self._select_matching_rows(lambda row: row["staged"])

    def select_unstaged_files(self):
        self._select_matching_rows(lambda row: row["unstaged"] or row["untracked"])

    def select_tracked_changes(self):
        self._select_matching_rows(lambda row: not row["untracked"])

    def select_untracked_files(self):
        self._select_matching_rows(lambda row: row["untracked"])

    def _select_matching_rows(self, predicate):
        self.files_list.selection_clear(0, "end")
        for idx, row in enumerate(self.visible_rows):
            if predicate(row):
                self.files_list.selection_set(idx)
        self._update_selection_summary()

    def _apply_file_filter(self, preserve_selection=True):
        previous_paths = set(self._selected_paths()) if preserve_selection else set()
        needle = (self.filter_var.get() or "").strip().lower()
        if needle:
            self.visible_rows = [row for row in self.file_rows if needle in row["path"].lower() or needle in row["status"].lower()]
        else:
            self.visible_rows = list(self.file_rows)

        self.files_list.delete(0, "end")
        for row in self.visible_rows:
            tags = []
            if row["staged"]:
                tags.append("staged")
            if row["unstaged"]:
                tags.append("unstaged")
            if row["untracked"]:
                tags.append("untracked")
            tag_text = ", ".join(tags)
            self.files_list.insert("end", f"[{row['status']}] {row['path']}" + (f"   <{tag_text}>" if tag_text else ""))

        for idx, row in enumerate(self.visible_rows):
            if row["path"] in previous_paths:
                self.files_list.selection_set(idx)
        self._update_selection_summary()

    def _update_selection_summary(self):
        count = len(self.files_list.curselection())
        total = len(self.visible_rows)
        self.selection_summary_var.set(f"{count} selected / {total} shown")

    def stage_selected(self):
        root = self._validate_repo()
        if not root:
            return
        paths = self._selected_paths()
        if not paths:
            messagebox.showinfo("ChiGit", "Select one or more changed files first.")
            return
        res = self._run_git(["add", "--"] + paths, root)
        self._log_action("stage_selected", root, paths, res)
        self.refresh_status()

    def unstage_selected(self):
        root = self._validate_repo()
        if not root:
            return
        paths = self._selected_paths()
        if not paths:
            messagebox.showinfo("ChiGit", "Select one or more staged files first.")
            return
        res = self._run_git(["restore", "--staged", "--"] + paths, root)
        self._log_action("unstage_selected", root, paths, res)
        self.refresh_status()

    def stage_all(self):
        root = self._validate_repo()
        if not root:
            return
        res = self._run_git(["add", "-A"], root)
        self._log_action("stage_all", root, ["*"], res)
        self.refresh_status()

    def commit_changes(self):
        root = self._validate_repo()
        if not root:
            return
        message = (self.commit_msg_var.get() or "").strip()
        if not message:
            messagebox.showwarning("ChiGit", "Add a commit message first.")
            return
        diff_res = self._run_git(["diff", "--cached", "--name-only"], root, quiet=True)
        staged_files = [line.strip() for line in diff_res["stdout"].splitlines() if line.strip()]
        if not staged_files:
            messagebox.showinfo("ChiGit", "Nothing is staged. Stage files first.")
            return
        if not messagebox.askyesno("ChiGit", f"Commit {len(staged_files)} staged file(s)?\n\nMessage:\n{message}"):
            self._set_status("Commit cancelled.")
            return
        res = self._run_git(["commit", "-m", message], root)
        self.config["last_commit_message"] = message
        self.config["last_work_note"] = self.work_note_var.get().strip()
        self._save_config()
        self._log_action("commit", root, staged_files, res, extra={
            "commit_message": message,
            "work_note": self.work_note_var.get().strip()
        })
        self.refresh_status()

    def fetch_remote(self):
        self._run_simple_remote_action("fetch", ["fetch", "--all", "--prune"])

    def pull_remote(self):
        root = self._validate_repo()
        if not root:
            return
        if not messagebox.askyesno("ChiGit", "Run git pull --ff-only ?"):
            self._set_status("Pull cancelled.")
            return
        res = self._run_git(["pull", "--ff-only"], root)
        self._log_action("pull", root, [], res, extra={
            "work_note": self.work_note_var.get().strip()
        })
        self.refresh_status()

    def push_remote(self):
        root = self._validate_repo()
        if not root:
            return
        if not messagebox.askyesno("ChiGit", "Run git push on the current branch?"):
            self._set_status("Push cancelled.")
            return
        res = self._run_git(["push"], root)
        self._log_action("push", root, [], res, extra={
            "work_note": self.work_note_var.get().strip()
        })
        self.refresh_status()

    def _run_simple_remote_action(self, action_name, git_args):
        root = self._validate_repo()
        if not root:
            return
        res = self._run_git(git_args, root)
        self._log_action(action_name, root, [], res, extra={
            "work_note": self.work_note_var.get().strip()
        })
        self.refresh_status()

    def checkout_branch(self):
        root = self._validate_repo()
        if not root:
            return
        branch_name = (self.branch_entry_var.get() or "").strip()
        if not branch_name:
            messagebox.showwarning("ChiGit", "Type a branch name first.")
            return
        res = self._run_git(["checkout", branch_name], root)
        self._log_action("checkout_branch", root, [], res, extra={"target_branch": branch_name})
        self.refresh_status()

    def create_branch_from_current(self):
        root = self._validate_repo()
        if not root:
            return
        branch_name = (self.branch_entry_var.get() or "").strip()
        if not branch_name:
            branch_name = simpledialog.askstring("ChiGit", "New branch name:", parent=self.frame)
            branch_name = (branch_name or "").strip()
            self.branch_entry_var.set(branch_name)
        if not branch_name:
            return
        res = self._run_git(["checkout", "-b", branch_name], root)
        self._log_action("create_branch", root, [], res, extra={"target_branch": branch_name})
        self.refresh_status()

    def _log_action(self, action, repo_root, paths, result, extra=None):
        payload = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "repo_root": repo_root,
            "branch_display": self.branch_var.get(),
            "head_display": self.head_var.get(),
            "upstream_display": self.upstream_var.get(),
            "ahead_behind_display": self.ahead_behind_var.get(),
            "paths": list(paths),
            "result_code": result.get("code"),
            "result_text": result.get("text", "")[:4000],
        }
        if extra:
            payload.update(extra)
        try:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception as exc:
            self._append_output("log write", f"failed to write sync log: {exc}")
        self._refresh_log_view()

    def _refresh_log_view(self):
        items = []
        try:
            with open(self.log_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        items.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except FileNotFoundError:
            pass
        items = items[-20:]
        lines = []
        for item in reversed(items):
            when = item.get("timestamp_utc", "")
            action = item.get("action", "")
            code = item.get("result_code", "")
            paths = item.get("paths", [])
            msg = item.get("commit_message", "")
            note = item.get("work_note", "")
            branch = item.get("branch_display", "")
            lines.append(f"{when} | {action} | code={code} | {branch}")
            if paths:
                lines.append(f"paths: {', '.join(paths[:8])}")
            if msg:
                lines.append(f"commit: {msg}")
            if note:
                lines.append(f"note: {note}")
            lines.append("")
        self._replace_log_view("\n".join(lines).strip() or "(no sync log entries yet)")

    def _load_config(self):
        try:
            with open(self.config_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {
            "last_repo_root": "",
            "last_commit_message": "",
            "last_work_note": "",
            "auto_refresh_ms": 0
        }

    def _save_config(self):
        self.config["last_repo_root"] = self.repo_var.get().strip()
        self.config["last_commit_message"] = self.commit_msg_var.get().strip()
        self.config["last_work_note"] = self.work_note_var.get().strip()
        try:
            with open(self.config_path, "w", encoding="utf-8") as fh:
                json.dump(self.config, fh, indent=2, ensure_ascii=False)
        except Exception as exc:
            self._append_output("config write", f"failed to save config: {exc}")

    def _ensure_file(self, path, default_data):
        if os.path.isfile(path):
            return
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(default_data, fh, indent=2, ensure_ascii=False)

    def _ensure_text_file(self, path):
        if os.path.isfile(path):
            return
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("")

    def _now_local(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

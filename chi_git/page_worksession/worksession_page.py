import json
import os
import subprocess
import tkinter as tk
from datetime import datetime, timezone
from tkinter import filedialog, messagebox, ttk


class ChiGitWorkSessionPage:
    def __init__(self, parent=None, app=None, page_key="", page_folder="", *args, **kwargs):
        self.app = kwargs.pop("controller", app)
        self.page_key = kwargs.pop("page_context", page_key)
        self.page_folder = kwargs.pop("page_folder", page_folder)

        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.data_dir = os.path.join(self.base_dir, "chigit_data")
        self.config_path = os.path.join(self.data_dir, "worksession_config.json")
        self.log_path = os.path.join(self.data_dir, "worksession_log.jsonl")
        os.makedirs(self.data_dir, exist_ok=True)

        self._ensure_json(self.config_path, {
            "last_repo_root": "",
            "last_lane": "",
            "last_agent": "",
            "last_task_id": "",
            "last_pagepack": "",
            "last_note": ""
        })
        self._ensure_text(self.log_path)
        self.config = self._load_config()

        self.parent = parent
        self.frame = ttk.Frame(parent) if parent is not None else ttk.Frame()

        self.repo_var = tk.StringVar(value=self.config.get("last_repo_root", ""))
        self.lane_var = tk.StringVar(value=self.config.get("last_lane", ""))
        self.agent_var = tk.StringVar(value=self.config.get("last_agent", ""))
        self.task_id_var = tk.StringVar(value=self.config.get("last_task_id", ""))
        self.pagepack_var = tk.StringVar(value=self.config.get("last_pagepack", ""))
        self.branch_var = tk.StringVar(value="(unknown)")
        self.head_var = tk.StringVar(value="(unknown)")
        self.status_var = tk.StringVar(value="ready")
        self.snapshot_summary_var = tk.StringVar(value="No snapshot yet")

        self.entry_rows = []
        self.visible_rows = []

        self._build_ui(self.frame)
        self.frame.after(150, self.refresh_repo_state)
        self.frame.after(250, self.refresh_log_list)

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
        parent.rowconfigure(4, weight=1)
        parent.rowconfigure(5, weight=1)

        repo_box = ttk.LabelFrame(parent, text="Repo")
        repo_box.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        repo_box.columnconfigure(1, weight=1)
        ttk.Label(repo_box, text="Repo root:").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        ttk.Entry(repo_box, textvariable=self.repo_var).grid(row=0, column=1, sticky="ew", padx=6, pady=6)
        ttk.Button(repo_box, text="Browse", command=self.choose_repo).grid(row=0, column=2, padx=6, pady=6)
        ttk.Button(repo_box, text="Refresh state", command=self.refresh_repo_state).grid(row=0, column=3, padx=6, pady=6)
        ttk.Label(repo_box, text="Branch:").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        ttk.Label(repo_box, textvariable=self.branch_var).grid(row=1, column=1, sticky="w", padx=6, pady=4)
        ttk.Label(repo_box, text="HEAD:").grid(row=1, column=2, sticky="e", padx=6, pady=4)
        ttk.Label(repo_box, textvariable=self.head_var).grid(row=1, column=3, sticky="w", padx=6, pady=4)

        session_box = ttk.LabelFrame(parent, text="Work session fields")
        session_box.grid(row=1, column=0, sticky="ew", padx=8, pady=4)
        session_box.columnconfigure(1, weight=1)
        session_box.columnconfigure(3, weight=1)

        ttk.Label(session_box, text="Lane:").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        lane_combo = ttk.Combobox(session_box, textvariable=self.lane_var, values=["browser", "claude_cli", "local_qwen", "manual_terminal", "guichi_manual", "other"])
        lane_combo.grid(row=0, column=1, sticky="ew", padx=6, pady=6)
        ttk.Label(session_box, text="Agent:").grid(row=0, column=2, sticky="w", padx=6, pady=6)
        ttk.Entry(session_box, textvariable=self.agent_var).grid(row=0, column=3, sticky="ew", padx=6, pady=6)

        ttk.Label(session_box, text="Task id:").grid(row=1, column=0, sticky="w", padx=6, pady=6)
        ttk.Entry(session_box, textvariable=self.task_id_var).grid(row=1, column=1, sticky="ew", padx=6, pady=6)
        ttk.Label(session_box, text="Pagepack:").grid(row=1, column=2, sticky="w", padx=6, pady=6)
        ttk.Entry(session_box, textvariable=self.pagepack_var).grid(row=1, column=3, sticky="ew", padx=6, pady=6)

        note_box = ttk.LabelFrame(parent, text="Work note")
        note_box.grid(row=2, column=0, sticky="nsew", padx=8, pady=4)
        note_box.columnconfigure(0, weight=1)
        note_box.rowconfigure(0, weight=1)
        self.note_text = tk.Text(note_box, wrap="word", height=7)
        self.note_text.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        note_scroll = ttk.Scrollbar(note_box, orient="vertical", command=self.note_text.yview)
        note_scroll.grid(row=0, column=1, sticky="ns", padx=(0, 6), pady=6)
        self.note_text.configure(yscrollcommand=note_scroll.set)
        if self.config.get("last_note"):
            self.note_text.insert("1.0", self.config.get("last_note", ""))

        action_box = ttk.LabelFrame(parent, text="Actions")
        action_box.grid(row=3, column=0, sticky="ew", padx=8, pady=4)
        for i in range(6):
            action_box.columnconfigure(i, weight=1)
        ttk.Button(action_box, text="Save draft fields", command=self.save_fields_only).grid(row=0, column=0, sticky="ew", padx=4, pady=6)
        ttk.Button(action_box, text="Save work entry", command=self.save_work_entry).grid(row=0, column=1, sticky="ew", padx=4, pady=6)
        ttk.Button(action_box, text="Save snapshot entry", command=self.save_snapshot_entry).grid(row=0, column=2, sticky="ew", padx=4, pady=6)
        ttk.Button(action_box, text="Refresh entries", command=self.refresh_log_list).grid(row=0, column=3, sticky="ew", padx=4, pady=6)
        ttk.Button(action_box, text="Copy summary", command=self.copy_current_summary).grid(row=0, column=4, sticky="ew", padx=4, pady=6)
        ttk.Button(action_box, text="Open data folder", command=self.open_data_folder).grid(row=0, column=5, sticky="ew", padx=4, pady=6)

        list_box = ttk.LabelFrame(parent, text="Recent work entries")
        list_box.grid(row=4, column=0, sticky="nsew", padx=8, pady=4)
        list_box.columnconfigure(0, weight=1)
        list_box.rowconfigure(0, weight=1)
        self.entry_list = tk.Listbox(list_box, exportselection=False)
        self.entry_list.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        self.entry_list.bind("<<ListboxSelect>>", lambda _event: self.show_selected_entry())
        list_scroll = ttk.Scrollbar(list_box, orient="vertical", command=self.entry_list.yview)
        list_scroll.grid(row=0, column=1, sticky="ns", padx=(0, 6), pady=6)
        self.entry_list.configure(yscrollcommand=list_scroll.set)

        detail_box = ttk.LabelFrame(parent, text="Entry details")
        detail_box.grid(row=5, column=0, sticky="nsew", padx=8, pady=(4, 8))
        detail_box.columnconfigure(0, weight=1)
        detail_box.rowconfigure(0, weight=1)
        self.detail_text = tk.Text(detail_box, wrap="word", height=12)
        self.detail_text.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        detail_scroll = ttk.Scrollbar(detail_box, orient="vertical", command=self.detail_text.yview)
        detail_scroll.grid(row=0, column=1, sticky="ns", padx=(0, 6), pady=6)
        self.detail_text.configure(yscrollcommand=detail_scroll.set)

        status = ttk.Frame(parent)
        status.grid(row=6, column=0, sticky="ew", padx=8, pady=(0, 8))
        status.columnconfigure(0, weight=1)
        ttk.Label(status, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        ttk.Label(status, textvariable=self.snapshot_summary_var).grid(row=0, column=1, sticky="e")

    def _ensure_json(self, path, payload):
        if os.path.isfile(path):
            return
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)

    def _ensure_text(self, path):
        if os.path.isfile(path):
            return
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("")

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
        payload = {
            "last_repo_root": self.repo_var.get().strip(),
            "last_lane": self.lane_var.get().strip(),
            "last_agent": self.agent_var.get().strip(),
            "last_task_id": self.task_id_var.get().strip(),
            "last_pagepack": self.pagepack_var.get().strip(),
            "last_note": self.note_text.get("1.0", "end-1c").strip(),
        }
        with open(self.config_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
        self.config = payload

    def _set_status(self, message):
        self.status_var.set(message)

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
        root = self.repo_var.get().strip()
        if not root:
            if show_error:
                messagebox.showwarning("WorkSession", "Choose a repo root first.")
            return None
        if not os.path.isdir(root):
            if show_error:
                messagebox.showerror("WorkSession", f"Folder does not exist:\n{root}")
            return None
        probe = self._run_git(["rev-parse", "--git-dir"], cwd=root)
        if probe["code"] != 0:
            if show_error:
                messagebox.showerror("WorkSession", f"Not a git repo:\n{root}")
            return None
        return root

    def choose_repo(self):
        chosen = filedialog.askdirectory(initialdir=self.repo_var.get() or os.path.expanduser("~"))
        if chosen:
            self.repo_var.set(chosen)
            self._save_config()
            self.refresh_repo_state()

    def refresh_repo_state(self):
        root = self._validate_repo(show_error=False)
        if not root:
            self.branch_var.set("(not set)")
            self.head_var.set("(not set)")
            self.snapshot_summary_var.set("No snapshot yet")
            self._set_status("Choose a valid repo to begin.")
            return
        self._save_config()
        branch_res = self._run_git(["branch", "--show-current"], cwd=root)
        head_res = self._run_git(["rev-parse", "--short", "HEAD"], cwd=root)
        status_res = self._run_git(["status", "--short"], cwd=root)
        changed_count = len([line for line in status_res["stdout"].splitlines() if line.strip()])
        self.branch_var.set((branch_res["stdout"] or "").strip() or "(detached/unknown)")
        self.head_var.set((head_res["stdout"] or "").strip() or "(none)")
        self.snapshot_summary_var.set(f"{changed_count} changed file(s)")
        self._set_status("Repo state refreshed.")

    def _make_entry(self, entry_type):
        root = self._validate_repo()
        if not root:
            return None
        self.refresh_repo_state()
        self._save_config()
        note = self.note_text.get("1.0", "end-1c").strip()
        status_res = self._run_git(["status", "--short"], cwd=root)
        last_commit_res = self._run_git(["log", "-1", "--pretty=%H%x1f%h%x1f%ad%x1f%s", "--date=short"], cwd=root)
        last_commit_parts = (last_commit_res["stdout"] or "").split("\x1f")
        while len(last_commit_parts) < 4:
            last_commit_parts.append("")
        changed_files = [line.strip() for line in status_res["stdout"].splitlines() if line.strip()]
        return {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "entry_type": entry_type,
            "repo_root": root,
            "lane": self.lane_var.get().strip(),
            "agent": self.agent_var.get().strip(),
            "task_id": self.task_id_var.get().strip(),
            "pagepack": self.pagepack_var.get().strip(),
            "branch": self.branch_var.get().strip(),
            "head": self.head_var.get().strip(),
            "note": note,
            "changed_files": changed_files,
            "changed_file_count": len(changed_files),
            "last_commit_full": last_commit_parts[0],
            "last_commit_short": last_commit_parts[1],
            "last_commit_date": last_commit_parts[2],
            "last_commit_subject": last_commit_parts[3],
        }

    def save_fields_only(self):
        self._save_config()
        self._set_status("Saved draft fields locally.")

    def save_work_entry(self):
        entry = self._make_entry("work_entry")
        if not entry:
            return
        self._append_entry(entry)
        self.refresh_log_list()
        self._set_status("Saved work entry.")

    def save_snapshot_entry(self):
        entry = self._make_entry("snapshot_entry")
        if not entry:
            return
        self._append_entry(entry)
        self.refresh_log_list()
        self._set_status("Saved snapshot entry.")

    def _append_entry(self, entry):
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def refresh_log_list(self):
        rows = []
        try:
            with open(self.log_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except FileNotFoundError:
            pass
        self.entry_rows = list(reversed(rows[-100:]))
        self.visible_rows = list(self.entry_rows)
        self.entry_list.delete(0, "end")
        for row in self.visible_rows:
            stamp = row.get("timestamp_utc", "")
            entry_type = row.get("entry_type", "")
            lane = row.get("lane", "") or "(no lane)"
            branch = row.get("branch", "") or "(no branch)"
            task = row.get("task_id", "") or "(no task)"
            note = (row.get("note", "") or "").splitlines()[0][:50]
            text = f"{stamp} | {entry_type} | {lane} | {branch} | {task} | {note}"
            self.entry_list.insert("end", text)

    def show_selected_entry(self):
        sel = self.entry_list.curselection()
        if not sel:
            return
        idx = sel[0]
        if not (0 <= idx < len(self.visible_rows)):
            return
        row = self.visible_rows[idx]
        pretty = json.dumps(row, indent=2, ensure_ascii=False)
        self.detail_text.delete("1.0", "end")
        self.detail_text.insert("1.0", pretty)
        self._set_status("Showing selected work entry.")

    def copy_current_summary(self):
        summary = {
            "repo_root": self.repo_var.get().strip(),
            "lane": self.lane_var.get().strip(),
            "agent": self.agent_var.get().strip(),
            "task_id": self.task_id_var.get().strip(),
            "pagepack": self.pagepack_var.get().strip(),
            "branch": self.branch_var.get().strip(),
            "head": self.head_var.get().strip(),
            "note": self.note_text.get("1.0", "end-1c").strip(),
        }
        text = json.dumps(summary, indent=2, ensure_ascii=False)
        try:
            self.frame.clipboard_clear()
            self.frame.clipboard_append(text)
            self._set_status("Copied current session summary.")
        except Exception as exc:
            self.detail_text.delete("1.0", "end")
            self.detail_text.insert("1.0", f"Clipboard copy failed: {exc}")

    def open_data_folder(self):
        try:
            subprocess.Popen(["xdg-open", self.data_dir], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._set_status("Opened ChiGit data folder.")
        except Exception as exc:
            self.detail_text.delete("1.0", "end")
            self.detail_text.insert("1.0", f"Could not open data folder: {exc}")

import json
import os
import shlex
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


class ChiGitSSHDockPage:
    def __init__(self, parent=None, app=None, page_key="", page_folder="", *args, **kwargs):
        self.app = kwargs.pop("controller", app)
        self.page_key = kwargs.pop("page_context", page_key)
        self.page_folder = kwargs.pop("page_folder", page_folder)

        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.data_dir = os.path.join(self.base_dir, "chigit_data")
        self.config_path = os.path.join(self.data_dir, "sshdock_config.json")
        os.makedirs(self.data_dir, exist_ok=True)
        self._ensure_json(self.config_path, {"last_repo_root": "", "last_public_key_path": os.path.expanduser("~/.ssh/id_ed25519.pub"), "last_test_host": "git@github.com"})
        self.config = self._load_config()

        self.parent = parent
        self.frame = ttk.Frame(parent) if parent is not None else ttk.Frame()

        self.repo_var = tk.StringVar(value=self.config.get("last_repo_root", ""))
        self.agent_var = tk.StringVar(value="(unknown)")
        self.sock_var = tk.StringVar(value="(unknown)")
        self.keys_var = tk.StringVar(value="(unknown)")
        self.remote_var = tk.StringVar(value="(unknown)")
        self.remote_mode_var = tk.StringVar(value="(unknown)")
        self.status_var = tk.StringVar(value="ready")
        self.public_key_path_var = tk.StringVar(value=self.config.get("last_public_key_path", os.path.expanduser("~/.ssh/id_ed25519.pub")))
        self.test_host_var = tk.StringVar(value=self.config.get("last_test_host", "git@github.com"))

        self._build_ui(self.frame)
        self._set_status("Ready. Refresh SSH status to begin.")
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
        parent.rowconfigure(4, weight=1)
        parent.rowconfigure(6, weight=1)

        repo_box = ttk.LabelFrame(parent, text="Repo")
        repo_box.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        repo_box.columnconfigure(1, weight=1)
        ttk.Label(repo_box, text="Repo root:").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        ttk.Entry(repo_box, textvariable=self.repo_var).grid(row=0, column=1, sticky="ew", padx=6, pady=6)
        ttk.Button(repo_box, text="Browse", command=self.choose_repo).grid(row=0, column=2, padx=6, pady=6)
        ttk.Button(repo_box, text="Refresh all", command=self.refresh_all).grid(row=0, column=3, padx=6, pady=6)

        status_box = ttk.LabelFrame(parent, text="SSH status")
        status_box.grid(row=1, column=0, sticky="ew", padx=8, pady=4)
        for i in range(3):
            status_box.columnconfigure(i, weight=1)
        self._meta_label(status_box, 0, 0, "Agent", self.agent_var)
        self._meta_label(status_box, 0, 1, "SSH_AUTH_SOCK", self.sock_var)
        self._meta_label(status_box, 0, 2, "Loaded keys", self.keys_var)
        self._meta_label(status_box, 1, 0, "Remote", self.remote_var)
        self._meta_label(status_box, 1, 1, "Remote mode", self.remote_mode_var)
        self._meta_label(status_box, 1, 2, "Test host", self.test_host_var)

        public_box = ttk.LabelFrame(parent, text="Public key")
        public_box.grid(row=2, column=0, sticky="ew", padx=8, pady=4)
        public_box.columnconfigure(1, weight=1)
        ttk.Label(public_box, text="Public key path:").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        ttk.Entry(public_box, textvariable=self.public_key_path_var).grid(row=0, column=1, sticky="ew", padx=6, pady=6)
        ttk.Button(public_box, text="Pick", command=self.pick_public_key).grid(row=0, column=2, padx=6, pady=6)
        ttk.Button(public_box, text="Show public key", command=self.show_public_key).grid(row=0, column=3, padx=6, pady=6)

        actions_box = ttk.LabelFrame(parent, text="Actions")
        actions_box.grid(row=3, column=0, sticky="ew", padx=8, pady=4)
        for i in range(6):
            actions_box.columnconfigure(i, weight=1)
        ttk.Button(actions_box, text="Check agent", command=self.check_agent).grid(row=0, column=0, sticky="ew", padx=4, pady=6)
        ttk.Button(actions_box, text="List keys", command=self.list_keys).grid(row=0, column=1, sticky="ew", padx=4, pady=6)
        ttk.Button(actions_box, text="List ~/.ssh", command=self.list_ssh_dir).grid(row=0, column=2, sticky="ew", padx=4, pady=6)
        ttk.Button(actions_box, text="Test GitHub SSH", command=self.test_github_ssh).grid(row=0, column=3, sticky="ew", padx=4, pady=6)
        ttk.Button(actions_box, text="Check remote", command=self.check_remote_mode).grid(row=0, column=4, sticky="ew", padx=4, pady=6)
        ttk.Button(actions_box, text="Copy remote", command=self.copy_remote).grid(row=0, column=5, sticky="ew", padx=4, pady=6)

        preview_box = ttk.LabelFrame(parent, text="Public key preview")
        preview_box.grid(row=4, column=0, sticky="nsew", padx=8, pady=4)
        preview_box.columnconfigure(0, weight=1)
        preview_box.rowconfigure(0, weight=1)
        self.public_text = tk.Text(preview_box, wrap="word", height=8)
        self.public_text.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        preview_scroll = ttk.Scrollbar(preview_box, orient="vertical", command=self.public_text.yview)
        preview_scroll.grid(row=0, column=1, sticky="ns", padx=(0, 6), pady=6)
        self.public_text.configure(yscrollcommand=preview_scroll.set)
        preview_toolbar = ttk.Frame(preview_box)
        preview_toolbar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=6, pady=(0, 6))
        ttk.Button(preview_toolbar, text="Copy public key", command=self.copy_public_key).pack(side="left", padx=(0, 4))
        ttk.Button(preview_toolbar, text="Clear preview", command=lambda: self.public_text.delete("1.0", "end")).pack(side="left", padx=4)

        output_box = ttk.LabelFrame(parent, text="SSH output")
        output_box.grid(row=6, column=0, sticky="nsew", padx=8, pady=(4, 8))
        output_box.columnconfigure(0, weight=1)
        output_box.rowconfigure(0, weight=1)
        self.output_text = tk.Text(output_box, wrap="word", height=12)
        self.output_text.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        output_scroll = ttk.Scrollbar(output_box, orient="vertical", command=self.output_text.yview)
        output_scroll.grid(row=0, column=1, sticky="ns", padx=(0, 6), pady=6)
        self.output_text.configure(yscrollcommand=output_scroll.set)
        output_toolbar = ttk.Frame(output_box)
        output_toolbar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=6, pady=(0, 6))
        ttk.Button(output_toolbar, text="Copy output", command=self.copy_output).pack(side="left", padx=(0, 4))
        ttk.Button(output_toolbar, text="Clear output", command=lambda: self.output_text.delete("1.0", "end")).pack(side="left", padx=4)

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
        payload = {
            "last_repo_root": self.repo_var.get().strip(),
            "last_public_key_path": self.public_key_path_var.get().strip(),
            "last_test_host": self.test_host_var.get().strip() or "git@github.com",
        }
        try:
            with open(self.config_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
            self.config = payload
        except Exception as exc:
            self._append_output("config write", f"Failed to save SSHDock config: {exc}")

    def _meta_label(self, parent, row, col, title, variable):
        box = ttk.Frame(parent)
        box.grid(row=row, column=col, sticky="ew", padx=4, pady=2)
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text=f"{title}:").grid(row=0, column=0, sticky="w")
        ttk.Label(box, textvariable=variable).grid(row=0, column=1, sticky="w")

    def _set_status(self, message):
        self.status_var.set(message)

    def _append_output(self, title, text):
        from datetime import datetime
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        clean_text = (text or "").strip() or "(no output)"
        self.output_text.insert("end", f"\n[{stamp}] {title}\n{clean_text}\n")
        self.output_text.see("end")

    def _run(self, cmd, cwd=None):
        try:
            completed = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
            merged = (stdout + ("\n" if stdout and stderr else "") + stderr).strip()
            return {"code": completed.returncode, "stdout": stdout, "stderr": stderr, "text": merged}
        except FileNotFoundError:
            return {"code": 127, "stdout": "", "stderr": "command not found", "text": "command not found"}
        except Exception as exc:
            return {"code": 1, "stdout": "", "stderr": str(exc), "text": str(exc)}

    def choose_repo(self):
        chosen = filedialog.askdirectory(initialdir=self.repo_var.get() or os.path.expanduser("~"))
        if chosen:
            self.repo_var.set(chosen)
            self._save_config()
            self.refresh_all()

    def pick_public_key(self):
        chosen = filedialog.askopenfilename(initialdir=os.path.expanduser("~/.ssh"))
        if chosen:
            self.public_key_path_var.set(chosen)
            self._save_config()
            self._set_status("Selected public key path.")

    def refresh_all(self):
        self._save_config()
        self.check_agent()
        self.list_keys()
        self.check_remote_mode()

    def check_agent(self):
        sock = os.environ.get("SSH_AUTH_SOCK", "")
        if sock:
            self.sock_var.set(sock)
            self.agent_var.set("running")
            self._append_output("check agent", f"SSH_AUTH_SOCK present: {sock}")
            self._set_status("SSH agent socket detected.")
        else:
            self.sock_var.set("(missing)")
            self.agent_var.set("not visible")
            self._append_output("check agent", "SSH_AUTH_SOCK is not set in this session.")
            self._set_status("No SSH agent visible in this session.")

    def list_keys(self):
        res = self._run(["ssh-add", "-l"])
        if res["code"] == 0:
            lines = [line for line in res["text"].splitlines() if line.strip()]
            self.keys_var.set(f"{len(lines)} key(s) loaded")
        elif "The agent has no identities" in res["text"]:
            self.keys_var.set("0 keys loaded")
        else:
            self.keys_var.set("(unknown)")
        self._append_output("ssh-add -l", res["text"])
        self._set_status("Listed SSH agent identities.")

    def list_ssh_dir(self):
        ssh_dir = os.path.expanduser("~/.ssh")
        res = self._run(["ls", "-la", ssh_dir])
        self._append_output("ls -la ~/.ssh", res["text"])
        self._set_status("Listed ~/.ssh contents.")

    def show_public_key(self):
        path = os.path.expanduser(self.public_key_path_var.get().strip())
        if not path:
            messagebox.showwarning("SSHDock", "Choose a public key file first.")
            return
        if not os.path.isfile(path):
            messagebox.showerror("SSHDock", f"Public key file not found:\n{path}")
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                content = fh.read().strip()
            self.public_text.delete("1.0", "end")
            self.public_text.insert("1.0", content)
            self._save_config()
            self._append_output("show public key", f"Loaded public key from {path}")
            self._set_status("Public key loaded into preview.")
        except Exception as exc:
            self._append_output("show public key", f"Failed to read public key: {exc}")

    def copy_public_key(self):
        text = self.public_text.get("1.0", "end-1c")
        if not text.strip():
            messagebox.showinfo("SSHDock", "No public key text to copy.")
            return
        try:
            self.frame.clipboard_clear()
            self.frame.clipboard_append(text)
            self._set_status("Copied public key to clipboard.")
        except Exception as exc:
            self._append_output("copy public key", f"Clipboard copy failed: {exc}")

    def test_github_ssh(self):
        host = self.test_host_var.get().strip() or "git@github.com"
        self._save_config()
        cmd = ["ssh", "-T", host]
        res = self._run(cmd)
        self._append_output(" ".join(shlex.quote(part) for part in cmd), res["text"])
        if "successfully authenticated" in res["text"].lower():
            self._set_status("SSH test succeeded.")
        else:
            self._set_status("SSH test finished. Review output.")

    def _repo_root(self):
        return (self.repo_var.get() or "").strip()

    def check_remote_mode(self):
        root = self._repo_root()
        if not root or not os.path.isdir(root):
            self.remote_var.set("(repo not set)")
            self.remote_mode_var.set("(unknown)")
            self._append_output("check remote", "Choose a valid repo to inspect remote settings.")
            return
        res = self._run(["git", "remote", "get-url", "origin"], cwd=root)
        remote = (res["stdout"] or "").strip()
        if res["code"] != 0 or not remote:
            self.remote_var.set("(unavailable)")
            self.remote_mode_var.set("(unknown)")
            self._append_output("git remote get-url origin", res["text"])
            self._set_status("Could not read repo remote.")
            return
        self.remote_var.set(remote)
        if remote.startswith("git@") or remote.startswith("ssh://"):
            mode = "ssh"
        elif remote.startswith("https://"):
            mode = "https"
        else:
            mode = "other"
        self.remote_mode_var.set(mode)
        self._append_output("git remote get-url origin", remote)
        self._set_status(f"Remote mode detected: {mode}")

    def copy_remote(self):
        remote = self.remote_var.get().strip()
        if not remote or remote.startswith("("):
            messagebox.showinfo("SSHDock", "No remote URL to copy.")
            return
        try:
            self.frame.clipboard_clear()
            self.frame.clipboard_append(remote)
            self._set_status("Copied remote URL to clipboard.")
        except Exception as exc:
            self._append_output("copy remote", f"Clipboard copy failed: {exc}")

    def copy_output(self):
        text = self.output_text.get("1.0", "end-1c")
        if not text.strip():
            messagebox.showinfo("SSHDock", "No output text to copy.")
            return
        try:
            self.frame.clipboard_clear()
            self.frame.clipboard_append(text)
            self._set_status("Copied output to clipboard.")
        except Exception as exc:
            self._append_output("copy output", f"Clipboard copy failed: {exc}")

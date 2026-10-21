import json
import os
import queue
import subprocess
import threading
import time
import urllib.error
import urllib.request

from rich.console import Console

console = Console()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RUNTIME_FILE = os.path.join(os.path.expanduser("~"), ".clawd", "runtime.json")
AGENT_ID_FILE = os.path.join(SCRIPT_DIR, "clawd_agent_id.txt")
SESSION_ID = "greg-assistant"

STATE_MAP = {
    "listening": ("idle", "SessionStart"),
    "triggered": ("notification", "Notification"),
    "thinking": ("thinking", "UserPromptSubmit"),
    "response": ("working", "PreToolUse"),
    "done": ("attention", "Stop"),
    "timeout": ("error", "PostToolUseFailure"),
    "error": ("error", "PostToolUseFailure"),
}

HOLD_SECONDS = {"attention": 3.0, "error": 3.0}


class ClawdBridge:
    def __init__(self, on_close_callback=None):
        self.on_close_callback = on_close_callback
        self.closed = threading.Event()
        self.agent_id = self._load_agent_id()
        self.enabled = bool(self.agent_id)
        self._queue = queue.Queue()
        self._last_sent = None
        self._last_state = None
        self._last_time = 0.0
        self._warned = set()

        if not self.enabled:
            console.print(
                "[bold yellow]⚠️ Clawd : aucun Agent ID. Enregistre un agent dans Clawd "
                "(Settings → Agents), puis mets son ID dans la variable CLAWD_AGENT_ID "
                "ou dans le fichier clawd_agent_id.txt.[/bold yellow]"
            )
            return

        self._maybe_launch_clawd()
        threading.Thread(target=self._worker, daemon=True).start()
        console.print(f"[bold green]✅ Pont Clawd actif (agent : {self.agent_id})[/bold green]")

    def update_display(self, text, state="listening"):
        """Affiche l'état dans la console et l'envoie à Clawd avec le texte de la réponse."""
        if text:
            console.print(f"[dim]\\[Clawd:{state}] {text[:120]}[/dim]")
        if self.enabled:
            self._queue.put((state, text))

    def start(self):
        self.closed.wait()

    def close_app(self):
        self.shutdown()

    def shutdown(self):
        if self.closed.is_set():
            return
        self.closed.set()
        if self.enabled:
            self._post("idle", "SessionEnd", text="")
        if self.on_close_callback:
            try:
                self.on_close_callback()
            except Exception:
                pass

    @staticmethod
    def _load_agent_id():
        agent_id = os.environ.get("CLAWD_AGENT_ID", "").strip()
        if agent_id:
            return agent_id
        try:
            with open(AGENT_ID_FILE, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            return ""

    @staticmethod
    def _read_port():
        try:
            with open(RUNTIME_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("app") == "clawd-on-desk":
                return int(data["port"])
        except Exception:
            pass
        return None

    def _maybe_launch_clawd(self):
        if self._read_port() is not None:
            return
        cmd = os.environ.get("CLAWD_LAUNCH_CMD", "").strip()
        if not cmd:
            return
        try:
            subprocess.Popen(cmd, shell=True)
            for _ in range(40):
                if self._read_port() is not None:
                    return
                time.sleep(0.25)
        except Exception:
            pass

    def _warn_once(self, key, message):
        if key not in self._warned:
            self._warned.add(key)
            console.print(message)

    def _post(self, clawd_state, event, text=""):
        port = self._read_port()
        if port is None:
            return False
        
        payload_data = {
            "agent_id": self.agent_id,
            "session_id": SESSION_ID,
            "state": clawd_state,
            "event": event,
            "cwd": SCRIPT_DIR,
            "pid_chain": [os.getpid()],
        }
        if text:
            payload_data["message"] = text

        payload = json.dumps(payload_data).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/state",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status == 204:
                    return False
                self._warned.discard("offline")
                return True
        except Exception:
            pass
        return False

    def _worker(self):
        while not self.closed.is_set():
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            label, text_content = item
            mapped = STATE_MAP.get(label)
            if not mapped:
                continue
            clawd_state, event = mapped

            hold = HOLD_SECONDS.get(self._last_state, 0)
            if clawd_state == "idle" and hold:
                remaining = hold - (time.time() - self._last_time)
                while remaining > 0 and self._queue.empty() and not self.closed.is_set():
                    time.sleep(min(0.1, remaining))
                    remaining = hold - (time.time() - self._last_time)
                if not self._queue.empty():
                    continue

            if self._post(clawd_state, event, text=text_content):
                self._last_sent = (clawd_state, event)
                self._last_state = clawd_state
                self._last_time = time.time()
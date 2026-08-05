# models/audit/state_sync.py
import os
import subprocess
import threading
import time

class GitStateSync:
    """
    Den Engine v39.7 — Git-backed state persistence.

    Render's free tier has an ephemeral filesystem: every restart wipes the shadow
    ledger, the derivatives history and the learned lexicon. We watched 18 live shadow
    trades vanish in under two minutes. Git is used here as a free durable store —
    pull state on boot, push it back periodically.

    Guardrails, because the naive version breaks in production:

      CREDENTIALS  An SSH remote cannot authenticate from Render. If GITHUB_TOKEN is
                   present we rewrite the remote to HTTPS for pushes only. Without a
                   token sync DISABLES ITSELF and says so loudly, rather than failing
                   silently every hour and letting you believe data is safe.

      CONFLICTS    State files are machine-generated and always conflict on merge.
                   Pull uses -X theirs scoped to the data directory, and a failed pull
                   never blocks startup — a stale ledger beats a dead scanner.

      BLOAT        derivatives_history.jsonl grows ~25k rows/day forever. It is rotated
                   at MAX_JSONL_MB, keeping the newest half. GitHub rejects files over
                   100MB and we are not going to find that out in production.

      SAFETY       Only files under audit/ are ever staged. The sync can never commit
                   source changes, and every git call is timeout-bounded so a hung
                   network operation cannot wedge the thread.
    """

    STATE_FILES = [
        "models/audit/shadow_open.json",
        "models/audit/shadow_closed.json",
        "models/audit/derivatives_history.jsonl",
        "models/audit/learned_lexicon.json",
        "models/audit/news_pending.json",
        "models/audit/event_outcomes.json",
        "models/audit/event_calendar.json",
        "models/portfolio/active_positions.json",
        "models/portfolio/signal_cooldown.json",
        "models/portfolio/dispatched_signals.json",
    ]

    PUSH_INTERVAL = 900.0        # 15 min — caps the worst-case loss window
    GIT_TIMEOUT = 60
    MAX_JSONL_MB = 40.0

    _enabled = None
    _lock = threading.Lock()
    _last_push = 0.0

    # ------------------------------------------------------------------
    @classmethod
    def _repo_root(cls) -> str:
        return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    @classmethod
    def _git(cls, *args, timeout=None) -> tuple:
        try:
            r = subprocess.run(["git", *args], cwd=cls._repo_root(),
                               capture_output=True, text=True,
                               timeout=timeout or cls.GIT_TIMEOUT)
            return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
        except Exception as e:
            return 1, "", f"{type(e).__name__}: {e}"

    # ------------------------------------------------------------------
    @classmethod
    def enabled(cls) -> bool:
        if cls._enabled is not None:
            return cls._enabled
        token = os.getenv("GITHUB_TOKEN", "").strip()
        repo = os.getenv("GITHUB_REPO", "Siva0702/den").strip()
        code, _, _ = cls._git("rev-parse", "--git-dir", timeout=10)
        if code != 0:
            print("[sync] DISABLED — not a git repository", flush=True)
            cls._enabled = False
            return False
        if not token:
            print("[sync] DISABLED — GITHUB_TOKEN not set. State will NOT survive a "
                  "restart. Add a Personal Access Token (repo scope) as GITHUB_TOKEN "
                  "in the Render environment to enable persistence.", flush=True)
            cls._enabled = False
            return False

        # HTTPS remote with the token, used only by this sync.
        url = f"https://x-access-token:{token}@github.com/{repo}.git"
        cls._git("remote", "remove", "state-sync", timeout=10)
        code, _, err = cls._git("remote", "add", "state-sync", url, timeout=10)
        if code != 0:
            print(f"[sync] DISABLED — could not configure remote: {err}", flush=True)
            cls._enabled = False
            return False

        cls._git("config", "user.email", "den-engine@local", timeout=10)
        cls._git("config", "user.name", "Den Engine State Sync", timeout=10)
        print("[sync] ENABLED — state will persist across restarts via git", flush=True)
        cls._enabled = True
        return True

    # ------------------------------------------------------------------
    @classmethod
    def pull_on_startup(cls) -> bool:
        """
        Restore state written before the last restart. Never raises, never blocks the
        scanner: if this fails the engine starts with an empty ledger, which is the
        situation we already live with.
        """
        if not cls.enabled():
            return False
        print("[sync] pulling saved state...", flush=True)
        code, out, err = cls._git("pull", "--no-rebase", "-X", "theirs",
                                  "state-sync", "main", timeout=120)
        if code != 0:
            print(f"[sync] pull failed (continuing with local state): {err[:180]}", flush=True)
            return False

        restored = [f for f in cls.STATE_FILES
                    if os.path.exists(os.path.join(cls._repo_root(), f))]
        print(f"[sync] restored {len(restored)}/{len(cls.STATE_FILES)} state files", flush=True)
        return True

    # ------------------------------------------------------------------
    @classmethod
    def _rotate_large_files(cls):
        """Keep the newest half of any JSONL that has grown past the cap."""
        for rel in cls.STATE_FILES:
            if not rel.endswith(".jsonl"):
                continue
            path = os.path.join(cls._repo_root(), rel)
            if not os.path.exists(path):
                continue
            try:
                size_mb = os.path.getsize(path) / (1024 * 1024)
                if size_mb < cls.MAX_JSONL_MB:
                    continue
                with open(path, "r") as f:
                    lines = f.readlines()
                keep = lines[len(lines) // 2:]
                with open(path, "w") as f:
                    f.writelines(keep)
                print(f"[sync] rotated {rel}: {size_mb:.1f}MB -> kept newest {len(keep)} rows",
                      flush=True)
            except Exception as e:
                print(f"[sync] rotate failed for {rel}: {e}", flush=True)

    # ------------------------------------------------------------------
    @classmethod
    def push_state(cls, reason: str = "periodic") -> bool:
        if not cls.enabled():
            return False
        with cls._lock:
            cls._rotate_large_files()

            # Stage ONLY state files. A bug here must never commit source changes.
            staged = 0
            for rel in cls.STATE_FILES:
                if os.path.exists(os.path.join(cls._repo_root(), rel)):
                    code, _, _ = cls._git("add", "-f", rel, timeout=20)
                    if code == 0:
                        staged += 1
            if staged == 0:
                return False

            code, out, _ = cls._git("diff", "--cached", "--name-only", timeout=20)
            if code != 0 or not out:
                return False                      # nothing actually changed

            msg = f"state: {reason} @ {time.strftime('%Y-%m-%d %H:%M:%S')} UTC"
            code, _, err = cls._git("commit", "-m", msg, timeout=30)
            if code != 0:
                print(f"[sync] commit failed: {err[:160]}", flush=True)
                cls._git("reset", timeout=20)
                return False

            code, _, err = cls._git("push", "state-sync", "HEAD:main", timeout=90)
            if code != 0:
                # Remote moved on (you pushed code). Rebase onto it and retry once.
                cls._git("pull", "--rebase", "-X", "theirs", "state-sync", "main", timeout=120)
                code, _, err = cls._git("push", "state-sync", "HEAD:main", timeout=90)
                if code != 0:
                    print(f"[sync] push failed: {err[:160]}", flush=True)
                    return False

            cls._last_push = time.time()
            print(f"[sync] state pushed ({len(out.splitlines())} files, {reason})", flush=True)
            return True

    # ------------------------------------------------------------------
    @classmethod
    def sync_daemon(cls):
        """Background pusher. Sleeps first so the first scan can produce something."""
        if not cls.enabled():
            return
        time.sleep(300)
        while True:
            try:
                cls.push_state("periodic")
            except Exception as e:
                print(f"[sync] daemon error: {e}", flush=True)
            time.sleep(cls.PUSH_INTERVAL)

    @classmethod
    def status(cls) -> dict:
        return {
            "enabled": bool(cls._enabled),
            "last_push": (time.strftime('%H:%M:%S', time.localtime(cls._last_push))
                          if cls._last_push else "never"),
            "interval_min": cls.PUSH_INTERVAL / 60,
        }

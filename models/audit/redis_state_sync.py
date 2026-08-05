# models/audit/redis_state_sync.py
import json
import os
import requests
import threading
import time

class UpstashRedisStateSync:
    """
    Den Engine v39.8 — Upstash Redis State Persistence (100% Free, Zero Git Churn).

    Stores engine state files in Upstash Redis key-value store:
    - shadow_ledger.json, shadow_closed.json, engine_efficiency.json stored as JSON strings.
    - derivatives_history.jsonl stored using Redis RPUSH / LTRIM (capped at 100k rows).

    Uses Upstash REST API via standard `requests` — 0 pip dependencies required.

    Render Setup:
    Add environment variables in Render:
      UPSTASH_REDIS_REST_URL = https://your-db-name.upstash.io
      UPSTASH_REDIS_REST_TOKEN = your_access_token_here
    """

    STATE_FILES = {
        "models/audit/shadow_open.json": "den:shadow_open",
        "models/audit/shadow_closed.json": "den:shadow_closed",
        "models/audit/learned_lexicon.json": "den:learned_lexicon",
        "models/audit/news_pending.json": "den:news_pending",
        "models/audit/event_outcomes.json": "den:event_outcomes",
        "models/audit/event_calendar.json": "den:event_calendar",
        "models/portfolio/active_positions.json": "den:active_positions",
        "models/portfolio/signal_cooldown.json": "den:signal_cooldown",
        "models/portfolio/dispatched_signals.json": "den:dispatched_signals",
    }

    DERIVATIVES_KEY = "den:derivatives_history"
    PUSH_INTERVAL = 900.0  # 15 minutes
    MAX_DERIVATIVES_ROWS = 100000

    _enabled = None
    _lock = threading.Lock()
    _last_push = 0.0

    @classmethod
    def _repo_root(cls) -> str:
        return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    @classmethod
    def enabled(cls) -> bool:
        if cls._enabled is not None:
            return cls._enabled

        url = os.getenv("UPSTASH_REDIS_REST_URL", "").strip()
        token = os.getenv("UPSTASH_REDIS_REST_TOKEN", "").strip()

        if not url or not token:
            # Fallback check if user passed redis:// URL
            redis_url = os.getenv("UPSTASH_REDIS_URL", "").strip()
            if redis_url and "upstash.io" in redis_url:
                try:
                    # Parse redis://default:token@host:port -> https://host
                    user_pass, host_port = redis_url.replace("redis://", "").split("@")
                    token = user_pass.split(":")[-1]
                    host = host_port.split(":")[0]
                    url = f"https://{host}"
                    os.environ["UPSTASH_REDIS_REST_URL"] = url
                    os.environ["UPSTASH_REDIS_REST_TOKEN"] = token
                except Exception:
                    pass

        if not url or not token:
            print("[redis-sync] DISABLED — UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN not set. State is local-only.", flush=True)
            cls._enabled = False
            return False

        # Try POST first, fallback to GET if needed
        for attempt in range(2):
            ok, res = cls._redis_cmd(["PING"])
            if ok and (res == "PONG" or res == "pong" or str(res).upper() == "PONG"):
                print("[redis-sync] ENABLED — Upstash Redis state persistence active (0 git churn)", flush=True)
                cls._enabled = True
                return True
            time.sleep(1)

        print("[redis-sync] DISABLED — Redis PING failed after retries", flush=True)
        cls._enabled = False
        return False

    @classmethod
    def _redis_cmd(cls, cmd_list: list) -> tuple:
        url = os.getenv("UPSTASH_REDIS_REST_URL", "").strip()
        token = os.getenv("UPSTASH_REDIS_REST_TOKEN", "").strip()
        if not url or not token:
            return False, None

        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        
        # Method 1: POST command array JSON
        for attempt in range(2):
            try:
                r = requests.post(url, json=cmd_list, headers=headers, timeout=15)
                if r.status_code == 200:
                    res = r.json().get("result")
                    return True, res
            except Exception as e:
                pass

        # Method 2: GET URL path fallback (e.g. GET /PING or GET /GET/key)
        try:
            cmd_path = "/".join([str(x) for x in cmd_list])
            r = requests.get(f"{url}/{cmd_path}", headers={"Authorization": f"Bearer {token}"}, timeout=15)
            if r.status_code == 200:
                res = r.json().get("result")
                return True, res
        except Exception as e:
            print(f"[redis-sync] Redis cmd error {cmd_list[:2]}: {e}", flush=True)

        return False, None

    @classmethod
    def pull_on_startup(cls) -> bool:
        """Restores saved state files from Upstash Redis at scanner boot."""
        if not cls.enabled():
            return False

        print("[redis-sync] Pulling saved state from Upstash Redis...", flush=True)
        restored_count = 0

        # 1. Pull JSON state files
        for rel_path, redis_key in cls.STATE_FILES.items():
            abs_path = os.path.join(cls._repo_root(), rel_path)
            ok, val = cls._redis_cmd(["GET", redis_key])
            if ok and val:
                try:
                    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                    with open(abs_path, "w") as f:
                        f.write(val if isinstance(val, str) else json.dumps(val))
                    restored_count += 1
                except Exception as e:
                    print(f"[redis-sync] Error writing {rel_path}: {e}", flush=True)

        # 2. Pull derivatives_history.jsonl
        ok, rows = cls._redis_cmd(["LRANGE", cls.DERIVATIVES_KEY, "0", "-1"])
        if ok and isinstance(rows, list) and len(rows) > 0:
            rel_path = "models/audit/derivatives_history.jsonl"
            abs_path = os.path.join(cls._repo_root(), rel_path)
            try:
                os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                with open(abs_path, "w") as f:
                    for row in rows:
                        f.write(str(row) + "\n")
                restored_count += 1
                print(f"[redis-sync] Restored {len(rows)} derivatives history rows from Redis", flush=True)
            except Exception as e:
                print(f"[redis-sync] Error writing derivatives history: {e}", flush=True)

        print(f"[redis-sync] Restored {restored_count} state files from Upstash Redis", flush=True)
        return True

    @classmethod
    def push_state(cls, reason: str = "periodic") -> bool:
        """Pushes current local state files to Upstash Redis."""
        if not cls.enabled():
            return False

        with cls._lock:
            pushed_count = 0

            # 1. Push JSON state files (SET)
            for rel_path, redis_key in cls.STATE_FILES.items():
                abs_path = os.path.join(cls._repo_root(), rel_path)
                if os.path.exists(abs_path):
                    try:
                        with open(abs_path, "r") as f:
                            content = f.read()
                        if content.strip():
                            ok, _ = cls._redis_cmd(["SET", redis_key, content])
                            if ok:
                                pushed_count += 1
                    except Exception as e:
                        print(f"[redis-sync] Error pushing {rel_path}: {e}", flush=True)

            # 2. Push derivatives_history.jsonl (RPUSH + LTRIM)
            deriv_path = os.path.join(cls._repo_root(), "models/audit/derivatives_history.jsonl")
            if os.path.exists(deriv_path):
                try:
                    with open(deriv_path, "r") as f:
                        lines = [l.strip() for l in f if l.strip()]
                    if lines:
                        capped_rows = lines[-cls.MAX_DERIVATIVES_ROWS:]
                        ok, _ = cls._redis_cmd(["DEL", cls.DERIVATIVES_KEY])
                        if capped_rows:
                            for i in range(0, len(capped_rows), 500):
                                chunk = capped_rows[i:i+500]
                                cls._redis_cmd(["RPUSH", cls.DERIVATIVES_KEY, *chunk])
                        pushed_count += 1
                except Exception as e:
                    print(f"[redis-sync] Error pushing derivatives history: {e}", flush=True)

            cls._last_push = time.time()
            print(f"[redis-sync] Pushed state to Upstash Redis ({pushed_count} files, {reason})", flush=True)
            return True

    @classmethod
    def sync_daemon(cls):
        """Background pusher. Syncs state to Redis every 15 minutes."""
        if not cls.enabled():
            return
        time.sleep(120)
        while True:
            try:
                cls.push_state("periodic")
            except Exception as e:
                print(f"[redis-sync] Daemon error: {e}", flush=True)
            time.sleep(cls.PUSH_INTERVAL)

    @classmethod
    def status(cls) -> dict:
        return {
            "enabled": bool(cls._enabled),
            "last_push": (time.strftime('%H:%M:%S', time.localtime(cls._last_push)) if cls._last_push else "never"),
            "provider": "Upstash Redis (REST)",
        }

class UnifiedStateSync:
    """Unified persistence interface favoring Upstash Redis over Git."""
    
    @classmethod
    def enabled(cls) -> bool:
        from audit.state_sync import GitStateSync
        return UpstashRedisStateSync.enabled() or GitStateSync.enabled()

    @classmethod
    def pull_on_startup(cls):
        from audit.state_sync import GitStateSync
        if UpstashRedisStateSync.enabled():
            return UpstashRedisStateSync.pull_on_startup()
        return GitStateSync.pull_on_startup()

    @classmethod
    def push_state(cls, reason: str = "periodic"):
        from audit.state_sync import GitStateSync
        if UpstashRedisStateSync.enabled():
            return UpstashRedisStateSync.push_state(reason)
        return GitStateSync.push_state(reason)

    @classmethod
    def sync_daemon(cls):
        from audit.state_sync import GitStateSync
        if UpstashRedisStateSync.enabled():
            return UpstashRedisStateSync.sync_daemon()
        return GitStateSync.sync_daemon()

    @classmethod
    def status(cls) -> str:
        from audit.state_sync import GitStateSync
        if UpstashRedisStateSync.enabled():
            st = UpstashRedisStateSync.status()
            return f"ENABLED ({st['provider']} @ {st['last_push']})"
        elif GitStateSync.enabled():
            st = GitStateSync.status()
            return f"ENABLED (Git @ {st.get('last_push', 'never')})"
        else:
            return "DISABLED (No UPSTASH_REDIS_REST_URL / GITHUB_TOKEN set)"

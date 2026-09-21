"""The Daemon — the agent's continuous-presence mode.

Without the daemon, every `Agent()` call is a fresh life. With it,
the agent has:

- **Continuity**: it stays alive between user invocations
- **Proactivity**: it can trigger itself on cron, file events, webhooks
- **Background reflection**: it processes the profile periodically
- **HTTP endpoint**: external systems can POST tasks to it
- **Identity persistence**: a single identity across all runs

The daemon is intentionally simple: an asyncio loop that:

1. Loads identity + profile + skills
2. Starts the HTTP webhook server in a background thread
3. Starts the filesystem watcher (polling)
4. Starts the proactive engine (periodic reflection)
5. Waits for triggers and runs the agent

All triggers funnel through the same queue. The agent runs as a
single concurrency-1 worker (no parallel LLM calls — that would be
expensive and unpredictable). Triggers are processed in order.

Triggers can come from:
- HTTP POST to /trigger (e.g. CI/CD, GitHub webhooks)
- File events (created/modified in a watched dir)
- Cron (time-based)
- The proactive engine itself (it may decide to act)

This is not a multi-tenant service. It's a single-user daemon. The
HTTP server binds to 127.0.0.1 by default.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import signal
import time
import traceback
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from odc.config import load_config
from odc.identity import load_identity
from odc.observability.logs import log_event, get_logger

log = get_logger("odc.daemon")


class Trigger:
    """A unit of work that the daemon will process.

    - kind: "webhook" | "file" | "cron" | "proactive"
    - source: where the trigger came from (file path, URL, etc.)
    - task: the natural-language task to run
    - created: timestamp
    - meta: optional dict with kind-specific data
    """

    def __init__(self, kind: str, task: str, source: str = "", meta: dict | None = None):
        self.kind = kind
        self.task = task
        self.source = source
        self.meta = meta or {}
        self.created = time.time()
        self.id = hashlib.sha1(
            f"{kind}:{source}:{task}:{self.created}".encode()
        ).hexdigest()[:12]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "source": self.source,
            "task": self.task,
            "meta": self.meta,
            "created": self.created,
        }


class Daemon:
    """The main daemon. One instance, one loop, many triggers."""

    def __init__(
        self,
        data_dir: Path | None = None,
        host: str = "127.0.0.1",
        port: int = 8766,
        watch_dirs: list[str] | None = None,
        proactive_interval: int = 300,
        auto_approve: bool = False,
    ):
        self.config = load_config()
        if data_dir is not None:
            self.config.data_dir = data_dir
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        self.identity = load_identity(self.config.data_dir)
        self.host = host
        self.port = port
        self.watch_dirs = [Path(d) for d in (watch_dirs or [])]
        self.proactive_interval = proactive_interval
        self.auto_approve = auto_approve

        self.queue: asyncio.Queue[Trigger] = asyncio.Queue()
        self.running = True
        self.processed: deque[dict[str, Any]] = deque(maxlen=200)
        self.httpd: ThreadingHTTPServer | None = None
        self._loop_task: asyncio.Task | None = None
        self._watcher_task: asyncio.Task | None = None
        self._proactive_task: asyncio.Task | None = None
        # Track file mtimes for the watcher
        self._watch_state: dict[str, float] = {}

    # ------------------------------------------------------------------ public

    async def run(self) -> None:
        """Main entry. Starts subsystems and processes triggers forever."""
        log_event(
            log, 20, "daemon_start",
            name=self.identity.data.get("name"),
            host=self.host, port=self.port,
            watching=[str(d) for d in self.watch_dirs],
            proactive_interval_s=self.proactive_interval,
        )

        # Start HTTP server in a thread
        self._start_http_server()
        # Start background tasks
        self._watcher_task = asyncio.create_task(self._watcher_loop())
        self._proactive_task = asyncio.create_task(self._proactive_loop())
        # Trap signals for graceful shutdown
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._request_stop)
            except NotImplementedError:
                # Windows / non-supported platforms: just continue
                pass

        # Main worker loop
        self._loop_task = asyncio.create_task(self._worker_loop())
        try:
            await self._loop_task
        finally:
            await self._shutdown()

    def _request_stop(self) -> None:
        log_event(log, 20, "daemon_stop_requested")
        self.running = False

    async def _shutdown(self) -> None:
        log_event(log, 20, "daemon_shutdown")
        for t in (self._watcher_task, self._proactive_task):
            if t and not t.done():
                t.cancel()
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
        self.identity.note_event("daemon_shutdown")

    # ------------------------------------------------------------------ triggers

    def enqueue(self, trigger: Trigger) -> None:
        """Enqueue a trigger from a non-async context (HTTP handler)."""
        try:
            self.queue.put_nowait(trigger)
            log_event(
                log, 20, "trigger_enqueued",
                kind=trigger.kind, source=trigger.source,
                task=trigger.task[:200], id=trigger.id,
            )
        except Exception as e:
            log_event(log, 30, "trigger_enqueue_failed", error=str(e)[:200])

    # ------------------------------------------------------------------ worker

    async def _worker_loop(self) -> None:
        """Process triggers under OTP-style supervision. If a trigger
        handler crashes, the supervisor restarts it. If too many
        restarts happen in a short window, the supervisor gives up.
        """
        from odc.daemon.supervisor import (
            Supervisor,
            SupervisorConfig,
            SupervisorError,
        )
        import odc.daemon.supervisor as _sup_mod

        async def _worker_entry() -> None:
            while self.running:
                try:
                    trigger = await asyncio.wait_for(self.queue.get(), timeout=2.0)
                except asyncio.TimeoutError:
                    continue
                await self._process_trigger(trigger)

        sup = Supervisor(SupervisorConfig(
            strategy="one_for_one",
            max_restarts=5,
            period=60.0,
            watchdog_timeout=600.0,  # 10min per trigger is generous
            backoff_base=1.0,
            backoff_cap=30.0,
        ))
        _sup_mod._current_supervisor = sup
        try:
            await sup.run(_worker_entry)
        except SupervisorError as e:
            log_event(log, 30, "supervisor_gave_up", error=str(e))
            self.running = False
        finally:
            _sup_mod._current_supervisor = None

    async def _process_trigger(self, trigger: Trigger) -> None:
        log_event(
            log, 20, "trigger_start",
            kind=trigger.kind, id=trigger.id, task=trigger.task[:200],
        )
        t0 = time.perf_counter()
        try:
            from odc import Agent
            agent = Agent(config=self.config, auto_approve=self.auto_approve, interactive=False)
            run = await agent.run(trigger.task)
            report = run.result.report
            ok = run.result.handed_back_reason is None
            self.processed.append({
                "id": trigger.id,
                "kind": trigger.kind,
                "task": trigger.task[:200],
                "ok": ok,
                "turns": run.result.turns,
                "duration_ms": int((time.perf_counter() - t0) * 1000),
                "report_head": report[:300],
                "ts": time.time(),
                "thread_id": run.result.thread_id,
            })
            log_event(
                log, 20, "trigger_done",
                kind=trigger.kind, id=trigger.id, ok=ok,
                turns=run.result.turns,
                duration_ms=int((time.perf_counter() - t0) * 1000),
                thread_id=run.result.thread_id,
            )
        except Exception as e:
            tb = traceback.format_exc()[:500]
            log_event(log, 30, "trigger_failed", id=trigger.id, error=str(e)[:200], traceback=tb)
            self.processed.append({
                "id": trigger.id, "kind": trigger.kind, "task": trigger.task[:200],
                "ok": False, "error": str(e)[:200], "ts": time.time(),
            })

    # ------------------------------------------------------------------ watcher

    async def _watcher_loop(self) -> None:
        """Poll the watch_dirs every 10s. Enqueue triggers for new/changed files."""
        if not self.watch_dirs:
            return
        while self.running:
            try:
                self._scan_watch_dirs()
            except Exception as e:
                log_event(log, 30, "watcher_error", error=str(e)[:200])
            await asyncio.sleep(10)

    def _scan_watch_dirs(self) -> None:
        for d in self.watch_dirs:
            if not d.exists():
                continue
            for p in d.rglob("*"):
                if not p.is_file():
                    continue
                if any(part.startswith(".") for part in p.relative_to(d).parts):
                    continue
                try:
                    mtime = p.stat().st_mtime
                except OSError:
                    continue
                key = str(p)
                prev = self._watch_state.get(key)
                if prev is None:
                    self._watch_state[key] = mtime
                    # New file
                    self.enqueue(Trigger(
                        kind="file",
                        task=f"A new file appeared at {p}. Briefly summarize it.",
                        source=str(p),
                        meta={"event": "created"},
                    ))
                elif mtime > prev:
                    self._watch_state[key] = mtime
                    self.enqueue(Trigger(
                        kind="file",
                        task=f"The file at {p} was modified. Briefly summarize the change.",
                        source=str(p),
                        meta={"event": "modified"},
                    ))

    # ------------------------------------------------------------------ proactive

    async def _proactive_loop(self) -> None:
        """Periodically reflect: look at the profile, find a gap or pattern,
        and enqueue a self-improvement task. This is the 'proactive engine'."""
        if self.proactive_interval <= 0:
            return
        # Wait one interval before the first proactive run
        await asyncio.sleep(min(self.proactive_interval, 30))
        while self.running:
            try:
                self._maybe_act_proactively()
            except Exception as e:
                log_event(log, 30, "proactive_error", error=str(e)[:200])
            await asyncio.sleep(self.proactive_interval)

    def _maybe_act_proactively(self) -> None:
        """Decide if the daemon should act on its own."""
        profile_path = self.config.data_dir / "cognitive" / "profile.json"
        if not profile_path.exists():
            return
        prof = json.loads(profile_path.read_text(encoding="utf-8"))
        m = prof.get("meta_metrics", {})
        # Heuristic 1: if there are many recent failures, run self_assess
        recent = self.processed
        recent_failures = sum(1 for r in list(recent)[-10:] if not r.get("ok"))
        if recent_failures >= 3:
            self.enqueue(Trigger(
                kind="proactive",
                task=(
                    "Several recent tasks failed. Call cognitive.self_assess to "
                    "identify the most common failure pattern, and write a new "
                    "skill that targets it. Report what you wrote."
                ),
                source="proactive.failures",
                meta={"failures": recent_failures},
            ))
            return
        # Heuristic 2: if there are no recorded task patterns yet, run route on a generic task
        if not prof.get("task_patterns") and m.get("total_tasks", 0) == 0:
            self.enqueue(Trigger(
                kind="proactive",
                task="Call cognitive.think with note='daemon is alive' and kind='observation'.",
                source="proactive.heartbeat",
            ))

    # ------------------------------------------------------------------ HTTP

    def _start_http_server(self) -> None:
        daemon = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                # Silence stderr access log; we have our own logger
                pass

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/health":
                    self._json(200, {"ok": True, "name": daemon.identity.data.get("name")})
                elif self.path == "/identity":
                    self._json(200, daemon.identity.data)
                elif self.path == "/queue":
                    self._json(200, {"pending": daemon.queue.qsize()})
                elif self.path == "/processed":
                    self._json(200, {"items": list(daemon.processed)[-50:]})
                else:
                    self._json(404, {"error": "not found"})

            def do_POST(self) -> None:  # noqa: N802
                if self.path == "/trigger":
                    length = int(self.headers.get("Content-Length", "0") or "0")
                    body = self.rfile.read(length).decode("utf-8", errors="replace")
                    try:
                        data = json.loads(body) if body.strip() else {}
                    except Exception:
                        data = {}
                    task = (data.get("task") or "").strip()
                    if not task:
                        self._json(400, {"error": "missing 'task' in body"})
                        return
                    t = Trigger(
                        kind="webhook",
                        task=task,
                        source=self.headers.get("X-Source", "http"),
                        meta=data.get("meta", {}),
                    )
                    daemon.enqueue(t)
                    self._json(202, {"queued": True, "id": t.id, "task": task[:200]})
                elif self.path == "/rename":
                    length = int(self.headers.get("Content-Length", "0") or "0")
                    body = self.rfile.read(length).decode("utf-8", errors="replace")
                    try:
                        data = json.loads(body) if body.strip() else {}
                    except Exception:
                        data = {}
                    new_name = (data.get("name") or "").strip()
                    if not new_name:
                        self._json(400, {"error": "missing 'name'"})
                        return
                    daemon.identity.rename(new_name, reason=data.get("reason", ""))
                    self._json(200, {"renamed": True, "name": new_name})
                else:
                    self._json(404, {"error": "not found"})

            def _json(self, code: int, payload: dict) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        try:
            self.httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        except OSError as e:
            log_event(log, 30, "http_bind_failed", host=self.host, port=self.port, error=str(e))
            return
        import threading
        t = threading.Thread(target=self.httpd.serve_forever, name="odc-daemon-http", daemon=True)
        t.start()
        log_event(log, 20, "http_listening", host=self.host, port=self.port)


def serve(
    host: str = "127.0.0.1",
    port: int = 8766,
    watch_dirs: list[str] | None = None,
    proactive_interval: int = 300,
    data_dir: Path | None = None,
    auto_approve: bool = False,
) -> None:
    """Synchronous entry point for the CLI."""
    d = Daemon(
        data_dir=data_dir,
        host=host,
        port=port,
        watch_dirs=watch_dirs or [],
        proactive_interval=proactive_interval,
        auto_approve=auto_approve,
    )
    asyncio.run(d.run())

"""End-to-end harness: a real engine in replay mode, a headless Edge, and the checks' helpers.

Section 4 - Pages, Engine & Demo. TODO: P-37.

Nothing here touches a live engine or a real device: every engine this starts runs in its
own temporary folder (config, data/ and a link to the models), on its own port (8013 by
default), replaying a video file and a WAV file, with the simulated Arduino and with
spoken replies going nowhere ([speech_out] device = "none"). The camera is never opened
by name: the config names a camera that does not exist and turns the "any camera"
fallback off, so a bad --source can't pick up a webcam.

See tests/e2e/README.md for how to run it.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

REPO = Path(__file__).resolve().parents[2]
E2E = Path(__file__).resolve().parent
PORT = int(os.environ.get("ATTUNE_E2E_PORT", "8013"))
WINDOWS = sys.platform == "win32"

# Model files the engine needs for a meaningful run (captions + faces).
REQUIRED_MODELS = (
    "nemotron/encoder.int8.onnx",
    "nemotron/decoder.int8.onnx",
    "nemotron/joiner.int8.onnx",
    "nemotron/tokens.txt",
    "faces/buffalo_l/det_10g.onnx",
    "faces/buffalo_l/w600k_r50.onnx",
)


# ---------------------------------------------------------------------------- discovery
def out_root() -> Path:
    """Where runs keep their temporary folders, logs and screenshots."""
    base = os.environ.get("ATTUNE_E2E_OUT")
    if base:
        return Path(base)
    return Path(tempfile.gettempdir()) / "attune_eval" / "qa"


def main_checkout() -> Path:
    """The main checkout when this is a git worktree (its sibling folders hold shared tools)."""
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        common = Path(out.stdout.strip())
        if out.returncode == 0 and out.stdout.strip():
            if not common.is_absolute():
                common = (REPO / common).resolve()
            return common.parent
    except (OSError, subprocess.SubprocessError):
        pass
    return REPO


def models_dir() -> Path:
    return Path(os.environ.get("ATTUNE_MODELS_DIR") or (REPO / "models"))


def missing_models() -> list[str]:
    root = models_dir()
    return [m for m in REQUIRED_MODELS if not (root / m).exists()]


def find_node() -> str | None:
    return os.environ.get("ATTUNE_NODE") or shutil.which("node")


def find_playwright() -> Path | None:
    """A node_modules folder that holds playwright-core."""
    candidates = []
    if os.environ.get("ATTUNE_PLAYWRIGHT_DIR"):
        candidates.append(Path(os.environ["ATTUNE_PLAYWRIGHT_DIR"]))
    main = main_checkout()
    candidates += [
        E2E / "node_modules",
        REPO / "node_modules",
        REPO.parent / "attune-video" / "render" / "node_modules",
        main.parent / "attune-video" / "render" / "node_modules",
    ]
    for c in candidates:
        if (c / "playwright-core" / "package.json").is_file():
            return c
    return None


def find_edge() -> str | None:
    """Microsoft Edge, which playwright-core drives with channel 'msedge'."""
    paths = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/opt/microsoft/msedge/msedge",
    ]
    for p in paths:
        if Path(p).is_file():
            return p
    return shutil.which("microsoft-edge") or shutil.which("msedge")


def find_video() -> Path | None:
    """A film reel with people in it (or the live clip)."""
    if os.environ.get("ATTUNE_E2E_VIDEO"):
        p = Path(os.environ["ATTUNE_E2E_VIDEO"])
        return p if p.is_file() else None
    for root in (
        REPO / "data" / "reels" / "film",
        main_checkout() / "data" / "reels" / "film",
    ):
        for name in ("cafe_friends.mp4", "dinner_group.mp4", "mom_talk.mp4"):
            if (root / name).is_file():
                return root / name
    clip = Path(tempfile.gettempdir()) / "liveclip1" / "clip.mp4"
    return clip if clip.is_file() else None


def reels_dir() -> Path | None:
    for root in (REPO / "data" / "reels", main_checkout() / "data" / "reels"):
        if (root / "film").is_dir():
            return root
    return None


def prerequisites(need_browser: bool = True, need_engine: bool = True) -> list[str]:
    """Reasons the suite can't run here (empty = ready)."""
    reasons = []
    if need_browser:
        if not find_node():
            reasons.append("node is not installed")
        if not find_playwright():
            reasons.append("playwright-core not found (set ATTUNE_PLAYWRIGHT_DIR)")
        if not find_edge():
            reasons.append("Microsoft Edge not found")
    if need_engine:
        missing = missing_models()
        if missing:
            reasons.append(
                f"models missing in {models_dir()}: {', '.join(missing[:3])}"
            )
        if not find_video():
            reasons.append("no film reel or clip (set ATTUNE_E2E_VIDEO)")
    return reasons


# ---------------------------------------------------------------------------- GPU lock
class GpuLock:
    """The team's shared GPU lock: a folder that exists while one agent's engine runs.

    `mkdir` succeeding means we hold it; we write owner.txt with our name and the time.
    Waits (polling) while someone else holds it. ATTUNE_E2E_NO_LOCK=1 skips it.
    """

    def __init__(self, what: str, path: str | None = None, wait_s: float = 3600.0):
        default = Path(tempfile.gettempdir()) / "attune_gpu.lock"
        self.path = Path(path or os.environ.get("ATTUNE_GPU_LOCK") or default)
        self.what = what
        self.wait_s = wait_s
        self.held = False
        self.since = 0.0

    @property
    def enabled(self) -> bool:
        return os.environ.get("ATTUNE_E2E_NO_LOCK", "") not in ("1", "true", "yes")

    def acquire(self) -> None:
        if not self.enabled or self.held:
            return
        end = time.monotonic() + self.wait_s
        announced = False
        while True:
            try:
                self.path.mkdir()
                break
            except FileExistsError:
                if not announced:
                    owner = self.owner()
                    print(f"[e2e] GPU lock held by {owner!r}; waiting", flush=True)
                    announced = True
                if time.monotonic() > end:
                    raise TimeoutError(f"GPU lock still held after {self.wait_s:.0f} s")
                time.sleep(20)
        stamp = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
        (self.path / "owner.txt").write_text(
            f"QA {stamp} {self.what}\n", encoding="utf-8"
        )
        self.held = True
        self.since = time.monotonic()
        print(f"[e2e] GPU lock taken for {self.what}", flush=True)

    def owner(self) -> str:
        try:
            return (self.path / "owner.txt").read_text(encoding="utf-8").strip()
        except OSError:
            return "?"

    def release(self) -> None:
        if not self.held:
            return
        shutil.rmtree(self.path, ignore_errors=True)
        self.held = False
        print(
            f"[e2e] GPU lock released after {time.monotonic() - self.since:.0f} s",
            flush=True,
        )

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


# ---------------------------------------------------------------------------- run folder
def _link_dir(link: Path, target: Path) -> None:
    """A directory link that needs no admin rights (a junction on Windows)."""
    if WINDOWS:
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def _unlink_dir(link: Path) -> None:
    """Remove a directory link, never what it points at."""
    if not os.path.lexists(link):
        return
    if WINDOWS:
        os.rmdir(link)  # removes the junction itself
    else:
        link.unlink()


BASE_CONFIG: dict[str, dict[str, Any]] = {
    "engine": {"host": "127.0.0.1", "data_dir": "data"},
    # Never a real webcam: no such camera, and no "any camera" fallback.
    "vision": {
        "camera_name": "attune-e2e-no-such-camera",
        "camera_fallback_any": False,
    },
    "hardware": {"simulate": True},
    # Replies run through the voices and events but are never played aloud, and never
    # sent to ElevenLabs (text must not leave the laptop in a test).
    "speech_out": {"device": "none", "elevenlabs": False},
    # Film reels carry unrelated audio: don't insist on lips in time with it.
    "fusion": {"require_sync": False},
}


def toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    raise TypeError(f"can't write {v!r} to TOML")


def to_toml(config: dict[str, dict[str, Any]]) -> str:
    lines = []
    for table, values in config.items():
        lines.append(f"[{table}]")
        for k, v in values.items():
            lines.append(f"{k} = {toml_value(v)}")
        lines.append("")
    return "\n".join(lines)


def merge(base: dict, over: dict) -> dict:
    out = {k: dict(v) for k, v in base.items()}
    for table, values in over.items():
        out.setdefault(table, {}).update(values)
    return out


class RunRoot:
    """A temporary engine working folder: config/, data/, and links to models and reels."""

    def __init__(
        self, name: str, config: dict | None = None, raw_toml: str | None = None
    ):
        stamp = dt.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        self.path = out_root() / f"run-{stamp}-{name}"
        n = 1
        while (
            self.path.exists()
        ):  # two engines of the same name in one second: never share
            n += 1
            self.path = out_root() / f"run-{stamp}-{name}-{n}"
        self.path.mkdir(parents=True)
        (self.path / "config").mkdir(exist_ok=True)
        (self.path / "data").mkdir(exist_ok=True)
        self.config_path = self.path / "config" / "attune.toml"
        text = (
            raw_toml
            if raw_toml is not None
            else to_toml(merge(BASE_CONFIG, config or {}))
        )
        self.config_path.write_text(text, encoding="utf-8")
        self.links = []
        models = models_dir()
        if models.is_dir():
            _link_dir(self.path / "models", models.resolve())
            self.links.append(self.path / "models")
        reels = reels_dir()
        if reels is not None:
            _link_dir(self.path / "data" / "reels", reels.resolve())
            self.links.append(self.path / "data" / "reels")

    @property
    def data(self) -> Path:
        return self.path / "data"

    def data_files(self) -> dict[str, int]:
        """Every file under data/ (not the linked reels): relative path -> size."""
        out = {}
        for root, dirs, files in os.walk(self.data):
            dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
            if Path(root) == self.data:
                dirs[:] = [d for d in dirs if d != "reels"]
            for f in files:
                p = Path(root) / f
                with contextlib.suppress(OSError):
                    out[p.relative_to(self.data).as_posix()] = p.stat().st_size
        return out

    def cleanup(self, keep_logs: bool = True) -> None:
        for link in self.links:
            with contextlib.suppress(OSError):
                _unlink_dir(link)
        self.links = []
        if not keep_logs:
            shutil.rmtree(self.path, ignore_errors=True)
            return
        # keep the logs, drop the data (history, session logs)
        shutil.rmtree(self.data, ignore_errors=True)


# ---------------------------------------------------------------------------- engine
def port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) != 0


def python_exe() -> str:
    return os.environ.get("ATTUNE_PYTHON") or sys.executable


@dataclass
class Engine:
    """`python -m attune` in replay mode, as a child process with its own log file."""

    root: RunRoot
    source: str | None = None
    audio: str | None = None
    repeat_audio: float = 0.0
    audio_delay: float = 2.0
    port: int = PORT
    extra: list[str] = field(default_factory=list)
    no_mic: bool = False
    env: dict[str, str] = field(default_factory=dict)
    proc: subprocess.Popen | None = None
    starts: int = 0
    remote_seen: dict[str, float] = field(
        default_factory=dict
    )  # "ip:port" -> first seen
    _watch: threading.Thread | None = None
    _watch_wanted: bool = False
    _watch_proc: subprocess.Popen | None = None  # the engine run being watched
    net_samples: int = (
        0  # looks the watcher managed (a look can time out on a busy laptop)
    )
    net_errors: int = 0
    net_since: float = 0.0  # monotonic time the watching started

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def ws_url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/ws"

    @property
    def log_path(self) -> Path:
        return self.root.path / f"engine-{self.starts}.log"

    def args(self) -> list[str]:
        a = [python_exe(), "-m", "attune", "--config", str(self.root.config_path)]
        a += ["--no-browser", "--port", str(self.port), "--simulate-hardware"]
        if self.source is not None:
            a += ["--source", str(self.source)]
        if self.audio:
            a += [
                "--audio-file",
                str(self.audio),
                "--audio-delay",
                str(self.audio_delay),
            ]
            if self.repeat_audio:
                a += ["--repeat-audio", str(self.repeat_audio)]
        if self.no_mic:
            a.append("--no-mic")
        return a + list(self.extra)

    def start(self, wait: bool = True, timeout: float = 180.0) -> Engine:
        if self.proc and self.proc.poll() is None:
            return self
        if not port_free(self.port):
            raise RuntimeError(
                f"port {self.port} is already in use; stop that engine first"
            )
        self.starts += 1
        env = {**os.environ, **self.env}
        env["PYTHONPATH"] = (
            str(REPO / "engine") + os.pathsep + env.get("PYTHONPATH", "")
        )
        env.setdefault("ATTUNE_LOG", "INFO")
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        # never talk to ElevenLabs from a test, even if a key is in the environment
        env.pop("ELEVENLABS_API_KEY", None)
        env.pop("ELEVENLABS_VOICE_ID", None)
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if WINDOWS else 0
        self._log = open(self.log_path, "w", encoding="utf-8", errors="replace")  # noqa: SIM115
        self.proc = subprocess.Popen(
            self.args(),
            cwd=self.root.path,
            env=env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
            start_new_session=not WINDOWS,
        )
        if wait:
            self.wait_ready(timeout)
        if self._watch_wanted:
            self.watch_network()
        return self

    def wait_ready(self, timeout: float = 180.0) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"engine exited ({self.proc.returncode}); see {self.log_path}"
                )
            log = self.log()
            up = "Attune is running" in log or "Running without" in log
            if up and self.http_status("/lens/") == 200:
                return
            time.sleep(0.5)
        raise TimeoutError(f"engine not ready in {timeout:.0f} s; see {self.log_path}")

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def net_watch_summary(self, every: float = 10.0) -> dict:
        """How well the network watcher covered the engine's run (for the checks)."""
        ran = time.monotonic() - self.net_since if self.net_since else 0.0
        return {
            "looks": self.net_samples,
            "failed_looks": self.net_errors,
            "watched_s": round(ran),
            # at least a third of the expected looks, and never fewer than 3
            "enough": self.net_samples >= max(3, int(ran / every / 3)),
        }

    def server_pid(self) -> int | None:
        """The process that really runs the engine: the one listening on its port.

        A venv's python.exe on Windows is a small launcher that starts the base interpreter
        as a child, so `proc.pid` is not the process to measure."""
        if not WINDOWS:
            return self.proc.pid if self.proc else None
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    f"(Get-NetTCPConnection -LocalPort {self.port} -State Listen "
                    "-ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        ).stdout.strip()
        return int(out) if out.isdigit() else None

    def watch_network(self, every: float = 10.0) -> None:
        """Note every outside address the engine talks to while it runs (Windows).

        A connection can be short (a telemetry upload lasts about a minute), so one look at
        the end is not enough."""
        self._watch_wanted = True  # start() watches again after a restart
        proc = self.proc
        if not WINDOWS or proc is None or self._watch_proc is proc:
            return
        self._watch_proc = proc
        t0 = time.monotonic()
        self.net_since = self.net_since or t0

        def run() -> None:
            pid = None
            while self.proc is proc and proc.poll() is None:  # this run of the engine
                try:  # a look that fails (busy laptop) must not end the watching
                    pid = pid or self.server_pid()
                    if pid:
                        for c in remote_connections(pid):
                            addr = str(c.get("RemoteAddress"))
                            if addr not in LOOPBACK_ADDRS:
                                key = f"{addr}:{c.get('RemotePort')}"
                                self.remote_seen.setdefault(
                                    key, round(time.monotonic() - t0, 1)
                                )
                        self.net_samples += 1
                except Exception:  # noqa: BLE001 - counted and reported by the checks
                    self.net_errors += 1
                time.sleep(every)

        self._watch = threading.Thread(target=run, name="e2e-net-watch", daemon=True)
        self._watch.start()

    def stop(self, timeout: float = 20.0) -> int | None:
        """Ask for a clean stop (Ctrl+Break / SIGTERM), then kill if it hangs."""
        proc = self.proc
        if proc is None:
            return None
        if proc.poll() is None:
            try:
                if WINDOWS:
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    os.killpg(proc.pid, signal.SIGTERM)
            except OSError:
                pass
            try:
                proc.wait(timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(5)
        with contextlib.suppress(Exception):
            self._log.close()
        # wait for the port to be released
        end = time.monotonic() + 10
        while not port_free(self.port) and time.monotonic() < end:
            time.sleep(0.2)
        return proc.returncode

    def restart(self, wait: bool = True) -> Engine:
        self.stop()
        return self.start(wait=wait)

    # ---- logs and HTTP
    def log(self) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def wait_log(self, pattern: str, timeout: float = 30.0) -> re.Match | None:
        rx = re.compile(pattern)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            m = rx.search(self.log())
            if m:
                return m
            time.sleep(0.3)
        return None

    def http(self, path: str, method: str = "GET", body: Any = None, headers=None):
        """(status, bytes) for a request; never raises for HTTP errors."""
        data = None
        hdrs = dict(headers or {})
        if body is not None:
            data = json.dumps(body).encode()
            hdrs.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(
            self.base + path, data=data, method=method, headers=hdrs
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except (urllib.error.URLError, OSError) as e:
            return 0, str(e).encode()

    def http_status(self, path: str) -> int:
        return self.http(path)[0]

    def json(self, path: str, method: str = "GET", body: Any = None) -> Any:
        status, raw = self.http(path, method, body)
        try:
            return json.loads(raw)
        except ValueError:
            return {"status": status, "raw": raw[:200].decode(errors="replace")}

    def sim(self) -> dict:
        return self.json("/api/sim/state")

    def touch(self, gesture: str) -> dict:
        return self.json("/api/sim/touch", "POST", {"gesture": gesture})

    def sim_control(self, **kw: Any) -> dict:
        return self.json("/api/sim/control", "POST", kw)


# ---------------------------------------------------------------------------- WebSocket pages
class WsPage:
    """A scripted page: says hello with a role and records every message it gets."""

    def __init__(self, url: str, role: str, frames: bool = False, name: str = ""):
        from websockets.sync.client import connect

        self.role, self.frames, self.name = role, frames, name or role
        self.msgs: list[dict] = []
        self.frame_times: list[float] = []
        self.lock = threading.Lock()
        self.ws = connect(url, max_size=8 * 1024 * 1024, open_timeout=10)
        self.ws.send(json.dumps({"type": "hello", "role": role, "frames": frames}))
        self.closed = False
        self.thread = threading.Thread(
            target=self._read, daemon=True, name=f"ws-{self.name}"
        )
        self.thread.start()

    def _read(self) -> None:
        with contextlib.suppress(Exception):  # the socket closed
            for m in self.ws:
                now = time.monotonic()
                with self.lock:
                    if isinstance(m, bytes):
                        self.frame_times.append(now)
                    else:
                        try:
                            msg = json.loads(m)
                        except ValueError:
                            continue
                        msg["_rx"] = now
                        self.msgs.append(msg)
        self.closed = True

    def send(self, name: str, args: dict | None = None) -> None:
        self.ws.send(json.dumps({"type": "command", "name": name, "args": args or {}}))

    def of(self, kind: str, since: float = 0.0) -> list[dict]:
        with self.lock:
            return [m for m in self.msgs if m.get("type") == kind and m["_rx"] >= since]

    def frames_since(self, since: float) -> int:
        with self.lock:
            return sum(1 for t in self.frame_times if t >= since)

    def wait(
        self, kind: str, pred=lambda m: True, timeout: float = 10.0, since: float = 0.0
    ):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for m in self.of(kind, since):
                if pred(m):
                    return m
            time.sleep(0.05)
        return None

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.ws.close()


@contextlib.contextmanager
def ws_pages(url: str, *specs: tuple[str, bool]) -> Iterator[list[WsPage]]:
    pages = [WsPage(url, role, frames) for role, frames in specs]
    try:
        yield pages
    finally:
        for p in pages:
            p.close()


# ---------------------------------------------------------------------------- small helpers
def wait_until(fn, timeout: float = 10.0, every: float = 0.25):
    """fn() until it returns something truthy (returned), or None after `timeout`."""
    end = time.monotonic() + timeout
    while True:
        try:
            got = fn()
        except Exception:  # noqa: BLE001 - not ready yet
            got = None
        if got:
            return got
        if time.monotonic() >= end:
            return None
        time.sleep(every)


suppress = contextlib.suppress


def _reader(proc: subprocess.Popen) -> Any:
    """A background reader of a script's stdout lines (so reads can time out)."""
    import queue

    q = getattr(proc, "_lines", None)
    if q is None:
        q = queue.Queue()

        def pump() -> None:
            for line in proc.stdout:
                q.put(line.rstrip("\n"))
            q.put(None)

        threading.Thread(target=pump, daemon=True).start()
        proc._lines = q  # type: ignore[attr-defined]
    return q


def read_line(proc: subprocess.Popen, want: str, timeout: float) -> str | None:
    """Wait for a stdout line starting with `want`."""
    import queue

    q = _reader(proc)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            line = q.get(timeout=max(0.05, end - time.monotonic()))
        except queue.Empty:
            return None
        if line is None:
            return None
        if line.startswith(want):
            return line
    return None


def read_json(proc: subprocess.Popen, timeout: float) -> dict:
    """The script's final JSON line."""
    import queue

    q = _reader(proc)
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        try:
            line = q.get(timeout=max(0.05, end - time.monotonic()))
        except queue.Empty:
            break
        if line is None:
            break
        if line.startswith("{"):
            try:
                last = json.loads(line)
                break
            except ValueError:
                continue
    if last is None:
        return {"ok": False, "error": "no result line", "stderr": drain(proc)}
    return last


def drain(proc: subprocess.Popen) -> str:
    try:
        proc.wait(2)
    except subprocess.TimeoutExpired:
        proc.kill()
    try:
        return (proc.stderr.read() or "")[-2000:]
    except (OSError, ValueError):
        return ""


# ---------------------------------------------------------------------------- browser scripts
def run_node(script: str, args: dict, timeout: float = 600.0) -> dict:
    """Run tests/e2e/<script> with a JSON argument; it prints one JSON result on its last line."""
    node, pw = find_node(), find_playwright()
    if not node or not pw:
        raise RuntimeError("node or playwright-core missing")
    env = {**os.environ, "NODE_PATH": str(pw)}
    proc = subprocess.run(
        [node, str(E2E / script), json.dumps(args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
        cwd=E2E,
        check=False,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    for ln in reversed(lines):
        if ln.startswith("{") and ln.endswith("}"):
            try:
                out = json.loads(ln)
                out.setdefault("_stderr", proc.stderr[-4000:])
                return out
            except ValueError:
                continue
    return {
        "ok": False,
        "error": f"{script} printed no result (exit {proc.returncode})",
        "_stdout": proc.stdout[-4000:],
        "_stderr": proc.stderr[-4000:],
    }


def start_node(script: str, args: dict) -> subprocess.Popen:
    """Start a long browser script that talks over stdin/stdout lines."""
    node, pw = find_node(), find_playwright()
    env = {**os.environ, "NODE_PATH": str(pw)}
    return subprocess.Popen(
        [node, str(E2E / script), json.dumps(args)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=E2E,
        bufsize=1,
    )


# ---------------------------------------------------------------------------- speech fixtures
def sapi_available() -> bool:
    return WINDOWS and shutil.which("powershell") is not None


def make_speech_wav(
    path: Path, sentences: list[str], voice: str = "David", gap_ms: int = 1400
):
    """Windows SAPI speech with exact known text, 16 kHz mono 16-bit, a pause between lines."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ssml_body = "".join(
        f'<s>{_xml(s)}</s><break time="{gap_ms}ms"/>' for s in sentences
    )
    ssml = (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">'
        f'<break time="600ms"/>{ssml_body}</speak>'
    )
    ssml_file = path.with_suffix(".ssml")
    ssml_file.write_text(ssml, encoding="utf-8")
    script = f"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$v = $s.GetInstalledVoices() | Where-Object {{ $_.VoiceInfo.Name -like '*{voice}*' }} | Select-Object -First 1
if ($v) {{ $s.SelectVoice($v.VoiceInfo.Name) }}
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$s.SetOutputToWaveFile('{path}', $fmt)
$s.SpeakSsml([IO.File]::ReadAllText('{ssml_file}'))
$s.Dispose()
"""
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        check=True,
        capture_output=True,
        timeout=120,
    )
    ssml_file.unlink(missing_ok=True)
    (path.with_suffix(".txt")).write_text("\n".join(sentences) + "\n", encoding="utf-8")
    return path


def _xml(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# Known-text scripts: short, distinct sentences with words Nemotron knows well.
SCRIPTS = {
    "david_meeting": (
        "David",
        [
            "Good morning everyone, thanks for coming in today.",
            "The train to the city leaves at nine thirty.",
            "Please bring the blue folder to the kitchen.",
            "We can meet by the front entrance after lunch.",
            "My sister is visiting us next weekend.",
            "The library closes early on Sunday evening.",
        ],
    ),
    "zira_cafe": (
        "Zira",
        [
            "Could I have a large coffee with milk please.",
            "The weather is lovely outside this afternoon.",
            "I left my umbrella on the bus this morning.",
            "Let us take a short walk around the park.",
        ],
    ),
}


def speech_fixture(name: str) -> tuple[Path, list[str]] | None:
    """(wav, sentences) for a known-text script, generated once with Windows SAPI."""
    folder = out_root() / "wavs"
    wav = folder / f"{name}.wav"
    voice, sentences = SCRIPTS[name]
    if not wav.is_file():
        if not sapi_available():
            return None
        make_speech_wav(wav, sentences, voice)
    return wav, sentences


def tone_fixture(kind: str, cycles: int = 6) -> Path:
    """T3 / T4 alarm WAVs from scripts/make_test_tones.py (never played aloud)."""
    import importlib.util

    wav = out_root() / "wavs" / f"{kind.lower()}_{cycles}.wav"
    if not wav.is_file():
        spec = importlib.util.spec_from_file_location(
            "tones", REPO / "scripts" / "make_test_tones.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.write_tone(wav, kind, cycles=cycles)
    return wav


# ---------------------------------------------------------------------------- text scoring
def words(text: str) -> list[str]:
    text = text.lower().replace("’", "'")
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    return [w.strip("'") for w in text.split() if w.strip("'")]


def edit_distance(a: list[str], b: list[str]) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, y in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y))
        prev = cur
    return prev[-1]


def wer(ref: str, hyp: str) -> float:
    r = words(ref)
    return edit_distance(r, words(hyp)) / max(1, len(r))


def align_captions(sentences: list[str], captions: list[str]) -> dict:
    """Match each final caption to the reference sentence it covers best.

    Returns order (sentence index per caption), missing sentences, duplicated sentences
    and the whole-text WER, so callers can check "in order, none missing, no duplicates".
    """
    matches = []
    for cap in captions:
        cw = set(words(cap))
        best, score = None, 0.0
        for i, s in enumerate(sentences):
            sw = words(s)
            overlap = sum(1 for w in sw if w in cw) / max(1, len(sw))
            if overlap > score:
                best, score = i, overlap
        matches.append((best if score >= 0.5 else None, round(score, 2)))
    order = [m for m, _ in matches if m is not None]
    seen: dict[int, int] = {}
    for i in order:
        seen[i] = seen.get(i, 0) + 1
    return {
        "matches": matches,
        "in_order": order == sorted(order),
        "missing": [i for i in range(len(sentences)) if i not in seen],
        "duplicates": [i for i, n in seen.items() if n > 1],
        "unmatched": [captions[k] for k, (m, _) in enumerate(matches) if m is None],
        "wer": round(wer(" ".join(sentences), " ".join(captions)), 3),
    }


# ---------------------------------------------------------------------------- resources
def process_stats(pid: int) -> dict:
    """Working set (MB) and total CPU seconds of a process (Windows via PowerShell)."""
    if WINDOWS:
        cmd = (
            f"$p = Get-Process -Id {pid} -ErrorAction SilentlyContinue; "
            "if ($p) { '{0} {1} {2} {3} {4}' -f $p.WorkingSet64, $p.PrivateMemorySize64, "
            "$p.TotalProcessorTime.TotalSeconds, $p.Threads.Count, $p.HandleCount }"
        )
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        ).stdout.split()
        if len(out) == 5:
            return {
                "rss_mb": round(int(out[0]) / 2**20, 1),
                "private_mb": round(int(out[1]) / 2**20, 1),
                "cpu_s": float(out[2].replace(",", ".")),
                "threads": int(out[3]),
                "handles": int(out[4]),
            }
        return {}
    try:
        with open(f"/proc/{pid}/status") as fh:
            rss = next(int(ln.split()[1]) for ln in fh if ln.startswith("VmRSS"))
        return {"rss_mb": round(rss / 1024, 1)}
    except (OSError, StopIteration):
        return {}


def gpu_used_mb() -> float | None:
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        used, _util = out.stdout.strip().splitlines()[0].split(",")
        return float(used)
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def gpu_process_mb(pid: int) -> float | None:
    """Dedicated GPU memory of one process (Windows "GPU Process Memory" counter).

    nvidia-smi can't split memory per process under WDDM, and the whole-GPU figure moves
    with every other engine on the laptop."""
    if not WINDOWS or not pid:
        return None
    cmd = (
        f"(Get-Counter '\\GPU Process Memory(pid_{int(pid)}_*)\\Dedicated Usage' "
        "-ErrorAction SilentlyContinue).CounterSamples | "
        "Measure-Object CookedValue -Sum | ForEach-Object { $_.Sum }"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        ).stdout.strip()
        return round(float(out) / 2**20, 1) if out else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


LOOPBACK_ADDRS = {"127.0.0.1", "::1", "0.0.0.0", "::"}


def dns_names(addresses: list[str]) -> dict[str, str]:
    """Host names the laptop looked up for these IPs (the DNS cache), for the report."""
    ips = sorted({a.rsplit(":", 1)[0] for a in addresses})
    if not WINDOWS or not ips:
        return {}
    cmd = (
        "Get-DnsClientCache -ErrorAction SilentlyContinue | "
        "Select-Object Entry,Data | ConvertTo-Json"
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", cmd],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    ).stdout.strip()
    try:
        rows = json.loads(out) if out else []
    except ValueError:
        return {}
    rows = rows if isinstance(rows, list) else [rows]
    return {r.get("Data"): r.get("Entry") for r in rows if r.get("Data") in ips}


def remote_connections(pid: int) -> list[dict]:
    """The process's TCP connections that aren't listening sockets (Windows)."""
    if not WINDOWS:
        return []
    cmd = (
        f"Get-NetTCPConnection -OwningProcess {pid} -ErrorAction SilentlyContinue | "
        "Where-Object { $_.State -ne 'Listen' } | "
        "Select-Object LocalAddress,LocalPort,RemoteAddress,RemotePort,State | ConvertTo-Json"
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", cmd],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    ).stdout.strip()
    if not out:
        return []
    try:
        data = json.loads(out)
    except ValueError:
        return []
    return data if isinstance(data, list) else [data]

"""Attune end-to-end suite: real engines in replay mode, real pages in headless Edge.

Section 4 - Pages, Engine & Demo. TODO: P-37.

    python tests/e2e/run_e2e.py                      # every scenario except the soak
    python tests/e2e/run_e2e.py pages captions       # some scenarios
    python tests/e2e/run_e2e.py soak --soak-min 9    # the soak (at most 9 minutes a turn)
    python tests/e2e/run_e2e.py --list

Prints a pass/fail table and writes results.json, the engine logs and screenshots to
%TEMP%/attune_eval/qa/<run>/ (ATTUNE_E2E_OUT to change). Engines use port 8013
(ATTUNE_E2E_PORT) and hold the team's GPU lock while they run. See tests/e2e/README.md.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import re
import sys
import time
import traceback
from collections.abc import Callable, Iterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness as h

Check = dict


def check(name: str, ok: bool, detail=None, **extra) -> Check:
    if detail is not None and not isinstance(detail, str):
        detail = json.dumps(detail, default=str)[:1500]
    c = {"name": name, "ok": bool(ok), "detail": detail or ""}
    c.update(extra)
    print(
        f"  {'PASS' if ok else 'FAIL'} {name}{(' - ' + c['detail'][:300]) if c['detail'] else ''}",
        flush=True,
    )
    return c


class Run:
    """One suite run: its output folder, the GPU lock and the shared engine."""

    def __init__(self, out: Path, keep: bool = False):
        self.out = out
        self.out.mkdir(parents=True, exist_ok=True)
        self.keep = keep
        self.lock = h.GpuLock("e2e suite engine")
        self.results: dict[str, list[Check]] = {}
        self.main: h.Engine | None = None
        self.main_root: h.RunRoot | None = None
        self.snap_before: dict[str, int] | None = None
        self.notes: dict[str, object] = {}

    # ---- engines
    def engine(self, name: str, config=None, raw_toml=None, **kw) -> h.Engine:
        self.lock.acquire()
        root = h.RunRoot(name, config=config, raw_toml=raw_toml)
        kw.setdefault("source", str(h.find_video()))
        return h.Engine(root, **kw)

    def main_engine(self) -> h.Engine:
        """The shared engine: a film reel and a looping speech script."""
        if self.main is not None and self.main.alive():
            return self.main
        wav = h.speech_fixture("david_meeting")
        self.main = self.engine(
            "main", audio=str(wav[0]) if wav else None, repeat_audio=4
        )
        self.main_root = self.main.root
        # canaries: files that must never be served
        (self.main_root.data / "people" / "e2e-canary").mkdir(
            parents=True, exist_ok=True
        )
        (self.main_root.data / "people" / "e2e-canary" / "meta.json").write_text(
            '{"name": "CANARY-PERSON"}', encoding="utf-8"
        )
        (self.main_root.path / ".env").write_text(
            "ELEVENLABS_API_KEY=CANARY-KEY\n", encoding="utf-8"
        )
        self.snap_before = self.main_root.data_files()
        self.main.start()
        self.main.watch_network()
        return self.main

    def stop_main(self) -> None:
        if self.main is not None:
            self.main.stop()
            self.copy_log(self.main, "main")
            self.main_root.cleanup(keep_logs=True)
            self.main = None

    def copy_log(self, engine: h.Engine, name: str) -> None:
        for i in range(1, engine.starts + 1):
            src = engine.root.path / f"engine-{i}.log"
            if src.is_file():
                (self.out / f"engine-{name}-{i}.log").write_text(
                    src.read_text(encoding="utf-8", errors="replace"), encoding="utf-8"
                )

    def breathe(self, next_name: str | None) -> None:
        """Between scenarios: give the GPU lock back when no engine needs it, and at least
        every ~9 minutes (the team holds it at most 10 minutes at a time)."""
        if next_name not in USES_MAIN:
            self.stop_main()
        held = time.monotonic() - self.lock.since if self.lock.held else 0
        if self.main is None or held > 9 * 60:
            self.stop_main()
            if self.lock.held:
                self.lock.release()
                if held > 9 * 60:
                    time.sleep(30)  # a turn for whoever waits

    def finish(self) -> None:
        self.stop_main()
        self.lock.release()


# ============================================================================ scenarios
def scen_security(run: Run) -> list[Check]:
    e = run.main_engine()
    out = []
    probes = [
        "/data/people/",
        "/data/people/e2e-canary/meta.json",
        "/data/history.db",
        "/data/sessions/",
        "/data/reels/film/../../people/e2e-canary/meta.json",
        "/data/reels/film/..%2f..%2fpeople%2fe2e-canary%2fmeta.json",
        "/data/reels/film/..%5c..%5cpeople%5ce2e-canary%5cmeta.json",
        "/config/attune.toml",
        "/.env",
        "/lens/../../.env",
        "/lens/..%2f..%2f.env",
        "/lens/%2e%2e/%2e%2e/.env",
        "/shared/..%5c..%5c.env",
        "/phone/..%5c..%5cengine%5cattune%5cconfig.py",
        "/demo/..%2f..%2fconfig%2fattune.example.toml",
        "/engine/attune/main.py",
        "/lens//etc/passwd",
        "/lens/C:/Windows/win.ini",
        "/lens/%00",
        "/api/history/sessions/..%2f..%2f",
        "/docs",
        "/redoc",
    ]
    leaks = []
    for path in probes:
        status, body = e.http(path)
        text = body[:4000].decode(errors="replace")
        leaked = status == 200 and (
            "CANARY" in text
            or "[engine]" in text
            or "attune-e2e-no-such-camera" in text
            or "SQLite format" in text
            or "import" in text
            or "[fonts]" in text
            or "root:" in text
        )
        if leaked or (
            status == 200
            and path.startswith(
                (
                    "/data/people",
                    "/data/sessions",
                    "/data/history",
                    "/config",
                    "/.env",
                    "/engine",
                )
            )
        ):
            leaks.append(f"{path} -> {status}")
    out.append(
        check(
            "security: data/people, config, .env, history and ../ paths are not served",
            not leaks,
            leaks,
        )
    )
    status, body = e.http("/openapi.json")
    out.append(
        check(
            "security: no API schema published (/openapi.json)",
            status != 200,
            f"{status}",
        )
    )
    # a website on another origin must not reach the engine's WebSocket (captions, face crops)
    from websockets.sync.client import connect

    evil = "rejected"
    try:
        with connect(e.ws_url, origin="http://evil.example", open_timeout=5) as ws:
            ws.send(json.dumps({"type": "hello", "role": "console", "frames": False}))
            msg = ws.recv(timeout=3)
            evil = f"accepted: got {json.loads(msg).get('type')}"
    except Exception as exc:  # noqa: BLE001 - refused is the pass
        evil = f"rejected ({type(exc).__name__})"
    out.append(
        check(
            "security: WebSocket refuses pages from other origins",
            evil.startswith("rejected"),
            evil,
        )
    )
    ok_local = "no"
    try:
        with connect(
            e.ws_url, origin=f"http://localhost:{e.port}", open_timeout=5
        ) as ws:
            ws.send(json.dumps({"type": "hello", "role": "phone", "frames": False}))
            ok_local = json.loads(ws.recv(timeout=3)).get("type")
    except Exception as exc:  # noqa: BLE001
        ok_local = f"error {exc}"
    out.append(
        check(
            "security: WebSocket still accepts the laptop's own pages",
            ok_local == "welcome",
            ok_local,
        )
    )
    # DNS rebinding: a request that names another host must not read the history
    status, _ = e.http("/api/history/sessions", headers={"Host": "evil.example"})
    out.append(
        check(
            "security: requests for another host name are refused (DNS rebinding)",
            status in (400, 403, 421),
            status,
        )
    )
    # sim controls accept JSON only (a form on another site can't press them)
    status, _ = e.http(
        "/api/sim/touch",
        "POST",
        None,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    out.append(
        check("security: simulator controls refuse form posts", status == 415, status)
    )
    return out


def scen_pages(run: Run) -> list[Check]:
    e = run.main_engine()
    res = h.run_node(
        "pages.mjs", {"base": e.base, "out": str(run.out / "shots")}, timeout=1200
    )
    run.notes["pages_stderr"] = res.get("_stderr", "")[-2000:]
    checks = res.get("checks") or []
    if not checks:
        return [
            check(
                "pages: browser script ran",
                False,
                res.get("error") or res.get("_stderr", "")[-800:],
            )
        ]
    return checks


def _group_segments(finals: list[dict]) -> list[str]:
    """Join caption segments (<utt_id>, <utt_id>.1 ...) back into utterances, in order."""
    order: list[str] = []
    parts: dict[str, list[tuple[int, str]]] = {}
    for c in finals:
        uid = str(c["utt_id"])
        root, _, n = uid.partition(".")
        if root not in parts:
            order.append(root)
            parts[root] = []
        parts[root].append((int(n or 0), c.get("text", "")))
    return [" ".join(t for _, t in sorted(parts[r])) for r in order]


def scen_captions(run: Run) -> list[Check]:
    out = []
    fx = h.speech_fixture("david_meeting")
    if not fx:
        return [
            check(
                "captions: known-text speech available",
                False,
                "no SAPI voices; set up wavs",
            )
        ]
    wav, sentences = fx
    e = run.engine("captions", audio=str(wav), audio_delay=12)
    try:
        e.start()
        res = h.run_node(
            "captions.mjs",
            {"base": e.base, "seconds": 48, "out": str(run.out / "shots")},
            timeout=300,
        )
        out.extend(res.get("checks", []))
        if not res.get("lens"):
            out.append(
                check(
                    "captions: browser script ran",
                    False,
                    res.get("error") or res.get("_stderr", "")[-800:],
                )
            )
            return out
        for page in ("lens", "phone"):
            got = _group_segments(res[page]["received"])
            a = h.align_captions(sentences, got)
            run.notes[f"captions_{page}"] = {"got": got, "align": a}
            out.append(
                check(
                    f"captions {page}: every sentence arrives",
                    not a["missing"],
                    {"missing": [sentences[i] for i in a["missing"]], "got": got},
                )
            )
            out.append(check(f"captions {page}: in order", a["in_order"], a["matches"]))
            out.append(
                check(
                    f"captions {page}: no duplicates",
                    not a["duplicates"],
                    a["duplicates"],
                )
            )
            out.append(
                check(
                    f"captions {page}: word error rate under 15%",
                    a["wer"] <= 0.15,
                    f"WER {a['wer']:.1%}; unmatched {a['unmatched']}",
                )
            )
        lens_r = [r["text"] for r in res["lens"]["rendered"] if r.get("final")]
        a = h.align_captions(sentences, lens_r)
        out.append(
            check(
                "captions lens: drawn on the glasses (view model), in order, none missing",
                not a["missing"] and a["in_order"],
                {"drawn": lens_r[:12], "missing": a["missing"]},
            )
        )
        sr = res["lens"]["sr"]
        a = h.align_captions(sentences, [s.split(": ", 1)[-1] for s in sr])
        out.append(
            check(
                "captions lens: screen-reader live region reads every sentence",
                not a["missing"],
                {"sr": sr[:10]},
            )
        )
        cards = [c["text"] for c in res["phone"]["rendered"]]
        a = h.align_captions(sentences, cards)
        out.append(
            check(
                "captions phone: shown in the live view, in order, none missing",
                not a["missing"] and a["in_order"],
                {"cards": cards[:12], "missing": a["missing"]},
            )
        )
        dup_cards = len(res["phone"].get("onScreen") or []) != len(
            {(c["who"], c["text"]) for c in res["phone"].get("onScreen") or []}
        )
        out.append(
            check(
                "captions phone: no line shown twice on screen",
                not dup_cards,
                res["phone"].get("onScreen"),
            )
        )
    finally:
        e.stop()
        run.copy_log(e, "captions")
        e.root.cleanup()
    return out


def scen_first_words(run: Run) -> list[Check]:
    """Speech that starts 2 s after start-up (models just loaded) keeps its first words."""
    fx = h.speech_fixture("zira_cafe")
    if not fx:
        return []
    wav, sentences = fx
    e = run.engine("firstwords", audio=str(wav), audio_delay=2)
    out = []
    try:
        e.start()
        with h.ws_pages(e.ws_url, ("phone", False)) as (ph,):
            time.sleep(28)
            finals = [m for m in ph.of("caption") if m.get("final")]
        got = _group_segments(finals)
        a = h.align_captions(sentences, got)
        out.append(
            check(
                "captions right after start-up: first sentence complete",
                0 not in a["missing"] and a["matches"] and a["matches"][0][1] >= 0.8,
                {"got": got, "matches": a["matches"]},
            )
        )
        out.append(
            check(
                "captions right after start-up: every sentence arrives",
                not a["missing"],
                {"missing": a["missing"], "wer": a["wer"]},
            )
        )
    finally:
        e.stop()
        run.copy_log(e, "firstwords")
        e.root.cleanup()
    return out


def scen_camera_pause(run: Run) -> list[Check]:
    e = run.main_engine()
    out = []
    with h.ws_pages(e.ws_url, ("lens", True), ("console", False), ("phone", False)) as (
        lens,
        con,
        ph,
    ):
        time.sleep(4)
        t = time.monotonic()
        time.sleep(3)
        base_frames = lens.frames_since(t)
        out.append(
            check(
                "camera: frames flow to the lens",
                base_frames >= 20,
                f"{base_frames} frames in 3 s",
            )
        )
        t_off = time.monotonic()
        con.send("camera.set", {"on": False})
        heard = [
            p.wait("camera", lambda m: m["on"] is False, 5, t_off)
            for p in (lens, con, ph)
        ]
        out.append(
            check(
                "camera off: every page hears it (lens, console, phone)",
                all(heard),
                [bool(x) for x in heard],
            )
        )
        time.sleep(2)
        t = time.monotonic()
        caps0 = len(ph.of("caption", t))
        time.sleep(8)
        frames_off = lens.frames_since(t)
        caps_off = len(ph.of("caption", t)) - caps0
        out.append(
            check(
                "camera off: no frames", frames_off == 0, f"{frames_off} frames in 8 s"
            )
        )
        out.append(
            check(
                "camera off: captions keep running",
                caps_off > 0,
                f"{caps_off} caption messages in 8 s",
            )
        )
        st = con.of("status")[-1] if con.of("status") else {}
        cam_part = (
            (st.get("parts") or {}).get("camera")
            or (st.get("parts") or {}).get("vision")
            or {}
        )
        out.append(
            check(
                "camera off: status says so",
                "off" in json.dumps(cam_part).lower(),
                cam_part.get("detail"),
            )
        )
        # a page that opens now learns the camera is off
        with h.ws_pages(e.ws_url, ("phone", False)) as (late,):
            w = late.wait("welcome", timeout=10)
            out.append(
                check(
                    "camera off: a page that connects later is told (welcome)",
                    bool(w) and w.get("camera_on") is False,
                    w and w.get("camera_on"),
                )
            )
        t_on = time.monotonic()
        con.send("camera.set", {"on": True})
        heard = [
            p.wait("camera", lambda m: m["on"] is True, 5, t_on)
            for p in (lens, con, ph)
        ]
        out.append(
            check(
                "camera on: every page hears it", all(heard), [bool(x) for x in heard]
            )
        )
        first = None
        end = time.monotonic() + 15
        while time.monotonic() < end:
            if lens.frames_since(t_on) > 0:
                first = time.monotonic() - t_on
                break
            time.sleep(0.1)
        out.append(
            check(
                "camera on: frames come back within 8 s",
                first is not None and first < 8,
                f"{first:.1f} s" if first else "no frames in 15 s",
            )
        )
        # pause: recognition stops (no captions), resume brings it back
        time.sleep(2)
        t_p = time.monotonic()
        ph.send("pause.toggle")
        heard = [
            p.wait("paused", lambda m: m["paused"] is True, 5, t_p)
            for p in (lens, con, ph)
        ]
        out.append(
            check("pause: every page hears it", all(heard), [bool(x) for x in heard])
        )
        time.sleep(1.5)
        t = time.monotonic()
        time.sleep(10)
        caps_paused = len([m for m in ph.of("caption", t) if m.get("final")])
        scenes = lens.of("scene", t)
        named = [
            f
            for s in scenes
            for f in s.get("faces") or []
            if f.get("status") in ("named", "enrolled")
        ]
        out.append(
            check(
                "pause: no new final captions while paused",
                caps_paused == 0,
                f"{caps_paused} finals in 10 s",
            )
        )
        out.append(
            check(
                "pause: no faces recognised while paused",
                not named,
                f"{len(scenes)} scenes, {len(named)} named faces",
            )
        )
        with h.ws_pages(e.ws_url, ("lens", False)) as (late,):
            w = late.wait("welcome", timeout=10)
            out.append(
                check(
                    "pause: a page that connects later is told (welcome)",
                    bool(w) and w.get("paused") is True,
                    w and w.get("paused"),
                )
            )
        t_r = time.monotonic()
        ph.send("pause.toggle")
        heard = [
            p.wait("paused", lambda m: m["paused"] is False, 5, t_r)
            for p in (lens, con, ph)
        ]
        out.append(
            check("resume: every page hears it", all(heard), [bool(x) for x in heard])
        )
        got = ph.wait("caption", lambda m: m.get("final"), 40, t_r)
        out.append(
            check("resume: captions come back", bool(got), (got or {}).get("text"))
        )
    return out


def scen_hardware(run: Run) -> list[Check]:
    e = run.main_engine()
    out = []
    s = h.wait_until(lambda: e.sim() if e.sim().get("ok") else None, 15)
    if not s:
        return [check("hardware: simulator status available", False, e.sim())]
    sim = s["sim"]
    out.append(
        check(
            "hardware: simulated Arduino linked (READY, heartbeat)",
            sim["linked"] and s["metrics"]["connected"],
            {"firmware": s["metrics"].get("firmware"), "icon": sim["icon"]},
        )
    )
    out.append(
        check(
            "hardware: CFG rate/tap_ms/hold_ms/led sent on link",
            {"rate": 50, "tap_ms": 400, "hold_ms": 800, "led": 180}.items()
            <= sim["cfg"].items(),
            sim["cfg"],
        )
    )
    out.append(
        check("hardware: matrix shows HEART", sim["icon"] == "HEART", sim["icon"])
    )
    # heartbeat rate
    hb0 = sim["counts"].get("HB", 0)
    time.sleep(5)
    s2 = e.sim()
    rate = (s2["sim"]["counts"].get("HB", 0) - hb0) / 5
    out.append(
        check(
            "hardware: laptop heartbeat every 0.5 s",
            1.5 <= rate <= 2.6,
            f"{rate:.2f} HB/s",
        )
    )
    with h.ws_pages(e.ws_url, ("console", False), ("phone", False)) as (con, ph):
        acks0 = s2["sim"]["sent_acks"]
        sent = 0
        for name in ("T3", "T4", "BELL", "NAME", "OK", "NO"):
            for side in ("L", "R"):
                con.send("pattern.test", {"name": name, "side": side})
                sent += 1
                got = h.wait_until(
                    lambda n=name, sd=side: any(
                        re.fullmatch(rf"PAT \d+ {sd} {n}", ln)
                        for ln in e.sim()["sim"]["received"][-12:]
                    ),
                    5,
                )
                out.append(
                    check(
                        f"hardware: pattern.test {name} {side} reaches the board as PAT",
                        bool(got),
                    )
                )
                time.sleep(0.3)
        time.sleep(1)
        s3 = e.sim()
        out.append(
            check(
                "hardware: every PAT acknowledged (ACK), none pending",
                s3["metrics"]["pending_acks"] == 0
                and s3["sim"]["sent_acks"] - acks0 >= sent,
                {
                    "pending": s3["metrics"]["pending_acks"],
                    "acks": s3["sim"]["sent_acks"] - acks0,
                    "last_ack_ms": s3["metrics"].get("last_ack_ms"),
                },
            )
        )
        ack_ms = s3["metrics"].get("last_ack_ms")
        out.append(
            check(
                "hardware: ACK round trip under 250 ms",
                ack_ms is not None and ack_ms < 250,
                ack_ms,
            )
        )
        # a test of a looping alarm pattern must not buzz forever
        t_test = time.monotonic()
        con.send("pattern.test", {"name": "T3", "side": "B"})
        time.sleep(1)
        playing = e.sim()["sim"]["pattern"]
        stopped = h.wait_until(lambda: e.sim()["sim"]["pattern"] is None, 8)
        took = time.monotonic() - t_test
        out.append(
            check(
                "hardware: a T3 pattern test stops by itself after one cycle",
                playing == "T3" and bool(stopped) and took < 5.5,
                {
                    "playing": playing,
                    "after": e.sim()["sim"]["pattern"],
                    "stopped_after_s": round(took, 1),
                },
            )
        )
        con.send("pattern.test", {"name": "XYZ", "side": "L"})
        time.sleep(1)
        out.append(
            check(
                "hardware: an unknown pattern is refused, nothing sent",
                not any("XYZ" in ln for ln in e.sim()["sim"]["received"]),
                e.sim()["sim"]["received"][-3:],
            )
        )
        # touches
        t0 = time.monotonic()
        e.touch("triple")
        p1 = ph.wait("paused", lambda m: m["paused"] is True, 5, t0)
        t1 = time.monotonic()
        e.touch("triple")
        p2 = ph.wait("paused", lambda m: m["paused"] is False, 5, t1)
        out.append(
            check(
                "touch: triple tap pauses, again resumes (pages hear it)",
                bool(p1) and bool(p2),
                {"pause": bool(p1), "resume": bool(p2)},
            )
        )
        t2 = time.monotonic()
        e.touch("double")
        sv = ph.wait("save_cancel", timeout=6, since=t2) or ph.wait(
            "save_request", timeout=1, since=t2
        )
        out.append(
            check(
                "touch: double tap starts 'save this person' (phone told)",
                bool(sv),
                sv and {k: sv.get(k) for k in ("type", "reason", "name")},
            )
        )
        if sv and sv.get("type") == "save_request":
            con.send("save.cancel", {"request_id": sv.get("request_id")})
        for g in ("tap", "hold"):
            n0 = e.sim()["metrics"]["touches"]
            e.touch(g)
            ok = h.wait_until(lambda n=n0: e.sim()["metrics"]["touches"] > n, 4)
            out.append(check(f"touch: {g} arrives from the board", bool(ok)))
        status, _ = e.http("/api/sim/touch", "POST", {"gesture": "swipe"})
        out.append(check("touch: an unknown gesture is refused", status == 400, status))
        # watchdog: laptop heartbeat stops -> the board stops everything and shows LOST
        con.send("pattern.test", {"name": "T4", "side": "L"})
        time.sleep(0.5)
        e.sim_control(heartbeat=False)
        lost = h.wait_until(
            lambda: (not e.sim()["sim"]["linked"]) and e.sim()["sim"]["icon"] == "LOST",
            5,
        )
        snap = e.sim()["sim"]
        out.append(
            check(
                "watchdog: no heartbeat for 2 s -> board stops patterns and shows LOST",
                bool(lost) and snap["pattern"] in (None, "LOST"),
                {
                    "linked": snap["linked"],
                    "icon": snap["icon"],
                    "pattern": snap["pattern"],
                },
            )
        )
        cfg0 = e.sim()["sim"]["counts"].get("CFG", 0)
        e.sim_control(heartbeat=True)
        back = h.wait_until(
            lambda: e.sim()["sim"]["linked"] and e.sim()["sim"]["icon"] == "HEART", 6
        )
        out.append(
            check(
                "watchdog: heartbeat back -> READY again, CFG re-sent, HEART",
                bool(back) and e.sim()["sim"]["counts"].get("CFG", 0) > cfg0,
                {
                    "cfg_before": cfg0,
                    "cfg_after": e.sim()["sim"]["counts"].get("CFG", 0),
                    "icon": e.sim()["sim"]["icon"],
                },
            )
        )
        m0 = e.sim()["metrics"]
        out.append(
            check(
                "watchdog: a relink after a heartbeat gap is not counted as a board restart",
                m0.get("board_restarts") == 0 and m0.get("ready_again", 0) >= 1,
                {k: m0.get(k) for k in ("board_restarts", "ready_again")},
            )
        )
        # a brown-out: the board resets (its clock starts again) - the laptop notices and re-sends CFG
        cfg1 = e.sim()["sim"]["counts"].get("CFG", 0)
        t_boot = time.monotonic()
        e.sim_control(reboot=True)
        noticed = h.wait_until(
            lambda: e.sim()["metrics"].get("board_restarts", 0) >= 1, 5
        )
        relinked = h.wait_until(
            lambda: (
                e.sim()["sim"]["icon"] == "HEART"
                and e.sim()["sim"]["counts"].get("CFG", 0) >= cfg1 + 4
            ),
            6,
        )
        warned = e.wait_log(r"the board restarted", 3)
        out.append(
            check(
                "hardware: a board restart (brown-out) is noticed, logged, and the link recovers with CFG re-sent",
                bool(noticed) and bool(relinked) and bool(warned),
                {
                    "board_restarts": e.sim()["metrics"].get("board_restarts"),
                    "cfg_sent": e.sim()["sim"]["counts"].get("CFG", 0) - cfg1,
                    "s": round(time.monotonic() - t_boot, 1),
                },
            )
        )
        # pause shows the PAUSE icon
        t3 = time.monotonic()
        ph.send("pause.toggle")
        ph.wait("paused", lambda m: m["paused"], 5, t3)
        icon = h.wait_until(lambda: e.sim()["sim"]["icon"] == "PAUSE", 4)
        t4 = time.monotonic()
        ph.send("pause.toggle")
        ph.wait("paused", lambda m: not m["paused"], 5, t4)
        icon2 = h.wait_until(lambda: e.sim()["sim"]["icon"] == "HEART", 4)
        out.append(
            check(
                "hardware: pause shows PAUSE on the matrix, resume HEART",
                bool(icon) and bool(icon2),
            )
        )
        hw = con.of("status")[-1]["parts"]["hardware"] if con.of("status") else {}
        out.append(
            check(
                "hardware: link stayed up, no board errors",
                hw.get("ok") and hw.get("metrics", {}).get("errors", 1) == 0,
                {
                    "ok": hw.get("ok"),
                    "errors": hw.get("metrics", {}).get("errors"),
                    "reconnects": hw.get("metrics", {}).get("reconnects"),
                },
            )
        )
    return out


def scen_alerts(run: Run) -> list[Check]:
    out = []
    for kind, pattern, alert_kind, side in (
        ("T3", "T3", "smoke", "left"),
        ("T4", "T4", "co", "right"),
    ):
        wav = h.tone_fixture(kind)
        e = run.engine(f"alert-{kind}", audio=str(wav), audio_delay=10)
        try:
            e.start()
            # a louder sensor on one side gives the alarm its direction
            h.wait_until(lambda e=e: e.sim().get("ok"), 15)
            e.sim_control(
                sound={"left": 300, "right": 0}
                if side == "left"
                else {"left": 0, "right": 300}
            )
            res = h.run_node(
                "alerts.mjs",
                {
                    "base": e.base,
                    "kind": alert_kind,
                    "side": side,
                    "timeout": 45,
                    "out": str(run.out / "shots"),
                },
                timeout=200,
            )
            out += res.get("checks") or [
                check(
                    f"alert {kind}: browser script ran",
                    False,
                    res.get("error") or res.get("_stderr", "")[-600:],
                )
            ]
            received = e.sim().get("sim", {}).get("received", [])
            pats = [ln for ln in received if ln.startswith("PAT")]
            want = "L" if side == "left" else "R"
            out.append(
                check(
                    f"alert {kind}: the rig gets PAT {pattern} on the {side} ({want})",
                    any(re.fullmatch(rf"PAT \d+ {want} {pattern}", ln) for ln in pats),
                    pats[-4:],
                )
            )
            out.append(
                check(
                    f"alert {kind}: acknowledging stops the rig (STOP)",
                    any(ln.startswith("STOP") for ln in received),
                    received[-4:],
                )
            )
        finally:
            e.stop()
            run.copy_log(e, f"alert-{kind}")
            e.root.cleanup()
    return out


def scen_reconnect(run: Run) -> list[Check]:
    e = run.main_engine()
    out = []
    proc = h.start_node(
        "reconnect.mjs", {"base": e.base, "out": str(run.out / "shots")}
    )
    try:
        line = h.read_line(proc, "READY", 60)
        if not line:
            return [
                check(
                    "reconnect: pages connected before the restart",
                    False,
                    h.drain(proc),
                )
            ]
        t0 = time.monotonic()
        e.stop()
        down = h.read_line(proc, "DOWN", 30)
        out.append(
            check("reconnect: pages notice the engine went away", bool(down), down)
        )
        e.start()
        ready_s = time.monotonic() - t0
        proc.stdin.write("UP\n")
        proc.stdin.flush()
        res = h.read_json(proc, 120)
        out += res.get("checks") or [
            check("reconnect: browser script finished", False, res)
        ]
        out.append(
            check(
                "reconnect: engine back up", e.alive(), f"restart took {ready_s:.0f} s"
            )
        )
    finally:
        with h.suppress():
            proc.kill()
    return out


def scen_save(run: Run) -> list[Check]:
    e = run.main_engine()
    res = h.run_node(
        "save.mjs", {"base": e.base, "out": str(run.out / "shots")}, timeout=240
    )
    return res.get("checks") or [
        check(
            "save: browser script ran",
            False,
            res.get("error") or res.get("_stderr", "")[-600:],
        )
    ]


def scen_privacy(run: Run) -> list[Check]:
    e = run.main_engine()
    out = []
    # history has this session's lines, forget wipes them everywhere
    with h.ws_pages(
        e.ws_url, ("console", False), ("lens", False), ("phone", False)
    ) as (con, lens, ph):
        cap = ph.wait(
            "caption",
            lambda m: m.get("final") and len(m.get("text", "").split()) >= 3,
            60,
        )
        word = max(h.words(cap["text"]), key=len) if cap else "train"
        rows = h.wait_until(lambda: e.json(f"/api/history/search?q={word}") or None, 10)
        out.append(
            check(
                "history: this session's captions are searchable",
                isinstance(rows, list) and len(rows) > 0,
                f"q={word}: {len(rows) if isinstance(rows, list) else rows}",
            )
        )
        scenes = lens.of("scene")[-30:]
        labels_before = sorted(
            {f.get("label") for s in scenes for f in s.get("faces") or []}
        )
        t = time.monotonic()
        wall_forget = time.time()
        con.send("session.forget")
        time.sleep(2.5)
        # history's default forget mode drops strangers' lines; the wearer's own stay
        current = e.json("/api/history/sessions/current")
        stranger = (
            [
                r
                for r in current
                if r.get("kind") in ("caption", "translation")
                and not str(r.get("speaker_label") or "").startswith("You")
                and float(r.get("t") or 0)
                < wall_forget - 0.5  # lines said after it are new
            ]
            if isinstance(current, list)
            else current
        )
        out.append(
            check(
                "forget session: strangers' lines are gone from this session's history",
                stranger == [],
                f"{len(stranger) if isinstance(stranger, list) else stranger} left: {[r.get('text') for r in stranger][:3] if isinstance(stranger, list) else ''}",
            )
        )
        after = [s for s in lens.of("scene", t + 0.5)]
        labels_after = sorted(
            {f.get("label") for s in after[:10] for f in s.get("faces") or []}
        )
        # strangers are described afresh from what they wear ("Person in blue"); a name heard
        # this session ("Maya?", "Maya") must be gone
        named = [
            lb
            for lb in labels_after
            if lb and not re.match(r"^(Person\b|Someone$)", lb)
        ]
        out.append(
            check(
                "forget session: no session names or name guesses left on strangers",
                not named,
                {"before": labels_before, "after": labels_after},
            )
        )
        log = con.of("event_log", t)
        out.append(
            check(
                "forget session: console event log cleared",
                any("forgotten" in m.get("text", "").lower() for m in log),
                [m.get("text") for m in log][:5],
            )
        )
    # nothing personal written to disk
    after_files = run.main_root.data_files()
    new = sorted(set(after_files) - set(run.snap_before or {}))
    # AGENTS.md: people clearly heard talking with the wearer become automatic contacts, kept
    # as face and voice prints in data/people; never a photo or a recording, never elsewhere
    media = [
        f
        for f in new
        if re.search(
            r"\.(jpe?g|png|webp|bmp|gif|wav|mp3|ogg|flac|m4a|webm|mp4)$",
            f,
            re.IGNORECASE,
        )
    ]
    stray = [
        f
        for f in new
        if re.search(r"\.(npy|npz)$", f, re.IGNORECASE) and not f.startswith("people/")
    ]
    contacts = sorted(
        {f.split("/")[1] for f in new if f.count("/") >= 2 and f.startswith("people/")}
    )
    out.append(
        check(
            "privacy: no photos or audio written; prints only as contacts in data/people",
            not media and not stray,
            {
                "photos_or_audio": media,
                "prints_outside_people": stray,
                "automatic_contacts": len(contacts),
                "new_files": new[:20],
            },
        )
    )
    for f in new:
        if f.startswith("sessions/") and f.endswith(".jsonl"):
            text = (
                (run.main_root.data / f)
                .read_text(encoding="utf-8", errors="replace")
                .lower()
            )
            leaked = [
                w
                for w in ("train", "library", "folder", "sister", "entrance", "canary")
                if w in text
            ]
            out.append(
                check("privacy: session log has no caption text", not leaked, leaked)
            )
    # network: only loopback, all the time the engine has been running (the watcher looks
    # every 10 s; the real interpreter, not the venv launcher)
    conns = h.remote_connections(e.server_pid() or e.proc.pid)
    for c in conns:
        if c.get("RemoteAddress") not in h.LOOPBACK_ADDRS:
            e.remote_seen.setdefault(
                f"{c.get('RemoteAddress')}:{c.get('RemotePort')}", -1
            )
    seen = dict(e.remote_seen)
    names = h.dns_names(list(seen))
    watch = e.net_watch_summary()
    out.append(
        check(
            "privacy: engine talks only to 127.0.0.1 (no outside connections while it ran)",
            not seen and watch["enough"],
            {
                "remote": {
                    k: {"first_seen_s": v, "host": names.get(k.rsplit(":", 1)[0])}
                    for k, v in seen.items()
                },
                "open_now": len(conns),
                "watch": watch,  # too few looks is a failure: not measured is not clean
            },
        )
    )
    return out


def code_hosts(root: Path) -> dict[str, list[str]]:
    """Hosts named in string literals of the code (not comments or docstrings)."""
    import ast

    hosts: dict[str, list[str]] = {}
    for p in root.rglob("*.py"):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        docstrings = set()
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            first = body[0] if isinstance(body, list) and body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstrings.add(id(first.value))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
            ):
                for m in re.finditer(
                    r"(https?|wss?)://([A-Za-z0-9.\-_\[\]:]+)", node.value
                ):
                    host = m.group(2).split(":")[0].strip("[]")
                    hosts.setdefault(host, []).append(p.relative_to(h.REPO).as_posix())
    return {k: sorted(set(v)) for k, v in hosts.items()}


def scen_code_network(run: Run) -> list[Check]:
    """The engine's code names only loopback hosts; downloads live in scripts/ only."""
    allowed = {"localhost", "127.0.0.1", "0.0.0.0", "", "::1"}
    hosts = code_hosts(h.REPO / "engine" / "attune")
    other = {k: v for k, v in hosts.items() if k not in allowed}
    run.notes["code_hosts"] = hosts
    return [
        check(
            "privacy: engine code only names loopback hosts (ElevenLabs goes through its SDK)",
            not other,
            other,
        )
    ]


def scen_robustness(run: Run) -> list[Check]:
    out = []
    cases = [
        (
            "no-mic",
            {},
            None,
            {"no_mic": True, "audio": None},
            [r"Started audio", r"Attune is running"],
        ),
        # the named camera isn't there (and the "any camera" fallback is off): keep running, say so
        (
            "no-camera",
            {},
            None,
            {"source": None},
            [r"(?i)camera\b.*\b(not found|lost|missing)|no camera"],
        ),
        # a typo in --source must stop at once, never fall back to a webcam
        (
            "bad-source",
            {},
            None,
            {"source": "e2e-missing-video.mp4"},
            [r"no such video file"],
        ),
        (
            "missing-model",
            {
                "nemotron": {"encoder": "models/nemotron/e2e-missing.onnx"},
                "vision": {"det_model": "models/faces/e2e-missing.onnx"},
            },
            None,
            {},
            [r"e2e-missing\.onnx", r"Running without"],
        ),
        (
            "bad-values",
            {
                "vision": {"det_size": "big", "e2e_unknown_key": 1},
                "alerts": {"window_s": 2.0},
                "pages": {"jpeg_quality": 500},
                "hardware": {"heartbeat_s": 0},
            },
            None,
            {},
            [r"Running without"],
        ),
        (
            "toml-syntax",
            None,
            "[vision\ncamera_name = \n",
            {},
            [r"Config error: .*attune\.toml.*Expected"],
        ),
    ]
    exits = {
        "bad-source",
        "toml-syntax",
    }  # these must stop with one clear line and exit code 2
    for name, cfg, raw, kw, expect in cases:
        audio = kw.pop("audio", "keep")
        fx = h.speech_fixture("zira_cafe")
        if audio == "keep" and fx:
            kw["audio"] = str(fx[0])
        e = run.engine(f"robust-{name}", config=cfg, raw_toml=raw, **kw)
        t0 = time.monotonic()
        try:
            try:
                e.start(timeout=90)
                started = True
            except (RuntimeError, TimeoutError) as exc:
                started = False
                start_err = str(exc)
            if started:
                time.sleep(25)
            log = e.log()
            alive = e.alive()
            # a busy shared Ollama logs two tracebacks per timed-out request (reported
            # separately); a crash loop is anything else
            ollama = log.count("local Ollama request failed")
            tracebacks = log.count("Traceback") - 2 * ollama
            lines = [
                ln.split(": ", 1)[-1]
                for ln in log.splitlines()
                if " E " in ln or " W " in ln
            ]
            counts: dict[str, int] = {}
            for ln in lines:
                key = re.sub(r"\d+(\.\d+)?", "#", ln)[:120]
                counts[key] = counts.get(key, 0) + 1
            loops = {k: v for k, v in counts.items() if v >= 15}
            found = [bool(re.search(x, log)) for x in expect]
            if name in exits:
                code = e.proc.returncode if e.proc else None
                out.append(
                    check(
                        f"robustness {name}: stops at once with one clear message (exit 2, no traceback)",
                        not started
                        and not alive
                        and code == 2
                        and all(found)
                        and tracebacks == 0,
                        {
                            "exit": code,
                            "tracebacks": tracebacks,
                            "tail": log.strip().splitlines()[-2:],
                        },
                    )
                )
                continue
            out.append(
                check(
                    f"robustness {name}: engine keeps running",
                    started and alive,
                    None if started else start_err,
                )
            )
            out.append(
                check(
                    f"robustness {name}: clear log message",
                    all(found),
                    {"expected": expect, "found": found},
                )
            )
            out.append(
                check(
                    f"robustness {name}: no crash loop or error flood",
                    not loops and tracebacks <= 3,
                    {"tracebacks": tracebacks, "repeated": loops},
                )
            )
            if name == "no-camera":

                def camera_missing(m):
                    parts = m.get("parts") or {}
                    cam, vis = parts.get("camera") or {}, parts.get("vision") or {}
                    return cam.get("ok") is False or (
                        vis.get("ok") is False and "camera" in str(vis.get("detail"))
                    )

                with h.ws_pages(e.ws_url, ("console", False)) as (con,):
                    st = con.wait("status", camera_missing, 10)
                    last = con.of("status")[-1] if con.of("status") else {}
                parts = (st or last).get("parts") or {}
                out.append(
                    check(
                        "robustness no-camera: the console shows the camera is missing",
                        bool(st),
                        {k: parts.get(k) for k in ("camera", "vision")},
                    )
                )
            if name == "bad-values":
                hb = h.wait_until(lambda e=e: e.sim().get("sim"), 5)
                if hb:
                    t = time.monotonic()
                    c0 = hb["counts"].get("HB", 0)
                    time.sleep(2)
                    rate = (e.sim()["sim"]["counts"].get("HB", 0) - c0) / (
                        time.monotonic() - t
                    )
                    out.append(
                        check(
                            "robustness bad-values: heartbeat_s = 0 doesn't flood the serial link",
                            rate < 25,
                            f"{rate:.0f} HB/s",
                        )
                    )
            run.notes[f"robust_{name}"] = {
                "elapsed": round(time.monotonic() - t0, 1),
                "warnings": list(counts)[:20],
            }
        finally:
            e.stop()
            run.copy_log(e, f"robust-{name}")
            e.root.cleanup()
    return out


def scen_soak(run: Run, minutes: float) -> list[Check]:
    out = []
    fx = h.speech_fixture("david_meeting")
    e = run.engine("soak", audio=str(fx[0]), repeat_audio=3)
    samples = []
    try:
        e.start()
        e.watch_network()
        proc = h.start_node(
            "soak.mjs", {"base": e.base, "minutes": minutes, "every": 30}
        )
        with h.ws_pages(
            e.ws_url, ("console", False), ("phone", False), ("lens", True)
        ) as (con, ph, lens):
            t0 = time.monotonic()
            end = t0 + minutes * 60
            pid = (
                e.server_pid() or e.proc.pid
            )  # the real interpreter, not the venv launcher
            last_counts = None
            while time.monotonic() < end:
                time.sleep(30)
                now = time.monotonic()
                ps = h.process_stats(pid)
                st = con.of("status")[-1] if con.of("status") else {}
                counts = {
                    k: len(p.of(k))
                    for p in (ph,)
                    for k in ("caption", "status", "paused")
                }
                counts["frames"] = lens.frames_since(0)
                counts["scene"] = len(lens.of("scene"))
                rates = {
                    k: round((counts[k] - (last_counts or {}).get(k, 0)) / 30, 2)
                    for k in counts
                }
                last_counts = counts
                cam = (st.get("parts") or {}).get("vision", {}).get("metrics", {})
                samples.append(
                    {
                        "t_min": round((now - t0) / 60, 2),
                        **ps,
                        "gpu_mb": h.gpu_used_mb(),
                        "gpu_proc_mb": h.gpu_process_mb(pid),
                        "fps": st.get("fps"),
                        "caption_delay": st.get("caption_delay"),
                        "rates": rates,
                        "vision": {
                            k: cam.get(k)
                            for k in ("fps", "det_ms", "faces")
                            if k in cam
                        },
                    }
                )
                print(
                    f"  soak {samples[-1]['t_min']:.1f} min: rss {ps.get('rss_mb')} MB, cpu {ps.get('cpu_s')} s, gpu {samples[-1]['gpu_mb']} MB, fps {st.get('fps')}, rates {rates}",
                    flush=True,
                )
                if not e.alive():
                    break
            alive = e.alive()
        page = h.read_json(proc, 180)
        run.notes["soak"] = {
            "engine": samples,
            "pages": page.get("samples"),
            "page_checks": page.get("checks"),
        }
        out.append(check(f"soak: engine alive after {minutes:.0f} min", alive))
        seen = dict(e.remote_seen)
        names = h.dns_names(list(seen))
        watch = e.net_watch_summary()
        out.append(
            check(
                "soak: no outside connections",
                not seen and watch["enough"],
                {
                    "remote": {
                        k: {"first_seen_s": v, "host": names.get(k.rsplit(":", 1)[0])}
                        for k, v in seen.items()
                    },
                    "watch": watch,
                },
            )
        )
        if len(samples) >= 4:
            # models load lazily in the first minutes (a one-off step); a leak keeps growing
            warm_min = min(3.0, minutes / 3)
            warm = next((i for i, s in enumerate(samples) if s["t_min"] >= warm_min), 1)
            first, base, last = samples[0], samples[warm], samples[-1]

            def grew(key: str) -> tuple[float, str]:
                a, b, z = first.get(key), base.get(key), last.get(key)
                if b is None or z is None:
                    return 0.0, f"{key} not measured"
                return z - b, (
                    f"{a} at {first['t_min']} min, {b} at {base['t_min']} min, "
                    f"{z} at {last['t_min']} min ({z - b:+.0f} after warm-up)"
                )

            growth, detail = grew("private_mb")
            out.append(
                check(
                    "soak: engine memory steady after warm-up (under 200 MB growth)",
                    growth < 200,
                    f"private MB: {detail}",
                )
            )
            t_growth, t_detail = grew("threads")
            h_growth, h_detail = grew("handles")
            out.append(
                check(
                    "soak: engine threads and handles steady after warm-up",
                    t_growth < 20 and h_growth < 300,
                    f"threads: {t_detail}; handles: {h_detail}",
                )
            )
            fps = [s["fps"] for s in samples if s.get("fps")]
            out.append(
                check(
                    "soak: vision fps stays >= 24 (T-E5)",
                    fps and min(fps) >= 24,
                    f"min {min(fps) if fps else None}, median {sorted(fps)[len(fps) // 2] if fps else None}",
                )
            )
            cap_rates = [s["rates"].get("caption", 0) for s in samples[1:]]
            out.append(
                check(
                    "soak: captions keep flowing",
                    all(r > 0 for r in cap_rates),
                    cap_rates,
                )
            )
            # the engine's own GPU memory when Windows can tell; otherwise the whole GPU,
            # which also moves with every other engine on the laptop
            own = all(s.get("gpu_proc_mb") is not None for s in samples)
            key = "gpu_proc_mb" if own else "gpu_mb"
            g_growth, g_detail = grew(key)
            out.append(
                check(
                    "soak: engine GPU memory steady after warm-up (under 300 MB growth)",
                    g_growth < 300,
                    f"{'this engine' if own else 'whole GPU (shared)'}: {g_detail}",
                )
            )
        out += page.get("checks") or [
            check("soak: page memory script finished", False, page)
        ]
    finally:
        e.stop()
        run.copy_log(e, "soak")
        e.root.cleanup()
    return out


# the phone's ElevenLabs settings (P-41) write the engine's working-folder .env: the suite's
# engines run in a temporary folder, so only that folder's .env is touched. Dummy keys only.
DUMMY_KEY = "e2e_dummy_key_0000_not_a_real_key"
ATTACKER_KEY = "e2e_attacker_key"


def scen_speech_settings(run: Run) -> list[Check]:
    from dotenv import dotenv_values

    e = run.main_engine()
    out: list[Check] = []
    env_file = e.root.path / ".env"
    if env_file.resolve().parent != e.root.path.resolve() or not str(
        e.root.path.resolve()
    ).startswith(str(h.out_root().resolve())):
        return [
            check(
                "settings: the engine runs in a temporary folder",
                False,
                str(e.root.path),
            )
        ]
    canary = env_file.read_bytes() if env_file.is_file() else b""
    old_key = (
        (dotenv_values(env_file).get("ELEVENLABS_API_KEY") or "") if canary else ""
    )
    repo_env = h.REPO / ".env"
    repo_before = repo_env.stat().st_mtime_ns if repo_env.is_file() else None
    api = "/api/settings/elevenlabs"
    own = {"Origin": e.base}
    try:
        status, body = e.http(api, headers=own)
        out.append(
            check(
                "settings: the laptop's own page reads the settings; the key is never in the answer",
                status == 200
                and b'"key_configured":true' in body.replace(b" ", b"")
                and (not old_key or old_key.encode() not in body),
                {"status": status, "body": body[:200].decode(errors="replace")},
            )
        )
        attack = {"api_key": ATTACKER_KEY}
        other = f"http://localhost:{e.port}"
        tries = {
            "GET, Origin evil.example": e.http(
                api, headers={"Origin": "http://evil.example"}
            )[0],
            "POST, Origin evil.example": e.http(
                api, "POST", attack, headers={"Origin": "http://evil.example"}
            )[0],
            "POST, Origin localhost (another origin)": e.http(
                api, "POST", attack, headers={"Origin": other}
            )[0],
            "POST, no Origin": e.http(api, "POST", attack)[0],
            "POST, own Origin but Sec-Fetch-Site cross-site": e.http(
                api, "POST", attack, headers={**own, "Sec-Fetch-Site": "cross-site"}
            )[0],
            "GET, Host evil.example (DNS rebinding)": e.http(
                api, headers={"Host": "evil.example"}
            )[0],
        }
        out.append(
            check(
                "settings: other origins, rebinding and origin-less posts are refused",
                all(s in (400, 403) for s in tries.values()),
                tries,
            )
        )
        bad = {
            "text/plain body": e.http(
                api, "POST", None, headers={**own, "Content-Type": "text/plain"}
            )[0],
            "a key with spaces": e.http(
                api, "POST", {"api_key": "bad key!"}, headers=own
            )[0],
            "an unknown field": e.http(
                api, "POST", {"api_key": "x", "url": "http://x"}, headers=own
            )[0],
        }
        out.append(
            check(
                "settings: bad requests from the own page are refused (415/400)",
                bad["text/plain body"] == 415
                and bad["a key with spaces"] == 400
                and bad["an unknown field"] == 400,
                bad,
            )
        )
        now = env_file.read_bytes() if env_file.is_file() else b""
        out.append(
            check(
                "settings: nothing was written by the refused requests",
                now == canary,
                "unchanged" if now == canary else "the .env changed",
            )
        )
        res = h.run_node(
            "settings.mjs",
            {
                "base": e.base,
                "other": other,
                "key": DUMMY_KEY,
                "old": old_key or "CANARY-KEY",
                "voice": "e2eVoice123",
                "frameVoice": "e2eVoiceFrame456",
                "out": str(run.out / "shots"),
            },
            timeout=300,
        )
        out.extend(
            res.get("checks")
            or [
                check(
                    "settings: browser script ran",
                    False,
                    res.get("error") or res.get("_stderr", "")[-600:],
                )
            ]
        )
        values = dotenv_values(env_file) if env_file.is_file() else {}
        text = env_file.read_text(encoding="utf-8") if env_file.is_file() else ""
        out.append(
            check(
                "settings: the key went into the engine's own (temporary) .env, the voice from the demo frame too",
                values.get("ELEVENLABS_API_KEY") == DUMMY_KEY
                and values.get("ELEVENLABS_VOICE_ID") == "e2eVoiceFrame456",
                {
                    "file": str(env_file),
                    "key_saved": values.get("ELEVENLABS_API_KEY") == DUMMY_KEY,
                    "voice": values.get("ELEVENLABS_VOICE_ID"),
                },
            )
        )
        out.append(
            check(
                "settings: the other origin's key never landed",
                ATTACKER_KEY not in text,
                "not in .env" if ATTACKER_KEY not in text else "FOUND in .env",
            )
        )
        repo_after = repo_env.stat().st_mtime_ns if repo_env.is_file() else None
        out.append(
            check(
                "settings: the checkout's own .env was not touched",
                repo_after == repo_before,
                {"exists": repo_env.is_file(), "changed": repo_after != repo_before},
            )
        )
        status, body = e.http(api, headers=own)
        log = (
            e.log_path.read_text(encoding="utf-8", errors="replace")
            if e.log_path.is_file()
            else ""
        )
        out.append(
            check(
                "settings: the key is not in the settings answer or the engine log",
                DUMMY_KEY.encode() not in body and DUMMY_KEY not in log,
                {"answer": DUMMY_KEY.encode() in body, "log": DUMMY_KEY in log},
            )
        )
        leftovers = sorted(p.name for p in e.root.path.glob(".env.settings-*"))
        out.append(
            check(
                "settings: no temporary settings files left", not leftovers, leftovers
            )
        )
    finally:
        # put the canary back so the other scenarios see the same engine folder
        if canary:
            env_file.write_bytes(canary)
        else:
            env_file.unlink(missing_ok=True)
        for p in e.root.path.glob(".env.settings-*"):
            p.unlink(missing_ok=True)
    return out


@contextlib.contextmanager
def fake_station_engine(tag: str) -> Iterator[str | None]:
    """Start tests/pages_engine/station_e2e/fake_engine.py on the QA port with an empty
    prints folder; yields its READY line (None if it did not come up), stops it after."""
    import subprocess

    fake = h.REPO / "tests" / "pages_engine" / "station_e2e" / "fake_engine.py"
    data = h.out_root() / f"station-{tag}-{dt.datetime.now().astimezone():%H%M%S}"
    proc = subprocess.Popen(
        [h.python_exe(), str(fake), "--port", str(h.PORT), "--data", str(data)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=h.REPO,
        env={**h.os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    try:
        yield h.read_line(proc, "READY", 45)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        h.wait_until(lambda: h.port_free(h.PORT), 10)
        h.shutil.rmtree(data, ignore_errors=True)


def scen_station(run: Run) -> list[Check]:
    """The phone's laptop-station screens (P-35) against the station's fake engine.

    tests/pages_engine/station_e2e/fake_engine.py runs the real hub, save flow and station
    with a fake laptop camera, mic and models (no device, no GPU, drawn "people", prints in a
    temporary folder). Its own flow test (phone_station.mjs) runs first, then station.mjs
    checks layout, contrast, names and focus on every station screen. Each gets a fresh fake
    engine, because the flow test saves the two "people" the audit needs unsaved."""
    import subprocess

    here = h.REPO / "tests" / "pages_engine" / "station_e2e"
    flow = here / "phone_station.mjs"
    if not (here / "fake_engine.py").is_file() or not flow.is_file():
        return [
            check("station: the station's fake engine is in the repo", False, str(here))
        ]
    if not h.port_free(h.PORT):
        return [check("station: port free for the fake engine", False, h.PORT)]
    base = f"http://127.0.0.1:{h.PORT}"
    out: list[Check] = []
    with fake_station_engine("flow") as ready:
        out.append(
            check("station: fake engine up (no camera, mic or GPU)", bool(ready), ready)
        )
        if not ready:
            return out
        res = subprocess.run(
            [
                h.find_node() or "node",
                str(flow),
                base,
                str(run.out / "shots" / "station-flow"),
            ],
            env={
                **h.os.environ,
                "PLAYWRIGHT_CORE": str(h.find_playwright() / "playwright-core"),
            },
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=400,
            check=False,
        )
        steps = [
            ln.split(" ok ", 1)[1] for ln in res.stdout.splitlines() if " ok " in ln
        ]
        failed = next(
            (ln for ln in (res.stderr + res.stdout).splitlines() if "FAILED" in ln), ""
        )
        out.append(
            check(
                "station flow: enroll, mismatch, linked save, fallback, Escape "
                f"({len(steps)} steps)",
                res.returncode == 0 and "PASS phone station screens" in res.stdout,
                failed or (steps[-1] if steps else res.stderr[-400:]),
            )
        )
    with fake_station_engine("audit") as ready:
        if not ready:
            out.append(
                check("station: fake engine up again for the audit", False, ready)
            )
            return out
        res2 = h.run_node(
            "station.mjs", {"base": base, "out": str(run.out / "shots")}, timeout=600
        )
        out.extend(
            res2.get("checks")
            or [
                check(
                    "station: audit script ran",
                    False,
                    res2.get("error") or res2.get("_stderr", "")[-600:],
                )
            ]
        )
    return out


SCENARIOS: dict[str, Callable[[Run], list[Check]]] = {
    "security": scen_security,
    "code_network": scen_code_network,
    "hardware": scen_hardware,
    "camera_pause": scen_camera_pause,
    "pages": scen_pages,
    "speech_settings": scen_speech_settings,
    "save": scen_save,
    "reconnect": scen_reconnect,
    "privacy": scen_privacy,
    "captions": scen_captions,
    "first_words": scen_first_words,
    "alerts": scen_alerts,
    "robustness": scen_robustness,
    "station": scen_station,
}
USES_MAIN = {
    "security",
    "hardware",
    "camera_pause",
    "pages",
    "speech_settings",
    "save",
    "reconnect",
    "privacy",
}


SOAK_TURN_MIN = (
    9.0  # one soak's turn with the GPU lock (the team's limit is 10 minutes)
)


def run_suite(names: list[str], out: Path, soak_min: float = 0.0) -> dict:
    run = Run(out)
    started = time.time()
    try:
        for name in names:
            print(f"\n== {name}", flush=True)
            run.breathe(name)
            t0 = time.monotonic()
            try:
                if name == "soak":
                    minutes = soak_min or SOAK_TURN_MIN
                    if run.lock.enabled and minutes > SOAK_TURN_MIN:
                        # the team holds the GPU lock at most 10 minutes at a time
                        print(
                            f"   soak capped at {SOAK_TURN_MIN:g} min (asked {minutes:g}): "
                            "run it again for a longer total, or set ATTUNE_E2E_NO_LOCK=1 "
                            "on a laptop nobody else uses",
                            flush=True,
                        )
                        run.notes["soak_capped_from_min"] = minutes
                        minutes = SOAK_TURN_MIN
                    checks = scen_soak(run, minutes)
                else:
                    checks = SCENARIOS[name](run)
            except Exception as exc:  # noqa: BLE001 - a broken scenario is a failed check
                traceback.print_exc()
                checks = [
                    check(
                        f"{name}: scenario ran", False, f"{type(exc).__name__}: {exc}"
                    )
                ]
            for c in checks:
                c.setdefault("scenario", name)
            run.results[name] = checks
            print(
                f"== {name}: {sum(c['ok'] for c in checks)}/{len(checks)} passed in {time.monotonic() - t0:.0f} s",
                flush=True,
            )
    finally:
        run.finish()
    summary = {
        "started": dt.datetime.fromtimestamp(started)
        .astimezone()
        .isoformat(timespec="seconds"),
        "seconds": round(time.time() - started),
        "results": run.results,
        "notes": run.notes,
    }
    (out / "results.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    return summary


def table(summary: dict) -> str:
    rows = ["| Scenario | Passed | Failed | Failing checks |", "|---|---|---|---|"]
    for name, checks in summary["results"].items():
        fails = [c["name"] for c in checks if not c["ok"]]
        rows.append(
            f"| {name} | {len(checks) - len(fails)} | {len(fails)} | {'; '.join(fails)[:400]} |"
        )
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "scenarios", nargs="*", help="scenarios to run (default: all but soak)"
    )
    ap.add_argument(
        "--soak-min",
        type=float,
        default=SOAK_TURN_MIN,
        help=f"soak length in minutes (at most {SOAK_TURN_MIN:g} while the GPU lock is used)",
    )
    ap.add_argument("--out", help="output folder")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)
    if a.list:
        print("\n".join([*SCENARIOS, "soak"]))
        return 0
    names = a.scenarios or list(SCENARIOS)
    unknown = [n for n in names if n not in SCENARIOS and n != "soak"]
    if unknown:
        ap.error(f"unknown scenario(s): {', '.join(unknown)}")
    reasons = h.prerequisites()
    if reasons:
        print("Can't run the end-to-end suite here:\n  " + "\n  ".join(reasons))
        return 2
    stamp = dt.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    out = Path(a.out) if a.out else h.out_root() / f"e2e-{stamp}"
    summary = run_suite(names, out, a.soak_min)
    print("\n" + table(summary))
    print(f"\nResults, logs and screenshots: {out}")
    return 0 if all(c["ok"] for cs in summary["results"].values() for c in cs) else 1


if __name__ == "__main__":
    sys.exit(main())

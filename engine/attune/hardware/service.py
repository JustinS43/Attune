"""HardwareService: the Arduino rig on the bus (TODO H-05, H-06).

Publishes ``sensors.levels``, ``sensors.touch``, ``touch.action``, ``hw.link`` and
``status.part``; turns ``hw.pattern`` / ``hw.stop`` / command ``pattern.test`` into serial
commands and keeps the LED matrix icon in step with alerts and pause.

A ``pattern.test`` of a repeating pattern (T3, T4) plays one cycle and then stops by itself
(H-17): the console's light-and-buzz buttons have no Stop, and an alarm pattern must never
keep buzzing after a test. A newer pattern or a real alert in the meantime keeps it going.

With ``hardware.simulate = true`` (or ``ATTUNE_SIMULATE_HARDWARE=1``) a FakeArduino runs
in-process instead of the rig; ``hw.sim_touch`` {gesture} injects a touch into it.
Without a board and without the simulator the service reports ``hw.link``
connected=false and keeps scanning; it never crashes the engine.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable

from . import protocol as p
from .common import fields, section, shared_clock
from .serial_link import SerialLink, SerialTransport, Transport, find_port
from .simulator import PATTERN_STEPS, FakeArduino
from .touch_router import TouchRouter

logger = logging.getLogger(__name__)


def one_cycle_stop_s(name: str) -> float | None:
    """When a ``pattern.test`` of `name` should be stopped: during the pause after its first
    cycle (T3 3.0 s, T4 1.2 s), or None for patterns that end by themselves."""
    steps, repeats = PATTERN_STEPS.get(name, ([], False))
    if not repeats or not steps:
        return None
    last = steps[-1][0]
    played = sum(s[0] for s in steps) - last
    return (played + min(500, last / 2)) / 1000.0


def simulate_requested(cfg: dict) -> bool:
    env = os.environ.get("ATTUNE_SIMULATE_HARDWARE", "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    if env in ("0", "false", "no", "off"):
        return False
    return bool(cfg.get("simulate", False))


class HardwareService:
    part = "hardware"

    def __init__(self, bus, config, *, connect: Callable[[], Transport | None] | None = None):
        self.bus = bus
        self.config = config
        self.cfg = section(config, "hardware")
        self._connect_override = connect
        self.simulate = simulate_requested(self.cfg)
        self.fake: FakeArduino | None = None
        self.link: SerialLink | None = None
        self.router: TouchRouter | None = None
        self.paused = False
        self.icon: str | None = None
        self._last_link: tuple | None = None
        self._unsubs: list = []
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._status_thread: threading.Thread | None = None
        self.metrics = {"levels": 0, "touches": 0, "patterns": 0, "last_levels": None}
        self._last_pat: int | None = None  # serial number of the newest PAT sent
        self._test_timer: threading.Timer | None = None

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        self.clock = shared_clock(self.config)
        self.router = TouchRouter(self.clock)
        self.link = SerialLink(
            self.cfg,
            self.clock,
            on_message=self._on_message,
            on_link=self._on_link,
            connect=self._connect,
            on_ready=self._on_ready,
        )
        for topic, handler in (
            ("hw.pattern", self._on_pattern),
            ("hw.stop", self._on_stop),
            ("command", self._on_command),
            ("alert", self._on_alert),
            ("name.proposal", self._on_proposal),
            ("paused", self._on_paused),
            ("session.forget", self._on_forget),
            ("hw.sim_touch", self._on_sim_touch),
            ("hw.sim_control", self._on_sim_control),
        ):
            self._unsubs.append(self.bus.subscribe(topic, handler))
        self._publish_link({"connected": False, "firmware": None, "driver": None})
        self._stop.clear()
        self.link.start()
        self._status_thread = threading.Thread(
            target=self._status_loop, name="hardware-status", daemon=True
        )
        self._status_thread.start()
        logger.info("hardware: started (%s)", "simulator" if self.simulate else "USB scan")

    def stop(self) -> None:
        self._stop.set()
        self._cancel_test_timer()
        for unsub in self._unsubs:
            if callable(unsub):
                try:
                    unsub()
                except Exception:
                    logger.debug("ignored error", exc_info=True)
        self._unsubs.clear()
        if self.link:
            if self.link.connected and self.link.transport is not None:
                try:  # leave the rig quiet
                    self.link.transport.write_line(p.stop(self.link.next_n()))
                except Exception:
                    logger.debug("ignored error", exc_info=True)
            self.link.stop(timeout=1.0)
        if self.fake:
            self.fake.close()
        if self._status_thread:
            self._status_thread.join(timeout=0.5)

    # ------------------------------------------------------------- simulator
    def _connect(self) -> Transport | None:
        if self._connect_override is not None:
            return self._connect_override()
        if self.simulate:
            if self.fake is None or self.fake.unplugged:
                if self.fake is not None and self.fake.unplugged:
                    return None  # stays unplugged until restarted
                self.fake = FakeArduino(driver=str(self.cfg.get("simulate_driver", "TB6612")))
            return self.fake
        port = find_port(self.cfg.get("port") or None)
        if port is None:
            return None
        return SerialTransport(port, int(self.cfg.get("baud", p.BAUD)))

    def simulate_touch(self, gesture: str) -> bool:
        """Inject a touch into the simulator (also via bus ``hw.sim_touch``)."""
        fake = self.fake
        if fake is None and self._connect_override is not None and self.link:
            fake = self.link.transport if isinstance(self.link.transport, FakeArduino) else None
        if fake is None:
            logger.info("hardware: hw.sim_touch ignored (not simulating)")
            return False
        fake.inject_touch(gesture)
        return True

    # ------------------------------------------------------------- serial -> bus
    def _on_message(self, msg: p.Message, t: float) -> None:
        if isinstance(msg, p.Levels):
            event = {"t": t, "left": msg.left, "right": msg.right, "motor_on": msg.motor_on}
            self.metrics["levels"] += 1
            self.metrics["last_levels"] = event
            self.bus.publish("sensors.levels", event)
        elif isinstance(msg, p.Touch):
            self.metrics["touches"] += 1
            self.bus.publish("sensors.touch", {"t": t, "gesture": msg.gesture})
            with self._lock:
                actions = self.router.route(msg.gesture) if self.router else []
            for topic, event in actions:
                self.bus.publish(topic, event)
            self._update_icon()

    def _on_link(self, state: dict) -> None:
        self._publish_link(state)
        if not state.get("connected"):
            self.icon = None  # resend on the next connect

    def _publish_link(self, state: dict) -> None:
        key = (bool(state.get("connected")), state.get("firmware"), state.get("driver"))
        if key == self._last_link:
            return
        self._last_link = key
        self.bus.publish("hw.link", {"connected": key[0], "firmware": key[1], "driver": key[2]})

    def _on_ready(self) -> None:
        self.icon = None
        self._update_icon()

    # ------------------------------------------------------------- bus -> serial
    def _on_pattern(self, event) -> None:
        e = fields(event)
        name = str(e.get("name", "")).upper()
        if name not in p.PATTERNS:
            logger.warning("hardware: unknown pattern %r", name)
            return
        n = self.link.pattern(name, p.side_code(e.get("side"))) if self.link else None
        if n is not None:
            self.metrics["patterns"] += 1
            self._last_pat = n

    def _on_stop(self, _event=None) -> None:
        self._cancel_test_timer()
        if self.link:
            self.link.stop_all()

    def _on_command(self, event) -> None:
        e = fields(event)
        if e.get("name") == "pattern.test":
            args = e.get("args") or {}
            name = str(args.get("name", "")).upper()
            self._on_pattern({"name": name, "side": args.get("side", "B")})
            after = one_cycle_stop_s(name)
            if after is not None and self._last_pat is not None:
                self._arm_test_stop(self._last_pat, after)

    def _arm_test_stop(self, n: int, after: float) -> None:
        self._cancel_test_timer()
        timer = threading.Timer(after, self._end_test, args=(n,))
        timer.daemon = True
        timer.name = "hardware-test-stop"
        self._test_timer = timer
        timer.start()

    def _cancel_test_timer(self) -> None:
        timer, self._test_timer = self._test_timer, None
        if timer is not None:
            timer.cancel()

    def _end_test(self, n: int) -> None:
        """One cycle of a test pattern has played: stop it, unless something newer is on."""
        if self._stop.is_set() or self._last_pat != n or not self.link:
            return
        with self._lock:
            alert = self.router.current_alert() if self.router else None
        if alert is not None:
            return  # a real alert took over the rig; its "Got it" stops it
        self.link.stop_all()
        logger.info("hardware: test pattern played one cycle; stopped")

    def _on_alert(self, event) -> None:
        with self._lock:
            if self.router:
                self.router.on_alert(fields(event))
        self._update_icon()

    def _on_proposal(self, event) -> None:
        with self._lock:
            if self.router:
                self.router.on_proposal(fields(event))

    def _on_paused(self, event) -> None:
        self.paused = bool(fields(event).get("paused"))
        self._update_icon()

    def _on_forget(self, _event=None) -> None:
        with self._lock:
            if self.router:
                self.router.clear()
        self._update_icon()

    def _on_sim_touch(self, event) -> None:
        gesture = fields(event).get("gesture", "tap")
        try:
            self.simulate_touch(str(gesture))
        except ValueError as exc:
            logger.warning("hardware: %s", exc)

    def _on_sim_control(self, event) -> None:
        """Simulator only: ``heartbeat`` (bool) stops or resumes the laptop's HB lines, to
        exercise the board's 2 s safety stop; ``sound`` {left, right} adds a steady loudness
        to each sensor (0..1023), so an alert has a direction; ``reboot`` (true) resets the
        board as a brown-out would."""
        if not self.simulate:
            logger.info("hardware: hw.sim_control ignored (not simulating)")
            return
        e = fields(event)
        if "heartbeat" in e and self.link:
            self.link.send_heartbeat = bool(e["heartbeat"])
            logger.info("hardware: simulator heartbeat %s", "on" if e["heartbeat"] else "off")
        if e.get("reboot") is True and self.fake is not None:
            logger.info("hardware: simulator reboot")
            self.fake.reboot()
        sound = e.get("sound")
        if isinstance(sound, dict) and self.fake is not None:
            try:
                left = min(1023.0, max(0.0, float(sound.get("left", 0))))
                right = min(1023.0, max(0.0, float(sound.get("right", 0))))
            except (TypeError, ValueError):
                logger.warning("hardware: bad simulated sound %r", sound)
                return
            self.fake.inject_sound(left, right)

    # ------------------------------------------------------------- matrix icon
    def wanted_icon(self) -> str:
        if self.paused:
            return "PAUSE"
        with self._lock:
            alert = self.router.current_alert() if self.router else None
        if alert is not None:
            side = p.side_code(alert[1].get("side"))
            return {"L": "ALERT_L", "R": "ALERT_R"}.get(side, "ALERT_B")
        return "HEART"

    def _update_icon(self) -> None:
        if not self.link or not self.link.connected:
            return
        icon = self.wanted_icon()
        if icon != self.icon and self.link.send(p.mx(icon)):
            self.icon = icon

    # ------------------------------------------------------------- status
    def status(self) -> dict:
        link = self.link.state if self.link else {"connected": False}
        metrics = {
            "connected": bool(link.get("connected")),
            "port": link.get("port"),
            "firmware": link.get("firmware"),
            "driver": link.get("driver"),
            "simulate": self.simulate,
            "icon": self.icon,
            **{k: v for k, v in self.metrics.items() if k != "last_levels"},
        }
        if self.link:
            metrics.update(self.link.metrics)
            metrics["pending_acks"] = len(self.link.pending)
            metrics["heartbeat_out"] = self.link.send_heartbeat
        if self.simulate and self.fake is not None:
            metrics["sim"] = self.fake.snapshot()
        last = self.metrics["last_levels"]
        if last:
            metrics["left"], metrics["right"] = last["left"], last["right"]
        connected = bool(link.get("connected"))
        if connected:
            detail = f"{'simulator' if self.simulate else link.get('port')} {link.get('driver')}"
        else:
            detail = (self.link.last_error if self.link else "") or "no Arduino found; scanning"
        return {"part": self.part, "ok": connected, "detail": detail, "metrics": metrics}

    def _status_loop(self) -> None:
        while not self._stop.wait(1.0):
            try:
                self.bus.publish("status.part", self.status())
            except Exception:
                logger.exception("hardware: status publish failed")

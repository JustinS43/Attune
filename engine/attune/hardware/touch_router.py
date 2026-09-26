"""Decides what a touch on the side of the glasses means right now (plan section 05, 'Touch priority').

Gesture map (TODO H-06, P-29):
- tap = yes: acknowledge the active alert, else confirm the newest pending name proposal.
- hold = no: the same targets, answered no (dismiss / reject).
- double tap = save this person: ``touch.action`` {target: save, id: <proposal_id or None>}.
  The engine's save flow (core/save_flow.py) picks who: that proposal's face, else the named
  speaker, else the most prominent named face, and asks them for consent on the phone.
  A double tap never answers an alert.
- triple tap = pause or resume recognition.

The router is pure logic: :meth:`TouchRouter.route` returns ``(topic, event)`` pairs for
the caller to publish, which keeps it easy to test.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

ACTIVE_ALERT_STATES = {"start", "update"}
ENDED_ALERT_STATES = {"acknowledged", "clear"}


class TouchRouter:
    def __init__(self, clock: Callable[[], float]):
        self.clock = clock
        self.alerts: dict[str, dict] = {}  # alert_id -> {kind, side, t}
        self.proposals: dict[str, dict] = {}  # proposal_id -> {name, expires_t, t}
        self.last: dict | None = None  # the last routing decision, for status/debug

    # --------------------------------------------------------- tracking
    def on_alert(self, e: dict) -> None:
        alert_id, state = e.get("alert_id"), e.get("state")
        if not alert_id:
            return
        if state in ACTIVE_ALERT_STATES:
            known = self.alerts.get(alert_id)
            self.alerts[alert_id] = {
                "kind": e.get("kind"),
                "side": e.get("side"),
                "t": known["t"] if known else self.clock(),
            }
        elif state in ENDED_ALERT_STATES:
            self.alerts.pop(alert_id, None)

    def on_proposal(self, e: dict) -> None:
        proposal_id = e.get("proposal_id")
        if not proposal_id:
            return
        if e.get("state") == "proposed":
            self.proposals[proposal_id] = {
                "name": e.get("name"),
                "expires_t": e.get("expires_t"),
                "t": self.clock(),
            }
        else:  # confirmed, rejected, expired
            self.proposals.pop(proposal_id, None)

    def clear(self) -> None:
        """session.forget: drop everything the router remembers."""
        self.alerts.clear()
        self.proposals.clear()

    def current_alert(self) -> tuple[str, dict] | None:
        if not self.alerts:
            return None
        return max(self.alerts.items(), key=lambda kv: kv[1]["t"])

    def current_proposal(self) -> tuple[str, dict] | None:
        now = self.clock()
        for key, value in list(self.proposals.items()):
            expires = value.get("expires_t")
            if isinstance(expires, (int, float)) and expires <= now:
                self.proposals.pop(key)
        if not self.proposals:
            return None
        return max(self.proposals.items(), key=lambda kv: kv[1]["t"])

    # --------------------------------------------------------- routing
    def route(self, gesture: str) -> list[tuple[str, dict]]:
        """Turn one raw gesture into bus events, following the priority rules."""
        gesture = gesture.lower()
        if gesture == "triple":
            self.last = {"gesture": gesture, "target": "pause"}
            return [
                ("touch.action", {"target": "pause", "id": None, "accept": True}),
                ("command", {"name": "pause.toggle", "args": {}}),
            ]
        if gesture == "double":
            # the pending proposal (if any) is who to save; the save flow confirms its name
            proposal = self.current_proposal()
            proposal_id = proposal[0] if proposal is not None else None
            if proposal_id is not None:
                self.proposals.pop(proposal_id, None)
            self.last = {"gesture": gesture, "target": "save", "id": proposal_id}
            return [("touch.action", {"target": "save", "id": proposal_id, "accept": True})]
        if gesture not in ("tap", "hold"):
            logger.debug("touch router: unknown gesture %r", gesture)
            return []
        accept = gesture == "tap"
        alert = self.current_alert()
        if alert is not None:
            alert_id, _ = alert
            # Stop routing further touches to it before the 'acknowledged' event returns.
            self.alerts.pop(alert_id, None)
            self.last = {"gesture": gesture, "target": "alert", "id": alert_id}
            return [("touch.action", {"target": "alert", "id": alert_id, "accept": accept})]
        proposal = self.current_proposal()
        if proposal is not None:
            proposal_id, _ = proposal
            self.proposals.pop(proposal_id, None)
            self.last = {"gesture": gesture, "target": "name", "id": proposal_id}
            return [("touch.action", {"target": "name", "id": proposal_id, "accept": accept})]
        self.last = {"gesture": gesture, "target": None}
        return []

"""Known faces and the rules for putting a name on a face.

Section 1 - Vision. TODO: V-06. Plan: section 05 "Faces" (match rules).

Three kinds of people:
- Manual: saved through enrollment with the person's consent.
- Automatic: saved after a visible face is attributed conversation, unnamed at first.
  Both persistent kinds live in data/people/<person_id>/ (gitignored).
- Session: named only in memory; "forget session" wipes them.

Match rules (per track):
- A name appears only when the best person scores >= threshold (0.45) and
  beats the runner-up by >= margin (0.08), three times within 1 s.
- Named faces are rechecked every 2 s; after three failed rechecks the tag
  goes back to "unknown".
- A tag never jumps straight from one name to another: a different person
  winning counts as a failed recheck.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class Person:
    person_id: str
    name: str
    prints: np.ndarray  # (n, 512), unit length
    enrolled: bool
    consent_t: str | None = None  # wall-clock ISO time the person ticked consent
    source: str = "manual"  # manual or auto; automatic profiles have no consent record
    tier: str = "other"  # close, familiar, other
    seen_count: int = 0
    last_seen_t: float = 0.0
    name_evidence: dict[str, dict] = field(default_factory=dict)
    blocked_names: list[str] = field(default_factory=list)


class Gallery:
    def __init__(self, people_dir: str):
        self.people_dir = people_dir
        self._people: dict[str, Person] = {}
        self._lock = threading.Lock()
        self._matrix: np.ndarray | None = None
        self._owners: list[str] = []

    # ---- storage ----
    def load(self) -> None:
        if not os.path.isdir(self.people_dir):
            return
        for pid in sorted(os.listdir(self.people_dir)):
            folder = os.path.join(self.people_dir, pid)
            meta_path = os.path.join(folder, "meta.json")
            prints_path = os.path.join(folder, "face.npy")
            if not (os.path.isfile(meta_path) and os.path.isfile(prints_path)):
                continue
            with open(meta_path, encoding="utf-8") as fh:
                meta = json.load(fh)
            prints = np.load(prints_path).astype(np.float32)
            self._people[pid] = Person(
                pid,
                meta["name"],
                prints,
                True,
                meta.get("consent_t"),
                meta.get("source", "manual"),
                meta.get("tier", "close" if meta.get("source", "manual") == "manual" else "other"),
                int(meta.get("seen_count", 0)),
                float(meta.get("last_seen_t", 0)),
                meta.get("name_evidence") or {},
                meta.get("blocked_names") or [],
            )
        self._rebuild()
        log.info("Loaded %d enrolled people", len(self._people))

    def _save(self, person: Person, *, prints: bool = True) -> None:
        folder = os.path.join(self.people_dir, person.person_id)
        os.makedirs(folder, exist_ok=True)
        if prints:
            np.save(os.path.join(folder, "face.npy"), person.prints)
        with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "name": person.name,
                    "consent_t": person.consent_t,
                    "source": person.source,
                    "tier": person.tier,
                    "seen_count": person.seen_count,
                    "last_seen_t": person.last_seen_t,
                    "name_evidence": person.name_evidence,
                    "blocked_names": person.blocked_names,
                },
                fh,
                indent=2,
            )

    # ---- changes ----
    def enroll(self, name: str, prints: np.ndarray, consent_t: str) -> Person:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "person"
        person = Person(
            f"{slug}-{uuid.uuid4().hex[:6]}",
            name,
            prints.astype(np.float32),
            True,
            consent_t,
            "manual",
            "close",
        )
        with self._lock:
            self._people[person.person_id] = person
            self._save(person)
            self._rebuild()
        return person

    def add_session(self, name: str, prints: np.ndarray) -> Person:
        person = Person(f"session-{uuid.uuid4().hex[:6]}", name, prints.astype(np.float32), False)
        with self._lock:
            self._people[person.person_id] = person
            self._rebuild()
        return person

    def remember_auto(
        self, prints: np.ndarray, now: float | None = None
    ) -> tuple[Person | None, str | None]:
        """Keep one engaged stranger, evicting only the least encountered automatic profile."""
        now = time.time() if now is None else now
        with self._lock:
            saved = [p for p in self._people.values() if p.enrolled]
            removed = None
            if len(saved) >= 150:
                choices = [p for p in saved if p.source == "auto" and p.tier != "close"]
                if not choices:
                    return None, None
                victim = min(choices, key=lambda p: (p.seen_count, p.last_seen_t))
                removed = victim.person_id
                del self._people[removed]
                shutil.rmtree(os.path.join(self.people_dir, removed), ignore_errors=True)
            pid = f"auto-{uuid.uuid4().hex[:12]}"
            person = Person(
                pid, "New person", prints.astype(np.float32), True, None, "auto", "other", 1, now
            )
            self._people[pid] = person
            self._save(person)
            self._rebuild()
            return person, removed

    def encounter(self, person_id: str, now: float | None = None) -> Person | None:
        """Count encounters at most once per hour and promote frequent automatic contacts."""
        now = time.time() if now is None else now
        with self._lock:
            person = self._people.get(person_id)
            if person is None or not person.enrolled:
                return None
            if now - person.last_seen_t >= 3600:
                person.seen_count += 1
                person.last_seen_t = now
                if person.source == "auto" and person.tier == "other" and person.seen_count >= 5:
                    person.tier = "familiar"
                self._save(person, prints=False)
            return person

    def set_tier(self, person_id: str, tier: str) -> Person | None:
        """Pin a close contact or return one to automatic ranking."""
        if tier not in {"close", "familiar", "other"}:
            return None
        with self._lock:
            person = self._people.get(person_id)
            if person is None or not person.enrolled:
                return None
            person.tier = tier
            self._save(person, prints=False)
            return person

    def note_name(self, person_id: str, name: str, utt_id: str, now: float | None = None) -> bool:
        """Learn an automatic person's name only after five uses on at least two days."""
        if not isinstance(name, str) or not (1 < len(name) <= 24 and name.isalpha()):
            return False
        now = time.time() if now is None else now
        name = name.title()
        key = name.casefold()
        with self._lock:
            person = self._people.get(person_id)
            if person is None or person.source != "auto" or person.name != "New person":
                return False
            if key in person.blocked_names:
                return False
            evidence = person.name_evidence.setdefault(
                key, {"name": name, "count": 0, "days": [], "utts": []}
            )
            if utt_id in evidence["utts"]:
                return False
            evidence["utts"] = (evidence["utts"] + [utt_id])[-32:]
            evidence["count"] = min(100, int(evidence["count"]) + 1)
            day = datetime.fromtimestamp(now, UTC).date().isoformat()
            if day not in evidence["days"]:
                evidence["days"].append(day)
            if len(person.name_evidence) > 3:
                weakest = min(person.name_evidence, key=lambda k: person.name_evidence[k]["count"])
                del person.name_evidence[weakest]
            competing = max(
                (v["count"] for k, v in person.name_evidence.items() if k != key), default=0
            )
            ready = (
                evidence["count"] >= 5
                and len(evidence["days"]) >= 2
                and evidence["count"] >= competing + 3
            )
            if ready:
                person.name = name
                person.name_evidence.clear()
            self._save(person, prints=False)
            return ready

    def reject_name(self, person_id: str, name: str) -> None:
        """A rejected suggestion is never later promoted by accumulated evidence."""
        with self._lock:
            person = self._people.get(person_id)
            if person is None or person.source != "auto":
                return
            key = name.casefold()
            person.name_evidence.pop(key, None)
            if key not in person.blocked_names:
                person.blocked_names.append(key)
            person.blocked_names = person.blocked_names[-16:]
            self._save(person, prints=False)

    def rename(self, person_id: str, name: str) -> Person | None:
        with self._lock:
            person = self._people.get(person_id)
            if person is None:
                return None
            person.name = name
            if person.source == "auto" and name != "New person":
                person.name_evidence.clear()
            if person.enrolled:
                self._save(person, prints=False)
            return person

    def delete(self, person_id: str) -> Person | None:
        with self._lock:
            person = self._people.pop(person_id, None)
            if person is not None and person.enrolled:
                shutil.rmtree(os.path.join(self.people_dir, person_id), ignore_errors=True)
            self._rebuild()
            return person

    def forget_session(self) -> list[str]:
        with self._lock:
            gone = [pid for pid, p in self._people.items() if not p.enrolled]
            for pid in gone:
                del self._people[pid]
            self._rebuild()
            return gone

    # ---- reads ----
    def get(self, person_id: str | None) -> Person | None:
        return self._people.get(person_id) if person_id else None

    def people(self, enrolled_only: bool = True) -> list[Person]:
        return [p for p in self._people.values() if p.enrolled or not enrolled_only]

    def _rebuild(self) -> None:
        owners, rows = [], []
        for pid, person in self._people.items():
            owners += [pid] * len(person.prints)
            rows.append(person.prints)
        self._owners = owners
        self._matrix = np.concatenate(rows) if rows else None

    def match(self, embedding: np.ndarray) -> tuple[str | None, float, float]:
        """Best person, their score (best print), and the runner-up person's score."""
        matrix, owners = self._matrix, self._owners
        if matrix is None:
            return None, 0.0, 0.0
        sims = matrix @ embedding
        best: dict[str, float] = {}
        for pid, s in zip(owners, sims):
            if s > best.get(pid, -1.0):
                best[pid] = float(s)
        ranked = sorted(best.items(), key=lambda kv: -kv[1])
        top_pid, top = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        return top_pid, top, second


@dataclass
class Identity:
    """Naming state for one track."""

    person_id: str | None = None
    match_score: float = 0.0
    hits: list[tuple[float, str]] = field(default_factory=list)
    last_check_t: float = -1e9
    fails: int = 0
    proposal: tuple[str, str] | None = None  # (proposal_id, name) while "Sam?" is pending


class IdentityRules:
    def __init__(
        self, gallery: Gallery, threshold=0.45, margin=0.08, hits=3, recheck_s=2.0, fails=3
    ):
        self.gallery = gallery
        self.threshold = threshold
        self.margin = margin
        self.hits = hits
        self.recheck_s = recheck_s
        self.fails = fails

    def needs_check(self, ident: Identity, t: float) -> bool:
        if ident.person_id is None:
            return True
        return t - ident.last_check_t >= self.recheck_s

    def observe(self, ident: Identity, embedding: np.ndarray, t: float) -> bool:
        """Apply one face-print check. Returns True if the track's name changed."""
        ident.last_check_t = t
        pid, score, second = self.gallery.match(embedding)
        passes = pid is not None and score >= self.threshold and score - second >= self.margin

        if ident.person_id is not None:
            if self.gallery.get(ident.person_id) is None:  # deleted meanwhile
                self.clear(ident)
                return True
            if passes and pid == ident.person_id:
                ident.fails = 0
                ident.match_score = score
                return False
            ident.fails += 1
            if ident.fails >= self.fails:
                self.clear(ident)
                return True
            return False

        ident.hits = [(ht, hp) for ht, hp in ident.hits if t - ht <= 1.0]
        if passes:
            ident.hits.append((t, pid))
            if sum(1 for _, hp in ident.hits if hp == pid) >= self.hits:
                ident.person_id = pid
                ident.match_score = score
                ident.fails = 0
                ident.hits.clear()
                return True
        return False

    def assign(self, ident: Identity, person_id: str, score: float, t: float) -> None:
        """Name a track straight away (after enrollment or a confirmed introduction)."""
        ident.person_id = person_id
        ident.match_score = score
        ident.fails = 0
        ident.hits.clear()
        ident.last_check_t = t
        ident.proposal = None

    @staticmethod
    def clear(ident: Identity) -> None:
        ident.person_id = None
        ident.match_score = 0.0
        ident.fails = 0
        ident.hits.clear()

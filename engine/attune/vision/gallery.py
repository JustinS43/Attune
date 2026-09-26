"""Known faces and the rules for putting a name on a face.

Section 1 - Vision. TODO: V-06. Plan: section 05 "Faces" (match rules).

Two kinds of people:
- Enrolled: consented in the console. Face prints and the consent record are
  saved in data/people/<person_id>/ (gitignored) and survive restarts.
  Delete removes the whole folder.
- Session: named by "tap to confirm" after an introduction. Memory only;
  "forget session" wipes them.

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
import uuid
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class Person:
    person_id: str
    name: str
    prints: np.ndarray  # (n, 512), unit length
    enrolled: bool
    consent_t: str | None = None  # wall-clock ISO time the person ticked consent


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
            self._people[pid] = Person(pid, meta["name"], prints, True, meta.get("consent_t"))
        self._rebuild()
        log.info("Loaded %d enrolled people", len(self._people))

    def _save(self, person: Person) -> None:
        folder = os.path.join(self.people_dir, person.person_id)
        os.makedirs(folder, exist_ok=True)
        np.save(os.path.join(folder, "face.npy"), person.prints)
        with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as fh:
            json.dump({"name": person.name, "consent_t": person.consent_t}, fh, indent=2)

    # ---- changes ----
    def enroll(self, name: str, prints: np.ndarray, consent_t: str) -> Person:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "person"
        person = Person(
            f"{slug}-{uuid.uuid4().hex[:6]}", name, prints.astype(np.float32), True, consent_t
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

    def rename(self, person_id: str, name: str) -> Person | None:
        with self._lock:
            person = self._people.get(person_id)
            if person is None:
                return None
            person.name = name
            if person.enrolled:
                self._save(person)
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

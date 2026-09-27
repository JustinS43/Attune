"""Cloud speaker tags in who's talking (V-32): bind tags to faces, split captions at a tag change.

Section 1 - Vision (fusion). TODO: V-32. Contracts: "Cloud captions" in docs/contracts.md.
Design: docs/cloud-diarization.md. Only used while the wearer has cloud captions on.

Cloud captions (Section 2, `speaker.cloud`) label the words with speaker tags from Google's
streaming diarization. A tag is evidence about one stream only: a restart numbers the voices
afresh, and Google revises tags as it hears more. So:

- **Cloud speakers.** Each (stream, tag) maps to a *cloud speaker*, the thing fusion binds. A
  new stream starts with audio the old one already heard, so a new tag whose words overlap an
  old tag's words for `bridge_min_s` takes over that tag's cloud speaker (and with it the face).
- **Binding.** Every tick `note` records which visible faces are talking: Light-ASD's verdict
  where it is fresh, else the lip checks (a probable-band face counts half); the wearer too,
  when "You" is decided. A cloud speaker binds to the face whose talking overlaps its words
  (read `talk_lag_s` later: the face evidence lags) for at least `bind_min_s`, and for
  `bind_share` of its words that any face was talking over. Pairs are made strongest first, one
  face per cloud speaker. A binding holds until another face has `rebind_ratio` times the
  evidence, and remembers the face's person, so the same person seen again as a new track keeps
  it. Only the newest stream's voices hold faces against each other; an older stream's voice
  keeps its face for its old words but blocks nobody.
- **Words.** `evidence` gives each caption word the cloud speaker of the cloud word nearest its
  middle (within `word_match_s`). A bound speaker's words go to its face, or, with the face out
  of view, to the dock under that face's label and exit side (words the face said while in view
  keep it). An unbound speaker's words in a caption all go to one place: the face the local
  evidence gives most of them, unless that face is another voice's; else a face in view the
  talk log has talking during them (`bind_share` / 2 of their time; "face" from `bind_share`);
  else the off-screen voice the voice prints name; else their own dock bubble, "Someone"
  (person id `session-cloud-N`: session-only, wiped by forget session like any stranger).
- **Turns.** `protect` locks a caption piece of one cloud speaker between pieces of others once
  it lasts `turn_min_s` (inside one other voice's speech, also two words: a single wrongly
  tagged word in someone's sentence is not a turn). The smoothing that folds short pieces into
  a neighbour leaves it, and `placed` tells `_snap` not to move a change the cloud put there.
  The drafts carry no tags (Google tags final results), so a piece already shown is cut where
  the final's tags hear another voice that long: a reply in the middle of it gets its own
  bubble.
- **Finals.** While the state is "on", `wait` holds a final caption up to `final_wait_ms` until
  the cloud has heard past its last word (the draft stays on screen). In any other state it
  never waits. A final caption is never changed after it is sent: later tags only improve the
  bindings for the next words.

Pure logic with explicit times, like SpeakerFusion; FusionService feeds it on the bus.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, fields, replace

from ..vision.types import Speaker, get
from .harvest import voice_id

YOU = -1  # the wearer, as a "face" in the talk log
_MEMO_MAX = 5000  # cached per-word face evidence entries before the cache starts over


@dataclass
class CloudTagSettings:
    """The [cloud] keys fusion uses (the audio client reads its own keys from the same table)."""

    final_wait_ms: float = 1200.0
    word_match_s: float = 0.25
    talk_lag_s: float = 0.3
    bind_min_s: float = 0.6
    bind_share: float = 0.6
    rebind_ratio: float = 2.0
    turn_min_s: float = 0.3
    bridge_min_s: float = 0.5
    memory_s: float = 90.0

    @classmethod
    def from_config(cls, table: dict | None) -> CloudTagSettings:
        known = {f.name for f in fields(cls)}
        out = cls()
        for k, v in (table or {}).items():
            if k in known:
                try:
                    setattr(out, k, float(v))
                except (TypeError, ValueError):
                    continue  # a malformed value keeps the default
        return out


@dataclass
class _Word:
    stream: str
    word: str
    t0: float
    t1: float
    tag: str


@dataclass
class _CloudSpeaker:
    """One voice as the cloud hears it, across streams."""

    n: int
    track: int | None = None  # the face it is bound to (YOU for the wearer)
    person: str | None = None  # that face's voice id (person_id or "track-N"): outlives the track
    label: str = ""
    votes: float = 0.0  # the most evidence the binding has had


def _span(t0: float, t1: float) -> float:
    return max(t1 - t0, 0.05)


def _mid(w) -> float:
    return (float(w[1]) + float(w[2])) / 2


def _norm(text: str) -> str:
    return "".join(c for c in text.lower() if c.isalnum() or c == "'")


def _most(words: list, evidence: list[Speaker]) -> Speaker:
    """The speaker the evidence gives most of these words' time."""
    share: dict[tuple, float] = {}
    first: dict[tuple, Speaker] = {}
    for w, spk in zip(words, evidence):
        k = (spk.kind, spk.track_id, spk.person_id, spk.label)
        share[k] = share.get(k, 0.0) + _span(float(w[1]), float(w[2]))
        first.setdefault(k, spk)
    return first[max(share, key=share.get)]


class CloudTags:
    def __init__(self, settings: CloudTagSettings | None = None):
        self.s = settings or CloudTagSettings()
        self.state = "off"
        self._n = 0  # cloud speaker numbers are never reused (dock bubbles carry them)
        self.forget()

    def forget(self) -> None:
        """Drop every tag, binding and face note (forget session, or cloud captions turned off)."""
        self.words: dict[str, dict[int, _Word]] = {}  # stream -> word start (10 ms) -> word
        self.order: list[str] = []  # streams, oldest first
        self.heard: dict[str, float] = {}  # stream -> t_end: audio the cloud has heard
        self.speaker_of: dict[tuple[str, str], int] = {}  # (stream, tag) -> cloud speaker
        self.speakers: dict[int, _CloudSpeaker] = {}
        self._talk_t: list[float] = []  # tick times...
        self._talk_f: list[dict[int, float]] = []  # ...and the faces talking then (strength)
        self._memo: dict[tuple[int, int], dict[int, float]] = {}
        self._index: list[_Word] | None = None  # every word by start time (rebuilt lazily)
        self._starts: list[float] = []
        self._finals: dict[str, float] = {}  # utt_id -> when its final started waiting
        self._bound_at: float | None = None

    # ------------------------------------------------------------------ inputs
    def on_state(self, ev) -> None:
        """`cloud.state`. Turned off: nothing the cloud said before is used again."""
        self.state = str(get(ev, "state", "off"))
        if not get(ev, "enabled", False):
            self.forget()

    def on_words(self, ev, now: float) -> None:
        """`speaker.cloud`: new or re-tagged words of one stream."""
        stream = str(get(ev, "stream_id", ""))
        table = self.words.get(stream)
        if table is None:
            table = self.words[stream] = {}
            self.order.append(stream)
        for item in get(ev, "words") or []:
            w, t0, t1, tag = item[:4]
            table[round(float(t0) * 100)] = _Word(stream, str(w), float(t0), float(t1), str(tag))
        t_end = float(get(ev, "t_end", 0.0) or 0.0)
        self.heard[stream] = max(self.heard.get(stream, t_end), t_end)
        self._index = None
        self._bound_at = None
        self._trim(now)
        # this stream's tags, and those of any newer stream (an old stream's last results can
        # arrive after the next stream started: its tags may bridge differently now)
        if stream in self.order:
            for s in self.order[self.order.index(stream) :]:
                self._map_tags(s)

    def note(self, fusion, now: float, current: Speaker | None) -> None:
        """Which faces are talking this tick (SpeakerFusion.tick calls this after deciding)."""
        if self.state == "off":
            return
        if self._talk_t and now <= self._talk_t[-1]:
            return
        covered = fusion.asd_gate.covered(fusion, now)
        faces: dict[int, float] = {}
        for tid, tr in fusion.tracks.items():
            if now - tr.t > 0.5:
                continue
            if tid in covered:
                strength = 1.0 if covered[tid].talking else 0.0
            else:
                strength = 1.0 if tr.talking else 0.5 if tr.probable else 0.0
            if strength:
                faces[tid] = strength
        if current is not None and current.kind == "you":
            faces[YOU] = 1.0
        self._talk_t.append(now)
        self._talk_f.append(faces)
        k = bisect_left(self._talk_t, now - self.s.memory_s)
        if k:
            del self._talk_t[:k], self._talk_f[:k]
        if self._bound_at is None and self.words:
            self.bind(fusion, now)  # new tags: bind while the faces are still in view

    # ------------------------------------------------------------------ tags -> cloud speakers
    def _trim(self, now: float) -> None:
        cutoff = now - self.s.memory_s
        for stream in list(self.order):
            table = self.words[stream]
            for key in [k for k, w in table.items() if w.t1 < cutoff]:
                del table[key]
            if not table and stream != self.order[-1]:
                self.order.remove(stream)
                del self.words[stream]
                self.heard.pop(stream, None)
                for k in [k for k in self.speaker_of if k[0] == stream]:
                    del self.speaker_of[k]
        used = set(self.speaker_of.values())
        for n in [n for n in self.speakers if n not in used]:
            del self.speakers[n]
        if len(self._memo) > _MEMO_MAX:
            self._memo.clear()
        for utt in [u for u, t in self._finals.items() if now - t > 30.0]:
            del self._finals[utt]

    def _new_speaker(self) -> int:
        self._n += 1
        self.speakers[self._n] = _CloudSpeaker(self._n)
        return self._n

    def _map_tags(self, stream: str) -> None:
        """Give each tag of `stream` a cloud speaker: an older one it overlaps (restart), or new.

        The new stream re-hears the old one's last audio, so the same words come back at the
        same moments: each tag votes for the old cloud speakers of the words it shares (same
        text, middles within word_match_s). Pairs are made strongest first, one to one, from
        `bridge_min_s` of shared words; a tag with none keeps its cloud speaker, or gets a new one.
        """
        tags: dict[str, list[_Word]] = {}
        for w in self.words[stream].values():
            tags.setdefault(w.tag, []).append(w)
        pos = self.order.index(stream)
        older = sorted(
            (w for s in self.order[:pos] for w in self.words[s].values()), key=lambda w: w.t0
        )
        starts = [w.t0 for w in older]
        tol = self.s.word_match_s
        votes: dict[tuple[str, int], float] = {}
        for tag, mine in tags.items():
            for w in mine:
                mid, text = (w.t0 + w.t1) / 2, _norm(w.word)
                for o in older[bisect_left(starts, w.t0 - 5.0) : bisect_right(starts, w.t1 + tol)]:
                    m = self.speaker_of.get((o.stream, o.tag))
                    if m is None or abs((o.t0 + o.t1) / 2 - mid) > tol or _norm(o.word) != text:
                        continue
                    both = min(_span(o.t0, o.t1), _span(w.t0, w.t1))
                    votes[(tag, m)] = votes.get((tag, m), 0.0) + both
        bridged: dict[str, int] = {}
        taken: set[int] = set()
        for (tag, m), v in sorted(votes.items(), key=lambda kv: -kv[1]):
            if v >= self.s.bridge_min_s and tag not in bridged and m not in taken:
                bridged[tag] = m  # the same voice as before the restart
                taken.add(m)
        by_length = sorted(tags, key=lambda tag: -sum(_span(w.t0, w.t1) for w in tags[tag]))
        for tag in by_length:
            key = (stream, tag)
            if tag in bridged:
                self.speaker_of[key] = bridged[tag]
                continue
            current = self.speaker_of.get(key)
            if current is None or current in taken:
                self.speaker_of[key] = self._new_speaker()
            taken.add(self.speaker_of[key])

    def _live(self) -> set[int]:
        """The cloud speakers of the newest stream that has words."""
        for stream in reversed(self.order):
            if self.words[stream]:
                return {n for (s, _), n in self.speaker_of.items() if s == stream}
        return set()

    # ------------------------------------------------------------------ binding to faces
    def _talking(self, t0: float, t1: float) -> dict[int, float]:
        """Seconds each face was talking over [t0, t1] (the face evidence read talk_lag_s later)."""
        if not self._talk_t:
            return {}
        a, b = t0 + self.s.talk_lag_s, t1 + self.s.talk_lag_s
        key = (round(t0 * 100), round(t1 * 100))
        settled = self._talk_t[-1] >= b + 0.1
        if settled and key in self._memo:
            return self._memo[key]
        lo, hi = bisect_left(self._talk_t, a), bisect_right(self._talk_t, b)
        if hi <= lo:  # a word shorter than a tick: the nearest tick
            mid = (a + b) / 2
            i = bisect_left(self._talk_t, mid)
            near = [j for j in (i - 1, i) if 0 <= j < len(self._talk_t)]
            j = min(near, key=lambda j: abs(self._talk_t[j] - mid))
            if abs(self._talk_t[j] - mid) > 0.1:
                return {}
            lo, hi = j, j + 1
        total: dict[int, float] = {}
        for faces in self._talk_f[lo:hi]:
            for tid, v in faces.items():
                total[tid] = total.get(tid, 0.0) + v
        span = _span(t0, t1)
        out = {tid: v / (hi - lo) * span for tid, v in total.items()}
        if settled:
            self._memo[key] = out
        return out

    def _face_now(self, fusion, spk: _CloudSpeaker, now: float) -> int | None:
        """The track showing this cloud speaker's face (or person) now, if any."""
        if spk.track is None or spk.track == YOU:
            return None
        tr = fusion.tracks.get(spk.track)
        if tr is not None and now - tr.t <= 0.5:
            return spk.track
        if spk.person and not spk.person.startswith("track-"):
            for tid, tr in fusion.tracks.items():
                if now - tr.t <= 0.5 and tr.person_id == spk.person:
                    return tid
        return None

    def bind(self, fusion, now: float) -> None:
        """Bind cloud speakers to faces from the words kept and the faces heard talking."""
        if self._bound_at == now:
            return
        self._bound_at = now
        live = self._live()
        votes: dict[int, dict[int, float]] = {}
        matched: dict[int, float] = {}
        for stream in self.order:
            for w in self.words[stream].values():
                n = self.speaker_of.get((stream, w.tag))
                talking = self._talking(w.t0, w.t1) if n is not None else None
                if not talking:
                    continue
                matched[n] = matched.get(n, 0.0) + _span(w.t0, w.t1)
                mine = votes.setdefault(n, {})
                for tid, v in talking.items():
                    mine[tid] = mine.get(tid, 0.0) + v
        taken: set[int] = set()  # faces held by the newest stream's voices
        done: set[int] = set()
        for n in sorted(self.speakers, key=lambda n: (n not in live, n)):
            spk = self.speakers[n]
            if spk.track is None:
                continue
            if n not in live:
                done.add(n)  # an older stream's voice keeps its face for its old words
                continue
            faces = votes.get(n, {})
            held = faces.get(spk.track, 0.0)
            rival, rival_v = None, 0.0
            for tid, v in faces.items():
                if tid != spk.track and tid not in taken and v > rival_v:
                    rival, rival_v = tid, v
            if spk.track in taken or (
                rival is not None
                and rival_v >= self.s.bind_min_s
                and rival_v >= self.s.rebind_ratio * max(held, 0.5 * spk.votes)
            ):
                spk.track, spk.person, spk.label, spk.votes = None, None, "", 0.0
                continue
            spk.votes = max(spk.votes, held)
            taken.add(spk.track)
            seen = self._face_now(fusion, spk, now)
            if seen is not None:
                taken.add(seen)
            done.add(n)
        pairs = sorted(
            ((v, n, tid) for n, faces in votes.items() for tid, v in faces.items()), reverse=True
        )
        for v, n, tid in pairs:
            if n in done or tid in taken or n not in self.speakers:
                continue
            if v < self.s.bind_min_s or v < self.s.bind_share * matched.get(n, 0.0):
                continue
            if tid != YOU and tid not in fusion.tracks:
                continue  # binds only to a face in view (its label and person come from there)
            spk = self.speakers[n]
            spk.track, spk.person, spk.label, spk.votes = tid, None, "", v
            done.add(n)
            if n in live:
                taken.add(tid)
        labels = fusion._labels()
        for spk in self.speakers.values():
            if spk.track is None or spk.track == YOU:
                continue
            tr = fusion.tracks.get(spk.track)
            if tr is not None and now - tr.t <= 0.5:
                spk.person = voice_id(tr.person_id, spk.track)
                spk.label = labels.get(spk.track, spk.label)

    # ------------------------------------------------------------------ caption words
    def _word_at(self, t: float, text: str | None = None) -> _Word | None:
        """The cloud word at `t` (within word_match_s): the same word if one is there, else the
        nearest; the newest stream's on a tie. In overlapping speech two voices' words cover
        the same moment, and the text tells them apart."""
        if self._index is None:
            rank = {s: i for i, s in enumerate(self.order)}
            self._index = sorted(
                (w for s in self.order for w in self.words[s].values()),
                key=lambda w: (w.t0, rank[w.stream]),
            )
            self._starts = [w.t0 for w in self._index]
        tol = self.s.word_match_s
        lo, hi = bisect_left(self._starts, t - 5.0), bisect_right(self._starts, t + tol)
        want = _norm(text) if text else None
        best, best_key = None, None
        for w in self._index[lo:hi]:
            gap = 0.0 if w.t0 <= t <= w.t1 else min(abs(t - w.t0), abs(t - w.t1))
            key = (want is not None and _norm(w.word) != want, gap)
            if gap <= tol and (best_key is None or key <= best_key):
                best, best_key = w, key
        return best

    def cloud_speaker_at(self, t: float, text: str | None = None) -> int | None:
        """The cloud speaker heard at time t (saying `text`), if the cloud tagged a word there."""
        w = self._word_at(t, text)
        return None if w is None else self.speaker_of.get((w.stream, w.tag))

    def _speaker_of(self, w) -> int | None:
        """The cloud speaker of a caption word (word, t0, t1)."""
        return self.cloud_speaker_at(_mid(w), str(w[0]))

    def evidence(self, fusion, words: list, local: list[Speaker], now: float) -> list[Speaker]:
        """Each caption word's speaker with the cloud's tags applied (see the module docstring)."""
        if not self.words or not words:
            return local
        cs = [self._speaker_of(w) for w in words]
        if all(n is None or n not in self.speakers for n in cs):
            return local
        self.bind(fusion, now)
        live = self._live()
        labels = fusion._labels()
        owner: dict[int, int] = {}  # face -> the newest stream's voice bound to it
        for n, spk in self.speakers.items():
            if spk.track is not None and n in live:
                owner[spk.track] = n
                seen = self._face_now(fusion, spk, now)
                if seen is not None:
                    owner[seen] = n
        guess: dict[int, Speaker] = {}
        out = []
        for loc, n in zip(local, cs):
            spk = self.speakers.get(n) if n is not None else None
            if spk is None or loc.kind in ("you", "you_typed"):
                out.append(loc)
            elif spk.track is not None:
                out.append(self._bound(fusion, spk, loc, labels, now))
            else:
                if n not in guess:
                    guess[n] = self._guess(fusion, n, words, local, cs, owner, labels, now)
                out.append(guess[n])
        return out

    def _bound(self, fusion, spk: _CloudSpeaker, loc: Speaker, labels, now: float) -> Speaker:
        """A bound voice's word: its face, or where that face went."""
        if spk.track == YOU:
            return Speaker("you", label="You")
        tid = self._face_now(fusion, spk, now)
        if tid is not None:
            tr = fusion.tracks[tid]
            return Speaker("face", tid, tr.person_id, labels.get(tid, spk.label))
        if loc.kind in ("face", "probable_face") and loc.track_id == spk.track:
            return loc  # said while its face was in view: a face leaving re-labels nothing
        vid = spk.person or f"track-{spk.track}"
        if loc.kind == "offscreen" and loc.person_id and loc.person_id == vid:
            return loc
        side, t_exit = fusion.exits.get(vid, ("none", -1e9))
        if now - t_exit > fusion.s.offscreen_exit_memory_s or side == "none":
            side = fusion._sensor_side(now)
        label = fusion.voice_labels.get(vid) or spk.label or "Someone"
        pid = None if vid.startswith("track-") else vid
        return Speaker("offscreen", None, pid, label, side)

    def _guess(self, fusion, n, words, local, cs, owner, labels, now: float) -> Speaker:
        """Where an unbound voice's words in this caption go (all of them to one place)."""
        cap = fusion.s.max_word_s
        mine = [(w, loc) for w, loc, m in zip(words, local, cs) if m == n]

        def dur(w) -> float:
            return min(_span(float(w[1]), float(w[2])), cap)

        # the face the local evidence gives most of these words, unless it's another voice's
        faces: dict[int, float] = {}
        best_of: dict[int, Speaker] = {}
        for w, loc in mine:
            if loc.kind in ("face", "probable_face") and loc.track_id not in owner:
                faces[loc.track_id] = faces.get(loc.track_id, 0.0) + dur(w)
                if loc.track_id not in best_of or loc.kind == "face":
                    best_of[loc.track_id] = loc
        if faces:
            return best_of[max(faces, key=faces.get)]
        # a face in view (nobody else's voice) the talk log has talking during these words
        talk: dict[int, float] = {}
        for w, _ in mine:
            for tid, v in self._talking(float(w[1]), float(w[2])).items():
                if tid != YOU and tid in labels and tid not in owner:
                    talk[tid] = talk.get(tid, 0.0) + v
        total = sum(dur(w) for w, _ in mine) or 1.0
        if talk:
            tid = max(talk, key=talk.get)
            share = talk[tid] / total
            if share >= self.s.bind_share / 2:
                tr = fusion.tracks[tid]
                kind = "face" if share >= self.s.bind_share else "probable_face"
                return Speaker(kind, tid, tr.person_id, labels[tid])
        # an off-screen voice the voice prints name
        off: dict[tuple, float] = {}
        off_of: dict[tuple, Speaker] = {}
        for w, loc in mine:
            if loc.kind == "offscreen":
                k = (loc.person_id, loc.label)
                off[k] = off.get(k, 0.0) + dur(w)
                off_of[k] = loc
        if off:
            return off_of[max(off, key=off.get)]
        # its own bubble in the dock
        return Speaker("offscreen", None, f"session-cloud-{n}", "Someone", fusion._sensor_side(now))

    # ------------------------------------------------------------------ caption pieces
    def _majority(self, words: list) -> tuple[int | None, float]:
        """The cloud speaker most of these words have, and its share of their time."""
        share: dict[int | None, float] = {}
        total = 0.0
        for w in words:
            d = _span(float(w[1]), float(w[2]))
            n = self._speaker_of(w)
            share[n] = share.get(n, 0.0) + d
            total += d
        if not share:
            return None, 0.0
        n = max(share, key=share.get)
        return n, share[n] / (total or 1.0)

    def _split(self, g) -> list:
        """Cut a piece already shown where the cloud hears another voice for turn_min_s.

        The drafts carry no tags (Google tags final results), so a reply in the middle of a
        shown piece only turns up with the final: it becomes its own piece, with the speaker
        its cloud evidence gives. The rest keeps the piece's speaker and segment id.
        """
        if not g.locked or len(g.words) < 2 or len(g.evidence) != len(g.words):
            return [g]
        runs: list[list] = []  # [cloud speaker, first word, last word]; untagged words join
        for i, w in enumerate(g.words):
            n = self._speaker_of(w)
            if runs and (n is None or runs[-1][0] in (None, n)):
                runs[-1][0] = runs[-1][0] if n is None else n
                runs[-1][2] = i
            else:
                runs.append([n, i, i])
        home, _ = self._majority(g.words)
        pieces: list[list] = []  # [key, first word, last word]
        for k, (n, i0, i1) in enumerate(runs):
            span = float(g.words[i1][2]) - float(g.words[i0][1])
            before = runs[k - 1][0] if k else None
            after = runs[k + 1][0] if k + 1 < len(runs) else None
            turn = self._is_turn(n, span, i1 - i0 + 1, before, after)
            key = n if n not in (None, home) and turn else "home"
            if pieces and pieces[-1][0] == key:
                pieces[-1][2] = i1
            else:
                pieces.append([key, i0, i1])
        if len(pieces) < 2:
            return [g]
        out, kept = [], False
        for key, i0, i1 in pieces:
            words, evidence = g.words[i0 : i1 + 1], g.evidence[i0 : i1 + 1]
            if key == "home":
                out.append(replace(g, words=words, evidence=evidence, prev=[] if kept else g.prev))
                kept = True
            else:
                spk = _most(words, evidence)
                out.append(
                    replace(g, speaker=spk, words=words, evidence=evidence, prev=[], locked=False)
                )
        return out

    def protect(self, groups: list) -> list:
        """Split and lock pieces the cloud says are another voice than their neighbours (a turn)."""
        if not self.words or not groups:
            return groups
        groups = [piece for g in groups for piece in self._split(g)]
        if len(groups) < 2:
            return groups
        who = [self._majority(g.words) for g in groups]
        for i, g in enumerate(groups):
            n, share = who[i]
            if n is None or share < self.s.bind_share or g.speaker.kind == "someone":
                continue
            before = who[i - 1][0] if i else None
            after = who[i + 1][0] if i + 1 < len(groups) else None
            span = float(g.words[-1][2]) - float(g.words[0][1])
            if n not in (before, after) and self._is_turn(n, span, len(g.words), before, after):
                g.locked = True
        return groups

    def _is_turn(self, n, span: float, words: int, before, after) -> bool:
        """Is a run of voice `n` (`span` s, `words` words) between these neighbours a turn?

        It lasts turn_min_s; inside one other voice's speech (the same voice on both sides)
        it also has two words: a single wrongly tagged word in someone's sentence is not a turn.
        """
        inside = before is not None and before == after and before != n
        return span >= self.s.turn_min_s and (words >= 2 or not inside)

    def placed(self, a: list, b: list) -> bool:
        """Did the cloud put the change between these two runs of words (different voices)?"""
        if not self.words:
            return False
        na, sa = self._majority(a)
        nb, sb = self._majority(b)
        share = self.s.bind_share
        return None not in (na, nb) and na != nb and sa >= share and sb >= share

    # ------------------------------------------------------------------ finals
    def wait(self, ev, now: float) -> bool:
        """Hold this final caption a moment for the cloud's tags on its words?"""
        if not get(ev, "final", False):
            return False
        utt = str(get(ev, "utt_id"))
        if self.state != "on" or not self.heard:
            self._finals.pop(utt, None)
            return False
        words = get(ev, "words") or []
        last = float(words[-1][2]) if words else float(get(ev, "t_end", now))
        if max(self.heard.values()) >= last - 0.05:
            self._finals.pop(utt, None)
            return False
        first = self._finals.setdefault(utt, now)
        if now - first >= self.s.final_wait_ms / 1000:
            self._finals.pop(utt, None)
            return False
        return True

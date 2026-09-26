"""P-29: the face part of enrollment reports its progress and a hint for the glasses."""

import numpy as np
from attune.vision.enrollment import EnrollJob, hint, progress


def job():
    return EnrollJob(track_id=3, name="Sam", consent_t="1.0", start_t=10.0)


def test_progress_follows_crops_but_never_runs_ahead_of_the_window():
    j = job()
    assert progress(j, 10.0, enroll_s=5.0, crops=8) == 0.0
    j.prints = [np.zeros(4)] * 8  # all crops in the first second
    assert progress(j, 11.0, 5.0, 8) == 0.2  # held back by the 5 s window
    assert progress(j, 15.0, 5.0, 8) == 1.0
    j.prints = [np.zeros(4)] * 2  # few good crops: the ring stalls
    assert progress(j, 15.0, 5.0, 8) == 0.25
    j.prints = [np.zeros(4)] * 20
    assert progress(j, 30.0, 5.0, 8) == 1.0


def test_hint_names_what_is_wrong_with_the_latest_crops():
    j = job()
    assert hint(j, 10.5) == ""
    j.last_reject = ("dark", 10.4)
    assert hint(j, 10.5) == "more light"
    j.last_accept_t = 10.6  # a good crop since: no hint
    assert hint(j, 10.7) == ""
    j.last_reject = ("small", 11.0)
    assert hint(j, 11.2) == "come closer"
    assert hint(j, 12.5) == ""  # an old reject is forgotten


def test_saving_a_confirmed_name_replaces_its_session_entry(tmp_path):
    """P-29: the double tap confirms "Sam?" (a session person with this face) and then saves
    Sam. The saved person must replace the session one, or the two identical faces tie on the
    match margin and Sam is never recognised again."""
    from types import SimpleNamespace

    from attune.vision.gallery import Gallery, Identity, IdentityRules
    from attune.vision.service import VisionService

    rng = np.random.default_rng(7)
    face = rng.standard_normal(512).astype(np.float32)
    face /= np.linalg.norm(face)
    prints = [
        face + 0.02 * rng.standard_normal(512).astype(np.float32) for _ in range(8)
    ]
    prints = [p / np.linalg.norm(p) for p in prints]

    gallery = Gallery(str(tmp_path / "people"))
    rules = IdentityRules(gallery)
    ident = Identity()
    session = gallery.add_session("Sam", np.stack(prints[:3]))  # the confirmed "Sam?"
    rules.assign(ident, session.person_id, 1.0, 0.0)
    track = SimpleNamespace(track_id=3, data={"ident": ident})
    events = []
    vs = VisionService.__new__(VisionService)
    vs.gallery, vs.rules = gallery, rules
    vs.s = SimpleNamespace(enroll_min_crops=5, enroll_crops=8, enroll_s=5.0)
    vs.bus = SimpleNamespace(publish=lambda topic, ev: events.append((topic, ev)))
    vs.tracker = SimpleNamespace(active=[track], lost=[])
    vs.job = EnrollJob(
        track_id=3, name="Sam", consent_t="1.0", start_t=0.0, prints=prints
    )

    vs._finish_enrollment(6.0)

    saved = gallery.people()
    assert len(saved) == 1 and saved[0].enrolled
    assert gallery.get(session.person_id) is None  # the session entry is gone
    assert ident.person_id == saved[0].person_id
    # and the same face is recognised again as the saved Sam
    pid, score, second = gallery.match(face)
    assert pid == saved[0].person_id and score - second >= rules.margin

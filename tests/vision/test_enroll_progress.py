"""P-28: the face part of enrollment reports its progress and a hint for the glasses."""

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

"""V-06 and V-07: gallery storage, match rules and enrollment helpers."""

import os
from collections import Counter

import numpy as np
import pytest
from attune.vision.enrollment import EnrollJob, finish, most_varied, validate_request
from attune.vision.gallery import Gallery, Identity, IdentityRules

rng = np.random.default_rng(0)


def unit(v):
    return (v / np.linalg.norm(v)).astype(np.float32)


def person_prints(center, n=6, noise=0.05):
    return np.stack([unit(center + noise * rng.standard_normal(512)) for _ in range(n)])


@pytest.fixture
def gallery(tmp_path):
    return Gallery(str(tmp_path / "people"))


def test_enroll_persists_and_delete_removes_files(gallery, tmp_path):
    a = unit(rng.standard_normal(512))
    p = gallery.enroll("Maya Lopez", person_prints(a), "2026-09-26T10:00:00")
    folder = tmp_path / "people" / p.person_id
    assert (folder / "face.npy").exists() and (folder / "meta.json").exists()
    assert p.person_id.startswith("maya-lopez-")

    again = Gallery(str(tmp_path / "people"))
    again.load()
    assert [x.name for x in again.people()] == ["Maya Lopez"]
    assert again.people()[0].consent_t == "2026-09-26T10:00:00"

    again.rename(p.person_id, "Maya")
    third = Gallery(str(tmp_path / "people"))
    third.load()
    assert third.people()[0].name == "Maya"

    third.delete(p.person_id)
    assert not os.path.exists(folder)
    assert third.match(a)[0] is None


def test_session_people_are_forgotten_and_never_saved(gallery, tmp_path):
    s = gallery.add_session("Sam", person_prints(unit(rng.standard_normal(512))))
    assert not (tmp_path / "people").exists()
    assert gallery.forget_session() == [s.person_id]
    assert gallery.get(s.person_id) is None


def make_rules(gallery):
    a, b = unit(rng.standard_normal(512)), unit(rng.standard_normal(512))
    pa = gallery.enroll("A", np.stack([a]), "t").person_id
    pb = gallery.enroll("B", np.stack([b]), "t").person_id
    return IdentityRules(gallery, 0.45, 0.08, 3, 2.0, 3), a, b, pa, pb


def test_name_needs_three_hits_within_one_second(gallery):
    rules, a, _, pa, _ = make_rules(gallery)
    ident = Identity()
    assert not rules.observe(ident, a, 0.0)
    assert not rules.observe(ident, a, 0.3)
    assert rules.observe(ident, a, 0.6)
    assert ident.person_id == pa


def test_hits_spread_over_more_than_one_second_do_not_name(gallery):
    rules, a, *_ = make_rules(gallery)
    ident = Identity()
    for t in (0.0, 0.7, 1.4, 2.1):
        rules.observe(ident, a, t)
    assert ident.person_id is None


def test_below_threshold_or_margin_is_not_named(gallery):
    rules, a, b, *_ = make_rules(gallery)
    stranger = unit(rng.standard_normal(512))
    ambiguous = unit(a + b)  # equally close to A and B: fails the margin
    for probe in (stranger, ambiguous):
        ident = Identity()
        for t in (0.0, 0.2, 0.4, 0.6):
            rules.observe(ident, probe, t)
        assert ident.person_id is None


def test_three_failed_rechecks_return_to_unknown(gallery):
    rules, a, _, pa, _ = make_rules(gallery)
    ident = Identity()
    for t in (0.0, 0.1, 0.2):
        rules.observe(ident, a, t)
    assert ident.person_id == pa
    assert not rules.needs_check(ident, 1.0)
    assert rules.needs_check(ident, 2.3)
    stranger = unit(rng.standard_normal(512))
    rules.observe(ident, stranger, 2.3)
    rules.observe(ident, stranger, 4.4)
    assert ident.person_id == pa
    assert rules.observe(ident, stranger, 6.5)
    assert ident.person_id is None


def test_never_jumps_straight_to_another_name(gallery):
    rules, a, b, pa, _ = make_rules(gallery)
    ident = Identity()
    for t in (0.0, 0.1, 0.2):
        rules.observe(ident, a, t)
    for t in (2.3, 4.4):
        rules.observe(ident, b, t)
        assert ident.person_id == pa  # B winning only counts as a failed recheck
    rules.observe(ident, b, 6.5)
    assert ident.person_id is None  # unknown first; B must now earn three hits
    rules.observe(ident, b, 6.6)
    assert ident.person_id is None


def test_most_varied_picks_spread_prints():
    base = unit(rng.standard_normal(512))
    near = [unit(base + 0.01 * rng.standard_normal(512)) for _ in range(10)]
    far = [unit(base + 0.6 * rng.standard_normal(512)) for _ in range(3)]
    chosen = most_varied(np.stack(near + far), 4)
    assert len(chosen) == 4
    matches = sum(any(np.allclose(c, f) for f in far) for c in chosen)
    assert matches == 3


def test_enrollment_validation_and_reasons():
    assert validate_request(1, "Maya", False, None) == "consent is required"
    assert validate_request(1, "Maya", True, "") == "consent is required"
    assert validate_request(1, " ", True, "t") == "a name is required"
    assert validate_request(1, "Maya", True, "t") == ""

    job = EnrollJob(
        1,
        "Maya",
        "t",
        0.0,
        prints=[unit(rng.standard_normal(512)) for _ in range(3)],
        rejects=Counter({"dark": 10, "small": 2}),
    )
    prints, reason = finish(job, 5, 8)
    assert prints is None and reason == "more light"
    job.prints += [unit(rng.standard_normal(512)) for _ in range(9)]
    prints, reason = finish(job, 5, 8)
    assert prints.shape == (8, 512) and reason == ""
    assert finish(EnrollJob(1, "M", "t", 0.0), 5, 8) == (None, "stay in view")

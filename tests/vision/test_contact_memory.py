"""Automatic face contact creation from attributed conversation, without live models."""

from collections import deque
from types import SimpleNamespace

import numpy as np
from attune.vision import types as T
from attune.vision.gallery import Identity
from attune.vision.service import VisionService

from .conftest import FakeBus


def test_confident_conversation_saves_once_and_resists_a_repeat(tmp_path):
    bus = FakeBus()
    svc = VisionService(bus, {"vision": {"asd_enabled": False}}, root=str(tmp_path))
    face = np.zeros(512, np.float32)
    face[0] = 1
    tr = SimpleNamespace(
        track_id=4,
        seen=True,
        first_t=0.0,
        data={"ident": Identity(), "recent": deque([face] * 5)},
    )
    svc.tracker.active = [tr]
    caption = {
        "utt_id": "one",
        "final": True,
        "speaker": {"kind": "face", "track_id": 4, "person_id": None},
    }
    svc._remember_speaker(caption, 2.0)
    person = svc.gallery.people()[0]
    assert person.source == "auto" and person.name == "New person"
    assert (tmp_path / "data" / "people" / person.person_id / "face.npy").exists()
    assert bus.last(T.ENROLL_RESULT).source == "auto"
    svc._remember_speaker(caption | {"utt_id": "two"}, 3.0)
    assert len(svc.gallery.people()) == 1


def test_probable_face_needs_two_distinct_final_captions(tmp_path):
    bus = FakeBus()
    svc = VisionService(bus, {"vision": {"asd_enabled": False}}, root=str(tmp_path))
    face = np.zeros(512, np.float32)
    face[0] = 1
    svc.tracker.active = [
        SimpleNamespace(
            track_id=4,
            seen=True,
            first_t=0.0,
            data={"ident": Identity(), "recent": deque([face] * 5)},
        )
    ]
    caption = {
        "utt_id": "one",
        "final": True,
        "speaker": {"kind": "probable_face", "track_id": 4, "person_id": None},
    }
    svc._remember_speaker(caption, 2.0)
    svc._remember_speaker(caption, 2.1)
    assert svc.gallery.people() == []
    svc._remember_speaker(caption | {"utt_id": "two"}, 3.0)
    assert len(svc.gallery.people()) == 1


def test_manual_face_is_reused_for_later_voice_enrollment(tmp_path):
    svc = VisionService(
        FakeBus(), {"vision": {"asd_enabled": False}}, root=str(tmp_path)
    )
    face = np.zeros(512, np.float32)
    face[0] = 1
    person, reused = svc._manual_person("Sam", np.stack([face] * 5), "1")
    assert not reused
    again, reused = svc._manual_person("Sam", np.stack([face] * 5), "2")
    assert reused and again.person_id == person.person_id
    assert len(svc.gallery.people()) == 1


def test_confirmed_proposal_names_an_automatic_profile(tmp_path):
    bus = FakeBus()
    svc = VisionService(bus, {"vision": {"asd_enabled": False}}, root=str(tmp_path))
    face = np.zeros(512, np.float32)
    face[0] = 1
    person, _ = svc.gallery.remember_auto(np.stack([face] * 5))
    tr = SimpleNamespace(
        track_id=4,
        seen=True,
        first_t=0.0,
        embedding=None,
        data={
            "ident": Identity(person_id=person.person_id),
            "recent": deque([face] * 5),
        },
    )
    svc.tracker.active = [tr]
    svc._on_proposal({"track_id": 4, "name": "Sam", "state": "confirmed"}, 2.0)
    assert svc.gallery.get(person.person_id).name == "Sam"
    assert bus.last(T.PERSON_CHANGED).action == "renamed"

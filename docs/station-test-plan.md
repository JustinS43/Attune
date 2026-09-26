# Enrollment station: human test plan (V-23, A-21, P-35)

People are saved at the laptop's own camera (OV02E10) and mic ("Microphone Array"). The glasses
camera and mic (Brio 101) stay on the room: captions, recognising people, translation. This plan
checks that a save at the laptop works, that the glasses recognise the person straight away by
face and by voice, and that nothing but prints is stored.

About 20 minutes, two people: **you** and a **teammate**. Only the person being saved ticks
consent, on the phone, themselves.

## 0. Before you start

1. Merge the PR, pull `main`, and add this to your local `config/attune.toml` (or copy the
   `[enroll]` table from `config/attune.example.toml`):

   ```toml
   [enroll]
   source = "station"
   camera_name = "OV02E10"
   mic_name = "Microphone Array"
   ```

2. Restart the engine the way you normally do. The laptop camera light must be **off**: the
   station opens the laptop camera only while someone is being saved.
3. Sit at the laptop and run the device check (it saves nothing):

   ```powershell
   uv run --project engine python scripts/station_check.py --faces --speak
   ```

   Good: both devices found, the Brio listed as "the glasses camera, not opened", about 30 fps,
   brightness 60 to 200, your face "seen" and about 25 to 60% of the preview width, the room's
   noise floor below -45 dBFS, your reading 20 dB or more above the room, and "OK" twice. Fix
   anything it names first (light in front of you, not behind; sit about an arm's length away).

## 1. Save yourself from the phone's Enroll tab

1. On the phone: **Enroll** tab. You should see "Remember me", a name box, a consent box and
   **Start at the laptop** (greyed out until you type your name and tick the box yourself).
2. Type your name, tick the box, press **Start at the laptop**.
3. Good:
   - The laptop camera light comes on within about 2 s. The phone shows **Look at the laptop
     camera** with a live, mirrored picture, an oval and one hint at a time ("Move to the
     middle of the picture", "Come a little closer", "More light on your face, please"...).
   - Line your face up in the oval; the oval turns solid and the Face bar fills in about 5 s.
   - The camera light goes **off** as soon as the face part is done.
   - The phone shows **Read this sentence**. Read it at your normal voice, facing the laptop.
     The level meter moves while you talk; the hint says "Speak up a little" if you are too
     quiet and "A bit softer, please" if you shout. The Voice bar fills after about 5 s of
     speech (the sentence takes 6 to 8 s to read).
   - **"<your name> is saved"** with Face saved and Voice saved.
4. Check the files: `data/people/<your-id>/` holds exactly `face.npy`, `meta.json` and
   `voice.json`. No pictures, no recordings anywhere.

## 2. The glasses recognise you at once

1. Point the glasses (Brio) at yourself, as the wearer would see you.
2. Good: the lens shows your name within about 1 s of your face appearing, without having to
   be introduced. If you were already in view while saving, your label changes to your name
   the moment the face part finishes.
3. Talk while in view: captions carry your name.

## 3. Save a teammate with a double tap

1. Wear or hold the glasses so the teammate is in view, say "This is <name>" (or introduce
   them however you normally do) so the lens shows "<name>?", then **double tap** the side
   of the glasses (key **D** on the lens).
2. The phone shows **Save <name>?**. Hand the phone to the teammate; **they** tick the box and
   press **Save**.
3. Good: the phone switches to **Look at the laptop camera**; the teammate sits at the laptop,
   and the same steps as in test 1 follow. Walking from the glasses' view to the laptop does
   not cancel anything.
4. After "saved", the teammate's label on the lens changes to their name at once (no
   re-introduction), and stays when they leave and come back.

## 4. The identity check

1. Double tap on the teammate again (or on a third person), but let **you** (someone else)
   sit at the laptop.
2. Good: after a few seconds the phone says **"That isn't the person you were looking at"**
   with **Try again** and **Save as someone new**. Nothing is saved yet.
3. Press **Try again** with the right person at the laptop: it goes on normally. Or press
   **Save as someone new**: the person at the laptop is saved under the name typed, and the
   glasses face the double tap started from keeps its old label.

## 5. Voice when the face is not visible

1. With the teammate saved (test 3), turn the glasses away so their face is out of view (or
   have them stand behind the wearer) and let them talk for a few seconds.
2. Good: after about 1 s of speech the caption is labelled with their name (an off-screen
   voice match), not "Someone". A saved person's voice print was made on the laptop mic; it
   is compared with its own threshold (`[voice] station_match`) so it still matches on the
   glasses mic.
3. Then let them talk in view of the glasses for a minute (alone, not over someone else).
   Their `voice.json` gains up to 8 `adapted` prints from the glasses mic (the original print
   is never replaced), which makes the off-screen match stronger. Repeat step 1: the match
   should come as fast or faster.

## 6. Things that go wrong on purpose

| Do this | Good |
|---|---|
| Open the Windows Camera app on the laptop camera, then start a save | The phone says "Can't use the laptop camera" and why; with a double-tap save it offers "Save with the glasses instead". Close the Camera app afterwards. |
| Unplug the Brio, then start a save | The main view falls back to the laptop camera; the save still works and the preview label says "LAPTOP CAMERA · SHARED". Plug the Brio back in: the main view returns to it. |
| Stay silent at "Read this sentence" | After 40 s: "We didn't hear enough" with Try again / Skip voice for now. Skip: saved with the face only. |
| Press Cancel (or Escape on a keyboard) during the face step | "Nothing was saved"; camera light off. |
| Triple tap (pause) or "Forget session" during a save | The save stops; the camera and mic close. |
| Two people in front of the laptop | "One person at a time, please". |

## 7. Privacy checks

- The laptop camera light is on only during the face step of a save.
- Only the phone that started the save shows the preview; another phone or the console does not.
- `data/people/` holds only `face.npy`, `meta.json`, `voice.json` per person; deleting the
  person in the phone's People list removes the whole folder.

## Numbers that tell you it is working

- Face step 5 to 15 s once the person is lined up; voice step 6 to 15 s.
- The glasses name a saved person within about 1 s of first seeing them.
- Off-screen voice: the speaker's name within about 1 to 2 s of speech in a quiet room.

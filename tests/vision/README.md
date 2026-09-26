# Tests - Section 1 - Vision

Pytest tests for this section only. Run with `uv run pytest tests/vision` from `engine/`'s parent.
Use the replay reel (data/reels/) instead of live devices where possible.

## V-25: live speaker gate rehearsal

Run this on a laptop with camera access and the existing local Attune weights.
Keep video, audio, truth labels, and any face or voice data local and out of Git.
Do not download models without the owner's approval.

1. From the repo root, list cameras with
   `uv run --project engine python -m attune.vision.devview --list-cameras`.
   On macOS, Attune prefers an external webcam and uses the built-in camera when no
   external camera is available (`camera_fallback_any=true`). If enumeration is
   unavailable but the built-in camera works, pass its index with `--source 0`.
   Grant the terminal or app macOS Camera access before testing.
2. Confirm the local face finder and landmarker weights and
   `models/light_asd/finetuning_TalkSet.model` are present. Start a brief live view
   with `uv run --project engine python -m attune.vision.devview --source 0 --seconds 10`
   when the built-in camera is index 0. Check that faces track and the camera does
   not drop frames. Use the listed index if it differs.
3. Capture one synchronized local video and WAV with an off-screen voice, a silent
   visible face, and a visible person speaking. Include a turn change if possible.
   Label the time intervals and visible person's face region in a local `truth.json`;
   the format is documented at the top of `scripts/eval_talker.py`. Keep the clip,
   WAV, and labels outside the repo or in ignored `data/`.
4. Compare the same inputs with Light-ASD enabled and disabled:

   ```sh
   uv run --project engine python scripts/eval_talker.py data/local/clip.mp4 data/local/clip.wav data/local/truth.json --cpu --seconds --out data/local/asd-on.json
   uv run --project engine python scripts/eval_talker.py data/local/clip.mp4 data/local/clip.wav data/local/truth.json --cpu --seconds --no-asd --out data/local/asd-off.json
   ```

   Replace the example paths with the actual local paths. Compare background words
   wrongly credited to a face, person words credited to the correct face, first
   caption latency, face-lit seconds, and ASD score coverage. If only the ASD-on
   run fails, inspect its score, timing, and threshold gate. If both fail, inspect
   mouth sampling, audio/video alignment, and voice-match decisions. Use the
   evaluator's `--offline --device cpu --det-device cpu --offsets ...` mode when
   timing is suspect.
5. Run `uv run --project engine python -m attune` with the chosen camera and open
   `http://localhost:8000/lens/` for the demo. Check that the speech bubble follows
   the visible speaker and that off-screen speech does not light a silent face.
   Record the observed results and the remaining gate in the V-25 follow-up PR
   before marking the TODO item done.

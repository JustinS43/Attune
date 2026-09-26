# ElevenLabs: Speak for me

Attune uses ElevenLabs to speak the wearer's typed replies, presets and explicitly
selected suggestions through the laptop speakers. Microphone audio, captions,
camera frames and conversation history are not sent to ElevenLabs.

## Connect your account

1. From the repository root, copy `.env.example` to `.env` if `.env` does not exist.
2. A human enters their API key into `ELEVENLABS_API_KEY` in that local file.
   Never paste it into chat, source code, logs or a pull request.
3. Optionally enter a preset voice's ID in `ELEVENLABS_VOICE_ID`. Leaving it blank
   uses the built-in George voice. Voice cloning is not needed for the demo.
4. Install the existing project dependencies with
   `uv sync --project engine --extra hardware --extra audio --extra dev`.
   This installs the full audio stack; it does not download the Kokoro model.
5. Start Attune from the repository root with `uv run --project engine --no-sync python -m attune`.
   Restart after changing credentials. Nonblank environment variables take
   precedence; missing key and voice values are filled independently from `.env`.
6. Open the Speak panel, type a short reply and submit it. The laptop should speak
   and show the reply as “You (typed)”. Check the console's `speech_out` status:
   `metrics.last_voice` must be `elevenlabs` to verify the online path.

The engine already starts `SpeechOutService` and routes the `speak` command to it.
The service uses Eleven Flash v2.5 and streams 24 kHz PCM. See the
[official streaming guide](https://elevenlabs.io/docs/eleven-api/guides/how-to/text-to-speech/streaming).

## Fallback and privacy checks

- If ElevenLabs fails or supplies no audio within `speech_out.fallback_after_s`
  (1.5 seconds by default), the service tries local Kokoro. Its model must already
  be installed in `models/tts/kokoro-multi-lang-v1_0`; use the project's model
  download workflow with human approval. Without it, offline speech is unavailable.
- Repeat a reply with Wi-Fi off and verify `metrics.last_voice` is `kokoro`.
- While playback runs, `speech_out.playing` tells the caption service to mute
  itself, avoiding duplicate captions of the laptop's voice.
- “Forget session” cancels playback and clears queued replies, including when
  the online stream stalls. It cannot retract text already sent to ElevenLabs.
- A stream that fails after playback starts does not repeat the reply using Kokoro.

Automated tests use synthetic audio and a mocked HTTP transport for the real SDK.
They do not spend credits or send replies online. Run them with
`uv run --project engine --no-sync pytest tests/hardware_services`.
Live account access, speaker output and the offline model still need the manual
checks above.

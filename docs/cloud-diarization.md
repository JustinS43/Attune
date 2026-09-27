# Cloud captions: Google speaker diarization (optional)

TODO: A-32 (audio client), V-32 (fusion), P-48 (Settings, badges, console). Contracts:
"Cloud captions" in [contracts.md](contracts.md). Setup steps: [setup.md](setup.md#cloud-captions-optional).

Attune decides who is talking on the laptop: Light-ASD hears which mouth moves with the sound,
CAM++ voice prints recognise voices, and fusion (`engine/attune/fusion/speaker.py`) puts them
together. In a quick back-and-forth that evidence arrives late and sticks to the person who
talked first, so a short reply from a second person often lands in the first person's bubble.

Cloud captions add one strong signal: a streaming speech service that labels every word with
a speaker. It is **optional and off by default**. The wearer turns it on in the phone's
Settings; while it is on, the laptop streams the mic audio to Google, and every page shows a
calm "Cloud captions on" badge. The local captions never stop: the words, drafts,
translations and history still come from the laptop, and if the cloud fails, is slow or has no
key, Attune falls back to local speaker labels without a gap.

## The choice: Speech-to-Text v1 `StreamingRecognize`, `latest_long`, en-US

Researched on 2026-09-26 from Google's documentation (pages dated 2026-09-24). The docs at
`cloud.google.com/speech-to-text/...` now redirect to `docs.cloud.google.com/...`.

| | v1 `StreamingRecognize` + `diarization_config` | v2 (recognizers; Chirp 2 / Chirp 3, `long`, `short`) |
|---|---|---|
| Diarization while **streaming** | **Yes.** `SpeakerDiarizationConfig` (min/max speakers) on `RecognitionConfig`; tags on the top alternative of final results | **No.** The v2 language table lists diarization only for `chirp_3` (and `medical_conversation`), and the Chirp 3 page says it is available in `Recognize` / `BatchRecognize`, not `StreamingRecognize`. `long`, `short`, `telephony` have none |
| Models with diarization (en-US) | `latest_long`, `latest_short`, `default`, `phone_call`, `telephony`, `telephony_short`, `command_and_search` (marked Preview) | batch only (above) |
| Spanish | es-US only with `command_and_search` (a short-phrase model); es-ES and es-MX have none | Chirp 3 batch: es-US, es-ES |
| Latency | streaming: interim results while you talk, finals at pauses. Google publishes no latency figures | Chirp models are slower (batch for diarization: seconds to minutes) |
| Stream limit | ~5 minutes per stream (305 s), audio at real-time pace, 10 MB per message | 5 minutes, 25 KB of audio per request; Chirp 3 only in the `us` / `eu` multi-regions |
| Auth | service account (documented); API key over gRPC with `x-goog-api-key` (see below) | same, plus a recognizer resource in a project and location |
| Price (per minute of audio, billed per second) | about $0.016 with Google's data logging, $0.024 without (we keep logging off); 60 free minutes a month | standard $0.016; dynamic batch $0.003 |

v1 is the only Google API that returns speaker labels **while streaming**, so it's the only
one that can help live captions. Among its models, `latest_long` is Google's current
conformer model for long, conversational audio (the others are tuned for phone calls, short
voice commands or short queries). `video` is not listed with diarization in the v1 table as of
2026-09-26. The language defaults to **en-US**. Spanish (es-US) is offered too, with Google's
short-phrase model and preview labels; the local captions keep transcribing and translating
Spanish either way. `[cloud] models` maps each language to its model.

Sources: [v1 RPC reference](https://docs.cloud.google.com/speech-to-text/docs/reference/rpc/google.cloud.speech.v1),
[multiple voices](https://docs.cloud.google.com/speech-to-text/docs/multiple-voices),
[v1 languages](https://docs.cloud.google.com/speech-to-text/docs/v1/speech-to-text-supported-languages),
[v2 languages](https://docs.cloud.google.com/speech-to-text/docs/speech-to-text-supported-languages),
[Chirp 3](https://docs.cloud.google.com/speech-to-text/v2/docs/chirp_3-model),
[quotas](https://docs.cloud.google.com/speech-to-text/quotas),
[pricing](https://cloud.google.com/speech-to-text/pricing) (the page didn't load in full for us;
the figures above are from third-party summaries of it: check it before relying on them).

### Auth: an API key first, a service account as the alternative

The phone's Settings takes a **Google API key** and saves it in the laptop's `.env` as
`GOOGLE_SPEECH_API_KEY`, exactly like the ElevenLabs key: loopback only, never shown again, applied without a restart. The client passes
it with `ClientOptions(api_key=...)` (google-cloud-speech 2.40.0 supports this), which sends it
as `x-goog-api-key` metadata on the gRPC call. Community examples confirm that a v1 gRPC
`Recognize` call works with that header. Google's Speech-to-Text auth pages document only
Application Default Credentials and OAuth, and don't mention API keys. We expect
`StreamingRecognize` to accept a key the same way (same service, same check), but that is
**not confirmed until the live check**.

If Google rejects the key (`UNAUTHENTICATED` / `PERMISSION_DENIED`), the client falls back to the
documented route: a service-account JSON key file whose full path is in `.env` as
`GOOGLE_APPLICATION_CREDENTIALS`. With both set, the key is tried first. The client never
uses any other Google login on the laptop (it never calls `google.auth.default()`), so no
audio leaves without one of these two.

### Data use

Google uses and keeps the audio only if the project opts in to data logging (the cheaper
rate). Leave it off. Everything else about Google's data terms applies to the audio while
cloud captions are on; that is why they are opt-in and visible on every page.

## How it works

```
mic -> audio.block (16 kHz) --+--> local VAD + Nemotron --> audio.transcript --+
                              |                                                 +--> fusion --> caption
                              +--> cloud_diarize (only while on) --> Google     |
                                        speaker.cloud (word, t0, t1, tag) ------+
                                        cloud.state (on / fallback / ...) ------> fusion, hub (badge)
```

**The client** (`engine/attune/audio/cloud_diarize.py`, Section 2) runs on its own threads.
The bus callback only puts a block in a bounded queue (`queue_s`), never waiting, so a slow
network can't hold up the loop or the local captions.
- **Streams.** A pump thread packs the 16 kHz audio into 100 ms LINEAR16 chunks and feeds the
  current stream. Each stream has its own thread reading Google's responses.
- **Restarts.** Before Google's ~5 minute limit (`stream_max_s`, 290 s) a new stream starts. It is
  fed the last `overlap_s` (3 s) of audio first, and the old stream is half-closed so its last
  results still arrive.
- **Tags.** Words come back with offsets from the start of their stream. The client maps them onto
  the engine clock (the stream's first sample plus the offset) and publishes `speaker.cloud`
  with the words that are new or re-tagged. With diarization on, Google repeats every word of
  the stream in each final result, with revised tags.
- **Silence and pauses.** During the wearer's own spoken reply the stream gets silence instead of
  the audio. While recognition is paused, the stream is closed.

**States and fallback.** `cloud.state` goes out on every change:
- `off`: not turned on. No network client exists.
- `connecting`.
- `on`: results are fresh.
- `fallback`: `network` errors (retried after `retry_s`), a `quota` error (retried after
  `quota_retry_s`), or `slow`: the median lag of recent results is over `latency_fallback_ms`, or
  speech was heard with no result for `stall_s`.
- `unavailable`: `credentials missing`, `credentials rejected` or `library missing`. Nothing is
  sent; a key saved in Settings is picked up by itself.
- `paused`.

The log says only "cloud: credentials present" or "cloud: credentials missing", and error class
names, never Google's full error text.

**Fusion** (`engine/attune/fusion/cloud_tags.py`, Section 1) treats tags as evidence from one
stream. It never trusts a tag number across streams:
- **Cloud speakers.** Each (stream, tag) is a *cloud speaker*. After a restart, a new tag takes
  over an old tag's cloud speaker when their words overlap in time for `bridge_min_s`. That
  overlap is why the new stream starts with audio the old one already heard.
- **Binding to faces.** Each fusion tick records which visible faces are talking: Light-ASD's
  verdict where it is fresh, otherwise the lip checks. A cloud speaker binds to a face once
  `bind_min_s` of its words overlap that face talking, and that face has `bind_share` of its
  face-matched speech. Faces and speakers are paired greedily, strongest first, one to one. A
  binding holds until another face has `rebind_ratio` times the evidence. When the face's
  person is known, the binding also remembers the person, so a new track of the same person
  takes it over.
- **Caption words.** Each word takes the cloud speaker of the cloud word nearest its middle
  (within `word_match_s`). A bound speaker's words go to its face. A face that isn't in view
  sends its words to the dock with that person's label and the side they left from.
- **Unbound speakers.** An unbound speaker whose words the local evidence gave to a face bound to
  someone else goes to another face that was talking then, if there is one. Otherwise it goes to
  the dock as its own "Someone", never merged into the neighbouring speaker's bubble.
- **Turn changes.** A run of another cloud speaker at least `turn_min_s` long always gets its own
  caption segment. The smoothing that folds short pieces into their neighbours leaves it alone,
  and the "move the change to the pause" rule doesn't move words the cloud tagged.
- **Waiting for tags.** While the state is `on`, a final caption waits up to `final_wait_ms`
  (1.2 s) for the cloud to hear past its last word. The draft stays on screen meanwhile. In any
  other state it never waits.
- **Finals are never changed.** A final caption is never changed once sent. Tags that arrive later
  only improve the bindings for the next words.

**Cloud words don't replace local words.** We kept the local recogniser's words for captions
even while cloud captions are on:
- Google's streaming diarization covers en-US well but not Spanish, which the local path
  transcribes and translates.
- The utterance ids that translation and history hang on come from the local path.
- Falling back is instant only because the local words are already on screen.
- We have no side-by-side accuracy or latency numbers for Google on our mic yet (no
  credentials on the test laptop).

Revisit after the live check. `scripts/bench_captions.py` and `scripts/eval_podcast.py` can
compare the two once a key is in.

## Privacy

Only the 16 kHz mic audio leaves, and only while the wearer has cloud captions on. Nothing
leaves while recognition is paused, and silence is sent while the wearer's own reply plays.
Frames, face and voice prints, names, captions and history stay on the laptop. The choice
itself (`<data_dir>/cloud.json`) and the key (`.env`) stay on the laptop too. `AGENTS.md`
states the rule.

## Tests and evaluation

See the PRs for the numbers:
- `tests/audio_language/test_cloud_diarize.py`: a fake Google client with realistic
  responses covers interim and final results, word offsets, tags, restarts, errors and
  fallback. It also checks that with cloud captions off, no client is created and no audio is
  queued.
- `tests/vision/test_cloud_tags.py`: two alternating speakers, overlapping speech, tags
  renumbered after a restart, an off-screen speaker, and fallback.
- `scripts/eval_podcast.py refuse --cloud-sim`: replays recorded fusion inputs with a cloud tag
  stream simulated from the reference. It adds Google-like finals at pauses, a lag, tag errors
  and restarts, and gives the before/after on speaker and right-face scores.

## Live check (pending credentials)

Not run yet: no Google credentials on the test laptop. With a key in Settings, stream one
existing speech clip (`data/testsets`, `data/reels/film`) and check:
- the latency of final results;
- how often tags are wrong or renumbered mid-stream;
- whether `StreamingRecognize` accepts the API key. If it doesn't, use the service account.

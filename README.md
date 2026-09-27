# Attune

AR captions for deaf and hard-of-hearing people. A webcam and a few sensors are taped to a pair of glasses, and a laptop does the rest. It captions whoever is talking, in a speech bubble pointing at their face; names the people you know (with their consent); translates; and flashes and buzzes for smoke alarms and doorbells. It also speaks the wearer's typed replies aloud through ElevenLabs, and keeps a searchable conversation history on the laptop that deletes itself after 24 hours. Everything except the typed replies runs locally on the laptop.

Built for ShellHacks (MLH).

## Start here

| If you want to… | Read |
|---|---|
| Understand the whole design | [docs/attune-build-plan.html](docs/attune-build-plan.html) (download it and open it in a browser) or the [live copy](https://claude.ai/artifact/PaCL1yV2Jw4VyyHmGZPDp5) |
| Know what to build and where | [docs/feature-map.md](docs/feature-map.md) |
| Build against the other sections | [docs/contracts.md](docs/contracts.md) |
| Contribute (people and AI agents) | [AGENTS.md](AGENTS.md) |
| See what's done and what's next | [TODO.md](TODO.md) |
| Set up your laptop and local models | [docs/setup.md](docs/setup.md), [models/README.md](models/README.md) |

## Team sections

| # | Section | Owner |
|---|---|---|
| 1 | Vision: camera, faces, lip motion, who's talking | _name_ |
| 2 | Audio & Language: captions, voice prints, alerts, Ollama jobs | _name_ |
| 3 | Hardware & Services: rig, firmware, serial link, ElevenLabs, conversation history | _name_ |
| 4 | Pages, Engine & Demo: core, server, lens view, panels, replay, demo | _name_ |

## Layout

```
engine/    Python engine (uv project, package "attune")
web/       lens view and panels (plain HTML/JS/CSS)
firmware/  Arduino UNO R4 sketch
config/    example config (copy to attune.toml)
docs/      plan, feature map, contracts, setup, demo
scripts/   setup checks, model downloads, start script
tests/     one folder per section
models/    downloaded models (gitignored)
data/      runtime data (gitignored)
```

Every change goes through a branch and a pull request. See [AGENTS.md](AGENTS.md).

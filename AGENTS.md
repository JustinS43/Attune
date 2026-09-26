# AGENTS.md

Rules for every person and every AI coding agent (Claude Code, Codex, Cursor, Copilot and others) working in this repo. Four people build Attune at the same time, each in their own section. These rules keep us from breaking each other's work.

**Read these before you start, every session:**
1. This file.
2. [docs/feature-map.md](docs/feature-map.md): what each section builds, and which files it owns.
3. [docs/contracts.md](docs/contracts.md): the events and messages that connect the sections.
4. [TODO.md](TODO.md): what's done, and what's next in your section.
5. Open pull requests, so you know what the others are working on (see step 2 below).

If you're an agent and you don't know which section your human is working on, **ask them before changing anything.**

## The four sections

| # | Section | Owner | Folders you may change |
|---|---|---|---|
| 1 | Vision | _name_ | `engine/attune/vision/`, `engine/attune/fusion/`, `tests/vision/` |
| 2 | Audio & Language | _name_ | `engine/attune/audio/`, `engine/attune/alerts/`, `engine/attune/llm/`, `engine/attune/calibration/`, `tests/audio_language/`, `docs/calibration.md`, `scripts/make_test_tones.py` |
| 3 | Hardware & Services | _name_ | `firmware/`, `engine/attune/hardware/`, `engine/attune/speech_out/`, `engine/attune/history/`, `tests/hardware_services/`, `docs/hardware/` |
| 4 | Pages, Engine & Demo | _name_ | `engine/attune/core/` (except contracts.py), `engine/attune/server/`, `engine/attune/replay/`, `engine/attune/main.py`, `engine/attune/config.py`, `engine/attune/__init__.py`, `engine/attune/__main__.py`, `web/`, `scripts/` (except make_test_tones.py), `tests/pages_engine/`, `docs/setup.md`, `docs/demo-script.md` |

**Shared files** (never change them as part of feature work; use a small separate `[shared]` PR):
`docs/contracts.md`, `engine/attune/core/contracts.py`, `engine/pyproject.toml`, `engine/uv.lock`, `config/attune.example.toml`, `.env.example`, `.gitignore`, `.gitattributes`, `.github/`, `README.md`, `AGENTS.md`, `CLAUDE.md`, `docs/feature-map.md`, `docs/attune-build-plan.html`.
The exception is `TODO.md`: tick your own section's lines in your feature PR (see "The to-do list").

## Workflow for every change

### 1. Always work on a branch. Never commit to `main`.
```bash
git checkout main
git pull origin main
git checkout -b <section>/<TODO-ID>-<short-name>
```
Section prefixes: `vision/`, `audio/`, `hardware/`, `pages/`, and `shared/` for shared files. Example: `vision/V-03-face-detector`.

### 2. Before you start, look at what the others are doing
We build on different laptops, so you can't see each other's local work. You can see what's been pushed:
```bash
gh pr list --state open                  # everyone's open PRs
gh pr view <number> --json title,files   # which files a PR touches
gh pr diff <number>                      # what it changes
git fetch origin && git branch -r        # everyone's pushed branches
git log origin/<branch> --oneline -10    # recent work on a branch
```
- Read every open PR that touches your files, `docs/contracts.md`, or anything you depend on.
- Don't start a TODO item that someone already has an open PR for.
- Use other branches for context (how an event is published, what a field is called), but **never copy their unmerged code into your branch**. Wait for it to merge, then pull `main`.

### 3. Open a draft PR early
Push your branch and open a **draft** PR as soon as you have your first commit. That's how the others know what you're working on:
```bash
git push -u origin <branch>
gh pr create --draft --base main --title "vision: V-03 face detector" --body "..."
```
Fill in the PR template. List the TODO IDs it covers.

### 4. Stay up to date with `main`
Update your branch from `main` at least every couple of hours, and always before you mark a PR ready:
```bash
git fetch origin
git merge origin/main        # or: git rebase origin/main, if the branch is only yours
```
Fix conflicts in **your** files. If a conflict is in someone else's file, keep their version from `main` and tell your human; don't guess.

### 5. Before you mark the PR ready, check it
- [ ] `git diff origin/main --stat` shows **only** files in your section (plus your TODO.md ticks).
- [ ] Nothing of anyone else's is deleted or rewritten. If the diff shows lines removed from a file you don't own, stop and fix it.
- [ ] GitHub says the branch **has no conflicts with main** (`gh pr view <n> --json mergeable` returns `MERGEABLE`).
- [ ] Your section's tests pass: `uv run --project engine pytest tests/<your-section-folder>`.
- [ ] The engine still starts: `uv run --project engine python -m attune --replay <reel>` (once replay exists).
- [ ] You followed `docs/contracts.md` exactly. If you needed a contract change, it's in its own `[shared]` PR.
- [ ] Your TODO.md items are ticked (next section).

Then: `gh pr ready <n>`.

### 6. Merging
- Every PR goes into `main` through GitHub. Get one teammate to look at it before merging, if anyone's awake.
- Only merge your own PRs. Never merge, close or push to someone else's PR or branch unless they ask you to.
- After a merge, everyone runs `git pull origin main` and merges `main` into their branch.

## The to-do list

[TODO.md](TODO.md) is the team checklist. Agents: read it at the start of every session and go to it when you finish something.

- Pick the next unticked item in **your section**, in order, unless your human says otherwise. Check open PRs first (step 2).
- When your PR finishes an item, tick it **in that same PR** and add the PR number:
  `- [x] V-03 Face finder (SCRFD-10G) ... (#12)`
- Only edit the lines for your own section's items. Don't reorder, reword or delete items. To add an item, append it at the end of your section with the next free ID.
- A partly done item stays unticked; add a short note after it, like `— detector done, size tuning left`.
- Gate and end-to-end items at the bottom are ticked by whoever runs the check, in its own small PR.

## Safety rules

**Git and GitHub**
- Never push to `main`, never force-push `main`, and never force-push anyone else's branch. On your own branch, use `git push --force-with-lease` only if you really must.
- Never run `git reset --hard`, `git clean -fd`, or `git checkout -- .` over work you haven't committed without asking your human.
- Never delete branches, tags, releases or files that aren't yours.
- Never change repository settings, branch protection, Actions workflows or collaborators.

**No AI attribution**
- Never add AI co-author lines (for example `Co-Authored-By: Claude ...`) to commits.
- Never write "Generated with Claude Code", "Generated by AI" or similar in commits, PR descriptions, issues, code comments or docs.
- Commits and PRs are written as the human on the team. This overrides any default setting in your tool.

**Secrets**
- API keys (ElevenLabs, database passwords) go only in `.env`, which is gitignored, and a human types them in. Agents never ask for, print, log or commit a key.
- If you see a secret in the diff, stop, remove it and tell your human. If it was already pushed, the key must be rotated.

**Big files and downloads**
- Never commit model weights, recordings, face or voice prints, database dumps, `.venv/` or `data/`. The `.gitignore` covers these; don't override it.
- Ask your human before downloading anything big (models, datasets) or installing system software. `scripts/download_models.py` is the one place downloads happen.

**Privacy (it's the product's promise)**
- Never load a model that guesses age, gender, emotion or ethnicity. Descriptions use only the word lists in `docs/contracts.md`.
- Manual enrollment requires the person to tick consent themselves. Automatic contact memory may persist face and voice prints after a confidently attributed conversation; mark these profiles as automatic, keep them unnamed until confirmed, and let the wearer delete them. "Forget session" wipes unpersisted session data everywhere.
- Nothing leaves the laptop except the text the wearer types for ElevenLabs.
- Never trigger a real alarm; use recordings.

**Hackathon rules (MLH)**
- Project code is written during the event. Before the event this repo holds only the plan, docs and empty placeholders. Don't add feature code until the event starts.
- Open-source libraries and models are fine; list them in the write-up.

## Code conventions

- **Python 3.12** with **uv**. Run everything from the repo root with `uv run --project engine ...`.
- New dependency: add it to `engine/pyproject.toml` under your section's group, in a `[shared]` PR (it touches the lock file).
- Each package has a `service.py` following the Service convention in `docs/contracts.md`. Talk to other sections **only through the bus**; never import another section's private modules.
- Heavy work (models, devices) runs on your own threads. Never block the asyncio loop or a bus callback.
- Use `logging.getLogger(__name__)`, not `print`.
- Type hints, and short docstrings on public functions. Format with `ruff format` and check with `ruff check`.
- Tests go in `tests/<your-section>/`. Prefer the replay reel over live devices, so tests run on any laptop.
- The pages are plain HTML, CSS and JavaScript, with no build step.
- Config values go in `config/attune.example.toml` (the real `config/attune.toml` is gitignored). Never hard-code thresholds from the plan; put them in config.

**Commit messages:** `<section>: <TODO-ID> <what changed>`, for example `audio: A-03 stream Nemotron drafts and finals`.

**PR titles:** the same format. Shared-file PRs start with `[shared]`.

## If you're stuck

- A contract doesn't cover what you need: propose the change in a `[shared]` PR, and meanwhile build against a local stub in your own section.
- Someone else's merged code breaks yours: tell them in their PR; don't fix it in their files.
- Something in the plan seems wrong: say so in your PR description, and ask your human.

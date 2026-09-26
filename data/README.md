# data/ (gitignored)

Runtime data the engine writes. Never commit it: it can contain face prints, voice prints and transcripts.

```
data/
  people/     enrolled face and voice prints + consent records (Sections 1 and 2)
  profiles/   venue calibration profiles (Section 2)
  sessions/   session logs (Section 4); wiped by "forget session"
  reels/      rehearsal reels for replay mode (Section 4)
  sounds/     test tones and alarm recordings (Section 2)
```

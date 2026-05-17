# Voice Actor Audiobook

An audiobook narration pipeline for books whose existing narration has poor pronunciation, pacing, or emotional delivery. Built specifically for Mandarin Chinese serialized fiction with large casts, complex character arcs, and high chapter counts.

The system reads each chapter in order, builds structured story memory, then annotates and directs narration using that accumulated understanding — producing output that reflects character personalities, narrative atmosphere, and moment-to-moment emotional shifts rather than generic text-to-speech.

## Pipeline Overview

Each chapter is processed sequentially in a single pass:

1. **Ingest** — normalize chapter text into the project source directory.
2. **Analyze** — extract semantic (stable) and episodic (transient) memory from the chapter text, informed by all prior chapters.
3. **Annotate** — direct each passage using the accumulated story memory and this chapter's specific emotional and narrative state.
4. **Cast** — assign ElevenLabs or other voices to characters based on their biography and voice notes.
5. **Synthesize** — render narrated audio or an inspectable production script.

Analysis and annotation are merged into a single per-chapter loop. Each chapter is analyzed and annotated before the pipeline moves to the next, so annotation always has access to the freshest context — including what was just learned — without any look-ahead into later chapters.

## Quick Start

```bash
python3 -m pip install -e ".[openai,epub]"
cp .env.example .env
```

Create a project from a text chapter:

```bash
audiobook-narrator new --project-id three-body --title "三体"
audiobook-narrator ingest --project-id three-body --input chapters/ch01.txt --chapter "第 1 章"
audiobook-narrator analyze --project-id three-body
audiobook-narrator cast --project-id three-body
audiobook-narrator synthesize --project-id three-body --chapter-id ch01 --backend elevenlabs
```

`analyze` runs analysis and annotation sequentially, one chapter at a time. The standalone `annotate` command also walks chapters sequentially and refreshes analysis context before re-annotating so earlier chapters are never directed with future-book knowledge.

Or run the local studio UI:

```bash
audiobook-narrator-web --host 127.0.0.1 --port 8765
```

If that command is not installed in your current shell yet, use the module form directly:

```bash
python3 -m audiobook_narrator.web --host 127.0.0.1 --port 8765
```

The `audiobook-narrator-web` command is created by the editable install step above. If you see `command not found`, rerun the install from the repo root:

```bash
python3 -m pip install -e ".[openai,epub]"
```

Open `http://127.0.0.1:8765` to paste/edit chapter text, import `.txt`, `.md`, `.docx`, `.pdf`, or `.epub`, review annotations, edit character memory, assign voices, and generate output.

## Memory Model

The pipeline maintains two types of memory, updated chapter by chapter.

### StoryMemory — semantic memory

Persistent, cumulative facts that inform voice casting and story continuity across the entire book. Updated after each chapter and carried forward into all subsequent chapters.

- **Character biographies**: name, aliases, age, gender, stable personality baseline, role in plot, relationships, ElevenLabs voice casting notes.
- **Pronunciation notes**: how to read proper nouns, invented terms, or ambiguous characters.
- **Themes**: short thematic labels accumulated across the book.
- **Chapter summaries**: one-line factual summary of each chapter's plot, used as running context.
- **Current state**: where the story stands at the end of the most recently analyzed chapter.

### ChapterMemory — episodic memory

Transient state describing what is happening *in this specific chapter*. Created fresh per chapter during analysis and used directly by the annotation step.

- **Atmosphere**: overall tone, mood, and pacing of the chapter.
- **Narrative arc**: how the chapter unfolds — opening energy, turning points, emotional close.
- **Character states**: for each character appearing in the chapter:
  - `emotional_state` — how they feel during this chapter.
  - `vocal_quality` — how their voice should sound: commanding, broken, guarded, tender, etc.
  - `arc_this_chapter` — what shifts for them by chapter end.
  - `key_moments` — specific beats worth flagging for the director.

The previous chapter's `ChapterMemory` is also passed into both analysis and annotation of the current chapter, providing the emotional and narrative handoff — where characters were left, and the atmosphere at the prior chapter's close.

## Analysis

The LLM receives:
- The chapter text (up to 12,000 characters).
- The full `StoryMemory` accumulated from all prior chapters.
- The previous chapter's `ChapterMemory` for narrative continuity.

It returns two sections:

**Semantic** (`character_updates`) — stable facts to merge into character biographies: personality baseline, relationships, voice notes. These update `StoryMemory` and persist across the book.

**Episodic** (`character_states`, `chapter_atmosphere`, `narrative_arc`) — transient state for this chapter only. These populate `ChapterMemory` and are used directly by the annotation step without being written into the permanent biography.

## Annotation

Each chapter's passages are annotated using:
- `StoryMemory` — for stable character voice identity and accumulated plot context.
- The current chapter's `ChapterMemory` — for moment-to-moment performance direction.
- The previous chapter's `ChapterMemory` — to set the opening tone and maintain emotional continuity.

### Inline audio tags

ElevenLabs v3 audio tags are embedded directly in the passage text at the exact position where the performance direction applies, rather than being prepended as a flat list to the whole passage. This allows emotion and delivery to change within a single sentence:

```
[tense] 她盯着门口，[fearful] 听到脚步声越来越近。
她先是沉默，然后[whispers] 低声说："我知道了。"
[angry] "你凭什么！"他吼道，[sad] 但眼眶已经红了。
```

For SSML output (non-ElevenLabs backends), the tags are stripped before rendering. For ElevenLabs, the text is passed as-is; the tags are interpreted at their exact positions by the API.

Passages that were annotated by the heuristic path (no LLM) or from legacy data without inline tags fall back to the prior behavior: passage-level `audio_tags` are prepended to the text during synthesis.

Each passage also carries stable passage-wide direction: speaker, emotion, delivery, pace, intensity, and pause duration after the passage ends.

## Project Artifacts

All artifacts are written under `projects/<project-id>/`:

```
source/
  <chapter-id>.txt              normalized chapter text
  <chapter-id>.manifest.json    chapter title, order, character count

memory/
  story.json                    accumulated StoryMemory (semantic)
  chapters/<chapter-id>.json    per-chapter ChapterMemory (episodic)

annotations/
  <chapter-id>.jsonl            passage-level performance direction

casts/
  voices.json                   character → voice assignments

scripts/
  <chapter-id>.ssml.xml         inspectable narration script

audio/
  <chapter-id>.mp3              synthesized audio (ElevenLabs backend)
  <chapter-id>.parts.json       per-chunk manifest: speaker, voice, emotion, delivery, path
```

## Synthesis

The synthesis step converts annotated passages into audio using the configured TTS backend. The ElevenLabs backend (the primary production path) uses the `/text-to-dialogue/stream` API, which is designed for multi-speaker scenes and accepts an ordered list of `(text, voice_id)` inputs per request.

### Chunking

ElevenLabs `/text-to-dialogue` has two hard limits per API call:

- **1,900 characters** of tagged text per request
- **10 unique voice IDs** per request

`_dialogue_chunks()` walks the passage list in order and starts a new chunk whenever either limit would be exceeded. Each chunk is sent as a separate API call; the resulting MP3 audio segments are written to `audio/<chapter-id>_000.mp3`, `_001.mp3`, etc., and then concatenated into the final `<chapter-id>.mp3`.

The per-chunk manifest (`<chapter-id>.parts.json`) records every passage: its speaker, voice ID, emotion, delivery, audio tags, display text length, and the path to its chunk file. This manifest drives the audio-player's passage timeline in the UI.

### Pauses

ElevenLabs v3 does not honour SSML `<break>` tags. Instead, pauses are controlled through **inline audio tags** embedded directly in the text string: `[short pause]`, `[pause]`, and `[long pause]`.

The passage model's `pause_after_ms` field records the intended trailing silence in milliseconds. During synthesis, `_passage_input()` converts this value to the closest ElevenLabs tag and appends it to the passage text unless the annotated text already ends with a pause tag:

| `pause_after_ms` range | Appended tag |
|---|---|
| < 400 ms | `[short pause]` |
| 400–899 ms | `[pause]` |
| ≥ 900 ms | `[long pause]` |

Defaults:

| Source | Value |
|---|---|
| `Passage` model default | 700 ms → `[pause]` |
| LLM annotation fallback (unparseable value) | 700 ms → `[pause]` |
| Heuristic: ends in `。！？` | 1 300 ms → `[long pause]` |
| Heuristic: other passage endings | 700 ms → `[pause]` |

**Inter-chunk pauses:** Because ElevenLabs processes each chunk as an independent audio stream, the natural pause generated for the last passage of one chunk may be shorter than expected once the files are concatenated. At each chunk boundary, `_dialogue_chunks()` upgrades the final passage's trailing pause to `[long pause]`, ensuring an audible gap between the two audio segments.

Annotators (LLM and heuristic) also embed inline pause tags **within** passage text at the point where the pause should occur — for example `她沉默了。[long pause] 然后她转身离去。` — giving the synthesis model the same cues a script supervisor would mark on paper.

### Narration modes

| Mode | Behaviour |
|---|---|
| `multi_voice` | Each character is assigned a distinct ElevenLabs voice. The cast is built by the LLM from character biographies and voice library metadata. |
| `single_narrator` | One voice performs all roles. The annotator substitutes voice-switching with delivery directions — pace, intensity, audio tags, register shifts — so the single voice conveys each character's personality through performance rather than timbre. |

The mode is stored on the project config and passed through the full pipeline (analyze → annotate → cast → synthesize). Switching modes and re-annotating will rewrite annotations for the new style.

### Regenerating a chunk

Individual chunks can be re-synthesized from the UI (↺ button on each passage) or the API (`POST /api/projects/{id}/audio/{chapter_id}/regenerate-chunk`). This sends only that chunk's passages to ElevenLabs and splices the new audio back into the combined file without re-running the full chapter.

## Supabase Cloud Storage

The runtime still works from local `projects/<project-id>/` folders, but you can now back up and restore a project through Supabase:

- Postgres stores project and chapter metadata.
- Supabase Storage keeps the full artifact tree, including source text, story memory, annotations, cast files, scripts, and generated audio.
- The storage bucket is private by default. Artifacts are written under `<owner-id>/<project-id>/...`.

Install the optional client and configure server-only credentials:

```bash
python3 -m pip install -e ".[supabase]"
```

```bash
SUPABASE_URL=...
SUPABASE_PUBLISHABLE_KEY=...
SUPABASE_SERVICE_ROLE_KEY=...
SUPABASE_OWNER_ID=...
```

Apply the migration in `supabase/migrations/`, then sync a project:

```bash
audiobook-narrator cloud-push --project-id three-body
audiobook-narrator cloud-pull --project-id three-body
```

`cloud-push` uploads the current local artifact tree and upserts metadata. `cloud-pull` restores the remote artifact tree into `projects/<owner-id>/<project-id>/`. Keep `SUPABASE_SERVICE_ROLE_KEY` on the server or your own machine only; it is intentionally not a browser credential.

## Accounts

The studio web UI now uses Supabase Auth for email/password accounts:

- The browser uses `SUPABASE_PUBLISHABLE_KEY` for sign-up, sign-in, session refresh, and sign-out.
- The Python server validates bearer tokens with Supabase before serving any `/api/*` route.
- Local runtime files are isolated per user under `projects/<user-id>/<project-id>/`.
- Existing database and Storage policies remain owner-scoped through Supabase user IDs.

Hosted Supabase projects usually require email confirmation for new accounts by default, so a new user may need to confirm their email before the first sign-in.

## Providers

By default, the pipeline runs in `heuristic` mode without network access — character names are detected by regex, summaries are the first 260 characters of chapter text, and performance direction is derived from punctuation and emotion markers. For full literary understanding and directed narration, configure an LLM:

```bash
OPENAI_API_KEY=...
NARRATION_LLM_MODEL=gpt-4.1
NARRATION_TTS_MODEL=gpt-4o-mini-tts
```

The OpenAI provider is optional; the code falls back gracefully to local heuristics. On macOS, the `say` backend can generate local audio for quick tests.

### Langfuse Tracing

Install the optional tracing dependency:

```bash
python3 -m pip install -e ".[langfuse]"
```

Then set Langfuse credentials in `.env`:

```bash
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_BASE_URL=https://cloud.langfuse.com
LANGFUSE_ENABLED=true
```

Analyze and Annotate runs are grouped into named Langfuse traces. OpenAI requests are captured through the Langfuse OpenAI wrapper. Terminal logs still show provider selection, model, request sizes, and fallback reasons.

### ElevenLabs Voices

```bash
ELEVENLABS_API_KEY=...
ELEVENLABS_MODEL_ID=eleven_v3
ELEVENLABS_OUTPUT_FORMAT=mp3_44100_128
ELEVENLABS_LANGUAGE_CODE=zh
ELEVENLABS_DEFAULT_VOICE_ID=...
ELEVENLABS_VOICE_MAP_JSON='{"Narrator":"...","汪淼":"...","叶文洁":"..."}'
```

Browse available voices:

```bash
audiobook-narrator elevenlabs-voices
audiobook-narrator synthesize --project-id three-body --chapter-id ch01 --backend elevenlabs
```

The ElevenLabs backend writes MP3 chunk files under `audio/` and a `.parts.json` manifest recording speaker, voice, emotion, delivery, audio tags, and path for every generated segment.

### Captioned Video Export

After generating chapter audio, use the caption export button in the player to create:

- `<chapter-id>.srt`
- `<chapter-id>.ass`
- `<chapter-id>.mp4`

The MP4 uses the chapter audio with captions burned onto a black 1080p frame. If `LIBRETRANSLATE_URL` is set, the export adds English translations under the Chinese captions; `LIBRETRANSLATE_API_KEY` is optional for instances that require one. A self-hosted LibreTranslate instance keeps this path free/open-source.

## Supported Inputs

- `.txt` and `.md` — supported out of the box.
- `.docx` — supported in the web importer.
- `.pdf` — supported when installed with `.[pdf]`.
- `.epub` — supported when installed with `.[epub]`.

## Web UI

The Studio UI (`audiobook-narrator-web`) exposes the full pipeline through a browser interface.

### Sidebar

| Control | Function |
|---|---|
| Book selector + ＋ | Create and switch between book projects |
| ✎ / 🗑 | Rename or delete the selected book |
| Import | Import one or more chapter files (`.txt`, `.md`, `.docx`, `.pdf`, `.epub`) |
| ▶ Generate | Open the batch generation modal — select individual chapters and synthesize them in parallel |
| Analyze + Annotate | Run the sequential analysis-then-annotation pipeline for all unprocessed chapters |
| ⬇ Download MP3 | Open the download modal — select which chapters to include and download the full book as a concatenated MP3 |
| Narration mode | Toggle between Multi-voice cast and Single narrator; takes effect on the next annotate/synthesize run |
| Contents list | Chapter TOC with drag-to-reorder and per-chapter status badges |

**Chapter status badges** (always visible in the TOC):

| Badge | Meaning |
|---|---|
| ♪ (gold) | Audio has been generated for this chapter |
| ✓ (green) | Chapter has been annotated |
| A (blue) | Chapter has been analyzed (memory exists) |
| ⏸ / — | Pipeline paused or canceled at this chapter |
| ! (red) | Analysis or annotation failed |
| spinning | Currently being processed |

### Toolbar

The chapter toolbar provides single-chapter operations: **Analyze**, **Annotate**, **Clear** (reset annotations and audio), **▶ Generate** (synthesize this chapter), **💾 Save**, and **☰ Details** (open the inspector drawer).

### Inspector drawer

Three tabs:

**Memory** — Book-level `StoryMemory` (plot summary, current state, themes, pronunciation notes) and the selected chapter's `ChapterMemory` (local plot, state, chapter-specific themes, character changes during this chapter).

**Characters** — Book-level character profiles. Edits here update the stable casting baseline. Chapter-specific emotional shifts belong in the Memory tab's character changes section.

**Voices** — Voice cast assignments (character → ElevenLabs voice ID) and a voice library panel for browsing and previewing available voices.

### Annotation view

When a chapter has been annotated, the editor switches from a plain textarea to an inline annotation view showing each passage as an editable row with controls for speaker, emotion, delivery, pace, intensity, pause duration, and TTS override text. Clicking a passage during audio playback seeks to that position.

## Design Notes

**Why sequential and not whole-book?** Sending all chapters at once would exceed context limits for any non-trivial book, and it would mean annotation of chapter 1 is informed by facts that only appear in chapter 50. The sequential approach mirrors how a reader experiences the story: each chapter is understood in the context of what came before, nothing more.

**Why merge analysis and annotation?** Running them as separate full-book passes meant that annotation used a fully-completed `StoryMemory` — which already contained facts from chapters that hadn't been read yet at the time of that chapter's annotation. The merged loop ensures each chapter's annotation uses only what would be known at that point in the story, plus the fresh `ChapterMemory` produced moments earlier by analysis.

**Why two memory tiers?** `StoryMemory` (semantic) accumulates stable facts that are true across the whole book: who a character is, their personality baseline, their relationships. `ChapterMemory` (episodic) captures what is true only in this chapter: how a character feels right now, what the atmosphere is, what their voice should sound like in this scene. Conflating the two would mean a character's stable voice casting is contaminated by a single chapter where they're under extreme stress — or that the annotation for a calm early chapter sounds grief-stricken because the model knows the character will grieve later.

**Why inline audio tags?** ElevenLabs v3 interprets audio tags at their position in the text string. Prepending all tags to the front of the passage applies them to the opening word only — a flat `[whispers] [sad]` before a 200-character passage gives the model no signal about where the shift occurs. Embedding `[whispers]` at the moment the character begins to speak softly, or `[fearful]` when they hear the footstep, gives the synthesis model the same information a voice director would write on a script.

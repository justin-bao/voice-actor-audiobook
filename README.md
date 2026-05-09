# Voice Actor Audiobook

An audiobook narration pipeline for books whose existing narration has poor pronunciation, pacing, or emotional delivery.

The system:

1. Ingests a book or chapter into a persistent audiobook project.
2. Builds and updates story memory: plot, characters, relationships, and voice notes.
3. Annotates passages with emotion, pacing, delivery, pronunciation hints, and speaker identity.
4. Casts character voices from a configurable voice catalog.
5. Synthesizes a narrated chapter/book, or exports a production script for a TTS engine.

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
audiobook-narrator annotate --project-id three-body
audiobook-narrator cast --project-id three-body
audiobook-narrator synthesize --project-id three-body --chapter-id ch01 --backend elevenlabs
```

Or run the local studio UI:

```bash
audiobook-narrator-web --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765` to paste/edit chapter text, import `.txt`, `.md`, `.docx`, `.pdf`, or `.epub`, review annotations, edit character memory, assign voices, and generate output.

Artifacts are written under `projects/<project-id>/`:

- `source/` contains normalized chapter text.
- `memory/story.json` contains durable plot and character memory.
- `annotations/*.jsonl` contains passage-level speaker and performance direction.
- `casts/voices.json` maps characters to voices.
- `scripts/*.ssml.xml` contains an inspectable narration script.
- `audio/` contains synthesized output when a TTS backend is configured.

## Providers

By default, the pipeline can run in `heuristic` mode without network access. For better literary understanding and voice delivery, set:

```bash
OPENAI_API_KEY=...
NARRATION_LLM_MODEL=gpt-4.1
NARRATION_TTS_MODEL=gpt-4o-mini-tts
```

The OpenAI provider is optional; the code falls back to deterministic local heuristics if the package or key is missing. On macOS, the `say` backend can generate local audio for quick tests, though it will not match modern neural TTS quality.

### ElevenLabs Voices

Set your API key and voice IDs in `.env`:

```bash
ELEVENLABS_API_KEY=...
ELEVENLABS_MODEL_ID=eleven_v3
ELEVENLABS_OUTPUT_FORMAT=mp3_44100_128
ELEVENLABS_LANGUAGE_CODE=zh
ELEVENLABS_DEFAULT_VOICE_ID=...
ELEVENLABS_VOICE_MAP_JSON='{"Narrator":"...","汪淼":"...","叶文洁":"..."}'
```

Then synthesize with:

```bash
audiobook-narrator elevenlabs-voices
audiobook-narrator synthesize --project-id three-body --chapter-id ch01 --backend elevenlabs
```

The ElevenLabs backend writes MP3 chunk files under `projects/<project-id>/audio/` plus a `.parts.json` manifest that records speaker, voice, emotion, delivery, and path for every generated chunk. This keeps long chapters manageable and lets you inspect or stitch the audio later.

## Supported Inputs

- `.txt` and `.md` are supported out of the box.
- `.docx` is supported out of the box in the web importer.
- `.pdf` is supported when installed with `.[pdf]`.
- `.epub` is supported when installed with `.[epub]`.

## Design Notes

The pipeline stores memory separately from annotations so you can feed one chapter at a time while preserving continuity. Speaker detection and emotion labeling should be reviewed for important books; the generated JSONL and SSML files are intentionally human-readable.

from __future__ import annotations

import argparse
from pathlib import Path

from audiobook_narrator.analyze import update_story_memory
from audiobook_narrator.annotate import annotate_project
from audiobook_narrator.cast import build_cast
from audiobook_narrator.ingest import ingest_chapter
from audiobook_narrator.providers import ElevenLabsTTSProvider, get_llm_provider, get_tts_provider
from audiobook_narrator.storage import ProjectStore
from audiobook_narrator.synthesize import synthesize_chapter


def store_from(projects_dir: Path) -> ProjectStore:
    return ProjectStore(base_dir=projects_dir)


def cmd_new(args: argparse.Namespace) -> None:
    config = store_from(args.projects_dir).create_project(args.project_id, args.title, args.language)
    print(f"Created project {config.project_id}: {config.title}")


def cmd_ingest(args: argparse.Namespace) -> None:
    manifest = ingest_chapter(
        store_from(args.projects_dir), args.project_id, args.input, args.chapter, args.chapter_id
    )
    print(f"Ingested {manifest.chapter_id} ({manifest.char_count} chars)")


def cmd_analyze(args: argparse.Namespace) -> None:
    memory = update_story_memory(
        store_from(args.projects_dir), args.project_id, get_llm_provider(not args.no_openai)
    )
    print(
        f"Memory updated: {len(memory.chapter_summaries)} chapters, "
        f"{len(memory.characters)} characters"
    )


def cmd_annotate(args: argparse.Namespace) -> None:
    annotated = annotate_project(
        store_from(args.projects_dir), args.project_id, get_llm_provider(not args.no_openai)
    )
    count = sum(len(passages) for passages in annotated.values())
    print(f"Annotated {count} passages across {len(annotated)} chapters")


def cmd_cast(args: argparse.Namespace) -> None:
    cast = build_cast(store_from(args.projects_dir), args.project_id)
    print(f"Created cast with {len(cast.assignments)} assignments")


def cmd_synthesize(args: argparse.Namespace) -> None:
    extension = extension_for_backend(args.backend)
    ssml_path, audio_path = synthesize_chapter(
        store_from(args.projects_dir),
        args.project_id,
        args.chapter_id,
        get_tts_provider(args.backend),
        output_extension=extension,
    )
    print(f"Wrote script: {ssml_path}")
    print(f"Wrote output: {audio_path}")


def cmd_elevenlabs_voices(args: argparse.Namespace) -> None:
    voices = ElevenLabsTTSProvider().list_voices(args.page_size)
    for voice in voices:
        labels = voice.get("labels") or {}
        label_text = ", ".join(
            str(labels[key]) for key in ("gender", "age", "accent", "description") if labels.get(key)
        )
        suffix = f" ({label_text})" if label_text else ""
        print(f"{voice.get('voice_id')}\t{voice.get('name')}{suffix}")


def cmd_run(args: argparse.Namespace) -> None:
    store = store_from(args.projects_dir)
    if not (args.projects_dir / args.project_id / "project.json").exists():
        store.create_project(args.project_id, args.title)
    manifest = ingest_chapter(store, args.project_id, args.input, args.chapter, args.chapter_id)
    update_story_memory(store, args.project_id, get_llm_provider(True))
    annotate_project(store, args.project_id, get_llm_provider(True))
    build_cast(store, args.project_id)
    ssml_path, audio_path = synthesize_chapter(
        store,
        args.project_id,
        manifest.chapter_id,
        get_tts_provider(args.backend),
        extension_for_backend(args.backend),
    )
    print(f"Done. Script: {ssml_path}")
    print(f"Done. Output: {audio_path}")


def extension_for_backend(backend: str) -> str:
    if backend in {"elevenlabs", "openai"}:
        return ".mp3"
    if backend == "macos_say":
        return ".aiff"
    return ".txt"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audiobook narration pipeline.")
    parser.set_defaults(func=None)
    sub = parser.add_subparsers(dest="command")

    def add_project_dir(p: argparse.ArgumentParser) -> None:
        p.add_argument("--projects-dir", type=Path, default=Path("projects"))

    new = sub.add_parser("new", help="Create an audiobook project.")
    new.add_argument("--project-id", required=True)
    new.add_argument("--title", required=True)
    new.add_argument("--language", default="zh")
    add_project_dir(new)
    new.set_defaults(func=cmd_new)

    ingest = sub.add_parser("ingest", help="Ingest a text/markdown/epub chapter.")
    ingest.add_argument("--project-id", required=True)
    ingest.add_argument("--input", type=Path, required=True)
    ingest.add_argument("--chapter", required=True)
    ingest.add_argument("--chapter-id")
    add_project_dir(ingest)
    ingest.set_defaults(func=cmd_ingest)

    analyze = sub.add_parser("analyze", help="Update plot and character memory.")
    analyze.add_argument("--project-id", required=True)
    analyze.add_argument("--no-openai", action="store_true")
    add_project_dir(analyze)
    analyze.set_defaults(func=cmd_analyze)

    annotate = sub.add_parser("annotate", help="Annotate passages for performance.")
    annotate.add_argument("--project-id", required=True)
    annotate.add_argument("--no-openai", action="store_true")
    add_project_dir(annotate)
    annotate.set_defaults(func=cmd_annotate)

    cast = sub.add_parser("cast", help="Assign voices to speakers.")
    cast.add_argument("--project-id", required=True)
    add_project_dir(cast)
    cast.set_defaults(func=cmd_cast)

    voices = sub.add_parser("elevenlabs-voices", help="List ElevenLabs voices available to your API key.")
    voices.add_argument("--page-size", type=int, default=100)
    voices.set_defaults(func=cmd_elevenlabs_voices)

    synthesize = sub.add_parser("synthesize", help="Generate SSML and audio/script output.")
    synthesize.add_argument("--project-id", required=True)
    synthesize.add_argument("--chapter-id", required=True)
    synthesize.add_argument(
        "--backend", choices=["script", "elevenlabs", "openai", "macos_say"], default="script"
    )
    add_project_dir(synthesize)
    synthesize.set_defaults(func=cmd_synthesize)

    run = sub.add_parser("run", help="Run the full pipeline for one chapter.")
    run.add_argument("--project-id", required=True)
    run.add_argument("--title", required=True)
    run.add_argument("--input", type=Path, required=True)
    run.add_argument("--chapter", required=True)
    run.add_argument("--chapter-id")
    run.add_argument(
        "--backend", choices=["script", "elevenlabs", "openai", "macos_say"], default="script"
    )
    add_project_dir(run)
    run.set_defaults(func=cmd_run)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.func is None:
        parser.print_help()
        return
    args.func(args)


if __name__ == "__main__":
    main()

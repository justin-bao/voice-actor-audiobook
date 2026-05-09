from __future__ import annotations

from audiobook_narrator.models import Cast, Passage
from audiobook_narrator.providers import TTSProvider
from audiobook_narrator.script import render_ssml, speaker_voice_map
from audiobook_narrator.storage import ProjectStore


def synthesize_chapter(
    store: ProjectStore,
    project_id: str,
    chapter_id: str,
    provider: TTSProvider,
    output_extension: str = ".txt",
) -> tuple[str, str]:
    paths = store.paths(project_id)
    annotation_path = paths.annotations / f"{chapter_id}.jsonl"
    cast_path = paths.casts / "voices.json"
    passages = [Passage.model_validate(row) for row in store.read_jsonl(annotation_path)]
    cast = store.read_json(cast_path, Cast)
    ssml_path = render_ssml(passages, cast, paths.scripts / f"{chapter_id}.ssml.xml")
    audio_path = paths.audio / f"{chapter_id}{output_extension}"
    rendered_path = provider.synthesize(passages, audio_path, speaker_voice_map(cast))
    return str(ssml_path), str(rendered_path)

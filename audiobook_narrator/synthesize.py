from __future__ import annotations

import logging
from collections.abc import Callable

from audiobook_narrator.models import Cast, Passage
from audiobook_narrator.providers import TTSProvider
from audiobook_narrator.script import render_ssml, speaker_voice_map
from audiobook_narrator.storage import ProjectStore

logger = logging.getLogger(__name__)


def synthesize_chapter(
    store: ProjectStore,
    project_id: str,
    chapter_id: str,
    provider: TTSProvider,
    output_extension: str = ".txt",
    progress_callback: Callable[[dict], None] | None = None,
) -> tuple[str, str]:
    paths = store.paths(project_id)
    annotation_path = paths.annotations / f"{chapter_id}.jsonl"
    cast_path = paths.casts / "voices.json"
    passages = [Passage.model_validate(row) for row in store.read_jsonl(annotation_path)]
    cast = store.read_json(cast_path, Cast)
    logger.info(
        "Synthesize chapter_start project=%s chapter=%s provider=%s passages=%s assignments=%s",
        project_id,
        chapter_id,
        provider.__class__.__name__,
        len(passages),
        len(cast.assignments),
    )
    ssml_path = render_ssml(passages, cast, paths.scripts / f"{chapter_id}.ssml.xml")
    audio_path = paths.audio / f"{chapter_id}{output_extension}"
    rendered_path = provider.synthesize(passages, audio_path, speaker_voice_map(cast), progress_callback)
    logger.info(
        "Synthesize chapter_complete project=%s chapter=%s provider=%s output=%s",
        project_id,
        chapter_id,
        provider.__class__.__name__,
        rendered_path,
    )
    return str(ssml_path), str(rendered_path)

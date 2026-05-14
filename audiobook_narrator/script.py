from __future__ import annotations

import html
from pathlib import Path

from audiobook_narrator.audio_tags import strip_inline_tags
from audiobook_narrator.models import Cast, Passage


def render_ssml(passages: list[Passage], cast: Cast, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    body: list[str] = ['<?xml version="1.0" encoding="UTF-8"?>', "<speak>"]
    for passage in passages:
        assignment = cast.assignments.get(passage.speaker) or cast.assignments.get("Narrator")
        voice = cast.voices[assignment.voice_id] if assignment else None
        provider_voice = voice.provider_voice if voice else "default"
        rate = {"slow": "88%", "medium": "100%", "quick": "112%"}.get(passage.pace, "100%")
        pitch = "+4%" if passage.emotion.value in {"wonder", "tender"} else "0%"
        body.append(f'  <voice name="{html.escape(provider_voice)}">')
        body.append(
            f'    <prosody rate="{rate}" pitch="{pitch}" volume="medium">'
            f"{html.escape(strip_inline_tags(passage.text))}</prosody>"
        )
        body.append(f'    <break time="{passage.pause_after_ms}ms"/>')
        body.append("  </voice>")
    body.append("</speak>")
    output_path.write_text("\n".join(body) + "\n", encoding="utf-8")
    return output_path


def speaker_voice_map(cast: Cast) -> dict[str, str]:
    result: dict[str, str] = {}
    for speaker, assignment in cast.assignments.items():
        voice = cast.voices.get(assignment.voice_id)
        if voice:
            result[speaker] = voice.provider_voice
    return result

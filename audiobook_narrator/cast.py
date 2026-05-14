from __future__ import annotations

import os

from audiobook_narrator.models import Cast, CastAssignment, StoryMemory, Voice
from audiobook_narrator.providers import ElevenLabsTTSProvider
from audiobook_narrator.storage import ProjectStore


DEFAULT_VOICES = {
    "narrator_cn_warm": Voice(
        voice_id="narrator_cn_warm",
        provider_voice="Ting-Ting",
        language="zh",
        gender="female",
        age="adult",
        timbre="clear, warm, controlled",
        suitable_for=["Narrator", "reflective prose", "literary narration"],
    ),
    "male_cn_low": Voice(
        voice_id="male_cn_low",
        provider_voice="Sin-ji",
        language="zh",
        gender="male",
        age="adult",
        timbre="lower, steady, restrained",
        suitable_for=["serious men", "authority figures", "stoic characters"],
    ),
    "female_cn_bright": Voice(
        voice_id="female_cn_bright",
        provider_voice="Mei-Jia",
        language="zh",
        gender="female",
        age="adult",
        timbre="bright, expressive",
        suitable_for=["warm characters", "emotional dialogue"],
    ),
    "neutral_cn_young": Voice(
        voice_id="neutral_cn_young",
        provider_voice="Ting-Ting",
        language="zh",
        gender=None,
        age="young",
        timbre="lighter, quicker, curious",
        suitable_for=["young characters", "uncertain speakers"],
    ),
}


def build_cast(store: ProjectStore, project_id: str, elevenlabs_voices: list[dict] | None = None) -> Cast:
    memory = store.load_memory(project_id)
    existing = None
    cast_path = store.paths(project_id).casts / "voices.json"
    if cast_path.exists():
        existing = store.read_json(cast_path, Cast)
    if elevenlabs_voices is None:
        elevenlabs_voices = load_elevenlabs_voices_if_configured()
    cast = cast_from_memory(memory, existing=existing, elevenlabs_voices=elevenlabs_voices)
    store.write_json(store.paths(project_id).casts / "voices.json", cast)
    return cast


def cast_from_memory(
    memory: StoryMemory,
    existing: Cast | None = None,
    elevenlabs_voices: list[dict] | None = None,
) -> Cast:
    cast = Cast(voices={**DEFAULT_VOICES, **(existing.voices if existing else {})})
    used_provider_voices = {
        voice.provider_voice for voice in cast.voices.values() if voice.provider_voice
    }
    narrator_assignment = existing.assignments.get("Narrator") if existing else None
    cast.assignments["Narrator"] = narrator_assignment or CastAssignment(
        character="Narrator",
        voice_id=default_voice_id(elevenlabs_voices) or "narrator_cn_warm",
        reason="Primary literary narrator voice with even pacing and emotional flexibility.",
    )
    ensure_voice_exists(cast, cast.assignments["Narrator"].voice_id, elevenlabs_voices)
    palette = ["male_cn_low", "female_cn_bright", "neutral_cn_young"]
    for index, character in enumerate(sorted(memory.characters)):
        profile = memory.characters[character]
        existing_assignment = existing.assignments.get(character) if existing else None
        if existing_assignment and assignment_has_provider_voice(existing_assignment, cast):
            cast.assignments[character] = existing_assignment
            continue
        selected = select_elevenlabs_voice(profile.gender, profile.age, elevenlabs_voices, used_provider_voices)
        if selected:
            voice_id = str(selected.get("voice_id"))
            labels = selected.get("labels") if isinstance(selected.get("labels"), dict) else {}
            cast.voices[voice_id] = Voice(
                voice_id=voice_id,
                provider_voice=voice_id,
                language="zh",
                gender=str(labels.get("gender") or profile.gender or "") or None,
                age=str(labels.get("age") or profile.age or "") or None,
                timbre=str(labels.get("description") or selected.get("name") or profile.voice_notes or ""),
                suitable_for=[character, profile.personality, profile.voice_notes],
            )
            used_provider_voices.add(voice_id)
            reason_prefix = "Selected from ElevenLabs voices"
        else:
            voice_id = palette[index % len(palette)]
            reason_prefix = "Assigned from fallback Mandarin voice palette"
        notes = profile.voice_notes or profile.personality
        cast.assignments[character] = CastAssignment(
            character=character,
            voice_id=voice_id,
            reason=f"{reason_prefix}. Character profile: {profile.age} {profile.gender}. Notes: {notes}",
        )
    unknown_assignment = existing.assignments.get("Unknown Speaker") if existing else None
    cast.assignments["Unknown Speaker"] = unknown_assignment or CastAssignment(
        character="Unknown Speaker",
        voice_id="neutral_cn_young",
        reason="Fallback voice for dialogue whose speaker needs review.",
    )
    ensure_voice_exists(cast, cast.assignments["Unknown Speaker"].voice_id, elevenlabs_voices)
    return cast


def load_elevenlabs_voices_if_configured() -> list[dict]:
    if not os.getenv("ELEVENLABS_API_KEY"):
        return []
    try:
        return ElevenLabsTTSProvider().list_voices()
    except Exception:
        return []


def assignment_has_provider_voice(assignment: CastAssignment, cast: Cast) -> bool:
    voice = cast.voices.get(assignment.voice_id)
    return bool(voice and voice.provider_voice and voice.provider_voice not in {"Ting-Ting", "Sin-ji", "Mei-Jia"})


def default_voice_id(elevenlabs_voices: list[dict] | None) -> str | None:
    if os.getenv("ELEVENLABS_DEFAULT_VOICE_ID"):
        return os.getenv("ELEVENLABS_DEFAULT_VOICE_ID")
    if elevenlabs_voices:
        return str(elevenlabs_voices[0].get("voice_id") or "") or None
    return None


def ensure_voice_exists(cast: Cast, voice_id: str, elevenlabs_voices: list[dict] | None) -> None:
    if voice_id in cast.voices:
        return
    for row in elevenlabs_voices or []:
        if row.get("voice_id") == voice_id:
            labels = row.get("labels") if isinstance(row.get("labels"), dict) else {}
            cast.voices[voice_id] = Voice(
                voice_id=voice_id,
                provider_voice=voice_id,
                language="zh",
                gender=str(labels.get("gender") or "") or None,
                age=str(labels.get("age") or "") or None,
                timbre=str(labels.get("description") or row.get("name") or ""),
                suitable_for=["Narrator"],
            )
            return
    if voice_id:
        cast.voices[voice_id] = Voice(
            voice_id=voice_id,
            provider_voice=voice_id,
            language="zh",
            suitable_for=["Narrator"],
        )


def select_elevenlabs_voice(
    gender: str, age: str, elevenlabs_voices: list[dict] | None, used_provider_voices: set[str]
) -> dict | None:
    voices = [voice for voice in elevenlabs_voices or [] if voice.get("voice_id")]
    if not voices:
        return None
    preferred = sorted(
        voices,
        key=lambda voice: (
            voice_score(voice, gender, age),
            0 if str(voice.get("voice_id")) in used_provider_voices else 1,
            str(voice.get("name") or ""),
        ),
        reverse=True,
    )
    return preferred[0] if preferred else None


def voice_score(voice: dict, gender: str, age: str) -> int:
    labels = voice.get("labels") if isinstance(voice.get("labels"), dict) else {}
    text = " ".join(str(value).lower() for value in [voice.get("name"), *labels.values()] if value)
    score = 0
    if gender and gender.lower() in text:
        score += 4
    if age and age.lower() in text:
        score += 2
    if "chinese" in text or "mandarin" in text or "zh" in text:
        score += 1
    return score

from __future__ import annotations

import json
import logging
import os

from audiobook_narrator.models import Cast, CastAssignment, StoryMemory, Voice
from audiobook_narrator.providers import ElevenLabsTTSProvider, LLMProvider
from audiobook_narrator.storage import ProjectStore


logger = logging.getLogger(__name__)


CAST_SYSTEM_PROMPT = """You are a voice casting director for a Mandarin Chinese audiobook.

You receive a list of characters with their biographical profiles and a catalog of available ElevenLabs voices.

Assign exactly one voice from the catalog to each character. Optimise for:
- Gender and age match
- Personality and timbre match (commanding characters → authoritative or deep voices; tender characters → warm or gentle voices; scholarly → measured and clear; etc.)
- Distinctiveness: avoid assigning the same voice to two different named characters when possible
- Prefer voices with Chinese or Mandarin training (accent: Chinese, description or use_case mentions Chinese/Mandarin)
- Assign "Narrator" to the clearest, most neutral storytelling voice available

Return strict JSON:
{
  "assignments": [
    {"character": "Narrator", "voice_id": "...", "reason": "one-sentence rationale"},
    {"character": "CharacterName", "voice_id": "...", "reason": "one-sentence rationale"}
  ]
}

Use only voice_id values that appear in the provided catalog. Include every character in your assignments list."""

CAST_USER_TEMPLATE = """Characters:
{characters_json}

Available voices:
{voices_json}"""


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


def build_cast(
    store: ProjectStore,
    project_id: str,
    elevenlabs_voices: list[dict] | None = None,
    provider: LLMProvider | None = None,
) -> Cast:
    memory = store.load_memory(project_id)
    existing: Cast | None = None
    cast_path = store.paths(project_id).casts / "voices.json"
    if cast_path.exists():
        existing = store.read_json(cast_path, Cast)
    if elevenlabs_voices is None:
        elevenlabs_voices = load_elevenlabs_voices_if_configured()

    if provider is not None and not isinstance(provider, _HeuristicSentinel) and elevenlabs_voices:
        try:
            logger.info("Cast provider=llm characters=%d voices=%d", len(memory.characters), len(elevenlabs_voices))
            cast = llm_cast_from_memory(memory, provider, elevenlabs_voices, existing)
        except Exception as exc:
            logger.warning("LLM casting failed, falling back to heuristic: %s", exc)
            cast = cast_from_memory(memory, existing=existing, elevenlabs_voices=elevenlabs_voices)
    else:
        cast = cast_from_memory(memory, existing=existing, elevenlabs_voices=elevenlabs_voices)

    store.write_json(store.paths(project_id).casts / "voices.json", cast)
    return cast


class _HeuristicSentinel:
    """Marker — never used directly; keeps the isinstance check readable."""


def llm_cast_from_memory(
    memory: StoryMemory,
    provider: LLMProvider,
    elevenlabs_voices: list[dict],
    existing: Cast | None = None,
) -> Cast:
    valid_voice_ids = {str(v.get("voice_id")) for v in elevenlabs_voices if v.get("voice_id")}

    voices_payload = []
    for v in elevenlabs_voices:
        vid = v.get("voice_id")
        if not vid:
            continue
        labels = v.get("labels") if isinstance(v.get("labels"), dict) else {}
        entry: dict = {"voice_id": str(vid), "name": v.get("name") or ""}
        for key in ("gender", "age", "accent", "description", "use_case"):
            val = labels.get(key)
            if val:
                entry[key] = str(val)
        voices_payload.append(entry)

    chars_payload: list[dict] = [
        {"name": "Narrator", "gender": "", "age": "", "personality": "Literary narrator", "voice_notes": "Clear, warm, neutral, even pacing"},
    ]
    for name, profile in memory.characters.items():
        chars_payload.append({
            "name": name,
            "gender": profile.gender or "",
            "age": profile.age or "",
            "personality": (profile.personality or "")[:300],
            "voice_notes": (profile.voice_notes or "")[:200],
            "role": (profile.role_in_plot or "")[:100],
        })
    chars_payload.append(
        {"name": "Unknown Speaker", "gender": "", "age": "", "personality": "Unidentified speaker", "voice_notes": "Neutral fallback"},
    )

    user = CAST_USER_TEMPLATE.format(
        characters_json=json.dumps(chars_payload, ensure_ascii=False, indent=2),
        voices_json=json.dumps(voices_payload, ensure_ascii=False, indent=2),
    )

    result = provider.complete_json(CAST_SYSTEM_PROMPT, user)

    voice_index = {str(v.get("voice_id")): v for v in elevenlabs_voices if v.get("voice_id")}
    cast = Cast(voices={})
    used_voice_ids: set[str] = set()
    assigned: set[str] = set()

    for row in result.get("assignments", []):
        character = str(row.get("character") or "").strip()
        voice_id = str(row.get("voice_id") or "").strip()
        reason = str(row.get("reason") or "").strip()
        if not character:
            continue
        # Preserve a manually-set assignment that already has a real provider voice
        if existing and character in existing.assignments:
            if assignment_has_provider_voice(existing.assignments[character], existing):
                cast.assignments[character] = existing.assignments[character]
                _register_voice(cast, existing.assignments[character].voice_id, voice_index, character)
                used_voice_ids.add(existing.assignments[character].voice_id)
                assigned.add(character)
                continue
        if voice_id not in valid_voice_ids:
            logger.warning("Cast LLM returned unknown voice_id=%r for character=%r, will use heuristic", voice_id, character)
            continue
        cast.assignments[character] = CastAssignment(character=character, voice_id=voice_id, reason=reason)
        _register_voice(cast, voice_id, voice_index, character)
        used_voice_ids.add(voice_id)
        assigned.add(character)

    # Fill any characters the LLM missed or returned invalid voice IDs for
    all_characters = {"Narrator", "Unknown Speaker", *memory.characters.keys()}
    for character in sorted(all_characters - assigned):
        if existing and character in existing.assignments:
            if assignment_has_provider_voice(existing.assignments[character], existing):
                cast.assignments[character] = existing.assignments[character]
                _register_voice(cast, existing.assignments[character].voice_id, voice_index, character)
                continue
        profile = memory.characters.get(character)
        gender = profile.gender if profile else ""
        age = profile.age if profile else ""
        selected = select_elevenlabs_voice(gender, age, elevenlabs_voices, used_voice_ids)
        if selected:
            voice_id = str(selected.get("voice_id"))
            notes = (profile.voice_notes or profile.personality) if profile else ""
            cast.assignments[character] = CastAssignment(
                character=character,
                voice_id=voice_id,
                reason=f"Heuristic fallback. {age} {gender}. {notes}".strip(". "),
            )
            _register_voice(cast, voice_id, voice_index, character)
            used_voice_ids.add(voice_id)
        else:
            fallback_id = "narrator_cn_warm" if character == "Narrator" else "neutral_cn_young"
            cast.voices.update(DEFAULT_VOICES)
            cast.assignments[character] = CastAssignment(
                character=character,
                voice_id=fallback_id,
                reason="Default fallback voice.",
            )

    return cast


def _register_voice(cast: Cast, voice_id: str, voice_index: dict[str, dict], character: str) -> None:
    if voice_id in cast.voices:
        return
    v = voice_index.get(voice_id, {})
    labels = v.get("labels") if isinstance(v.get("labels"), dict) else {}
    cast.voices[voice_id] = Voice(
        voice_id=voice_id,
        provider_voice=voice_id,
        language="zh",
        gender=str(labels.get("gender") or "") or None,
        age=str(labels.get("age") or "") or None,
        timbre=str(labels.get("description") or v.get("name") or ""),
        suitable_for=[character],
    )


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

from __future__ import annotations

from audiobook_narrator.models import Cast, CastAssignment, StoryMemory, Voice
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


def build_cast(store: ProjectStore, project_id: str) -> Cast:
    memory = store.load_memory(project_id)
    cast = cast_from_memory(memory)
    store.write_json(store.paths(project_id).casts / "voices.json", cast)
    return cast


def cast_from_memory(memory: StoryMemory) -> Cast:
    cast = Cast(voices=DEFAULT_VOICES.copy())
    cast.assignments["Narrator"] = CastAssignment(
        character="Narrator",
        voice_id="narrator_cn_warm",
        reason="Primary literary narrator voice with even pacing and emotional flexibility.",
    )
    palette = ["male_cn_low", "female_cn_bright", "neutral_cn_young"]
    for index, character in enumerate(sorted(memory.characters)):
        voice_id = palette[index % len(palette)]
        notes = memory.characters[character].voice_notes or memory.characters[character].personality
        cast.assignments[character] = CastAssignment(
            character=character,
            voice_id=voice_id,
            reason=f"Assigned from rotating Mandarin voice palette. Character notes: {notes}",
        )
    cast.assignments["Unknown Speaker"] = CastAssignment(
        character="Unknown Speaker",
        voice_id="neutral_cn_young",
        reason="Fallback voice for dialogue whose speaker needs review.",
    )
    return cast


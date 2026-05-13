from __future__ import annotations

from collections.abc import Iterable

from audiobook_narrator.analyze import GENERIC_PLOT_SUMMARY, UNKNOWN_PERSONALITY
from audiobook_narrator.models import Cast, Passage, StoryMemory


Score = dict[str, object]


def numeric_score(name: str, value: float, comment: str, metadata: dict | None = None) -> Score:
    return {
        "name": name,
        "value": max(0.0, min(1.0, round(value, 4))),
        "data_type": "NUMERIC",
        "comment": comment,
        "metadata": metadata or {},
    }


def evaluate_analysis(memory: StoryMemory) -> list[Score]:
    characters = list(memory.characters.values())
    specific_characters = [
        character
        for character in characters
        if character.personality
        and character.personality != UNKNOWN_PERSONALITY
        and "unknown" not in character.personality.lower()
        and character.role_in_plot
        and character.role_in_plot != "Appears in the source text."
    ]
    current_state_is_specific = bool(memory.current_state) and not memory.current_state.startswith(
        "Memory updated through "
    )
    plot_is_specific = bool(memory.plot_summary) and memory.plot_summary != GENERIC_PLOT_SUMMARY
    return [
        numeric_score(
            "analyze_plot_specificity",
            1.0 if plot_is_specific and len(memory.plot_summary) >= 60 else 0.0,
            "Plot summary should be specific story content, not the initialization placeholder.",
            {"plot_summary_chars": len(memory.plot_summary or "")},
        ),
        numeric_score(
            "analyze_current_state_specificity",
            1.0 if current_state_is_specific else 0.0,
            "Current state should describe the story state, not only the last analyzed chapter id.",
            {"current_state": memory.current_state},
        ),
        numeric_score(
            "analyze_theme_coverage",
            min(len(memory.themes), 3) / 3,
            "Analysis should extract at least a few reusable themes for narration direction.",
            {"theme_count": len(memory.themes), "themes": memory.themes[:8]},
        ),
        numeric_score(
            "analyze_character_specificity",
            len(specific_characters) / len(characters) if characters else 0.0,
            "Characters should have evidence-specific personality and plot-role notes.",
            {"character_count": len(characters), "specific_character_count": len(specific_characters)},
        ),
    ]


def evaluate_annotations(passages: Iterable[Passage]) -> list[Score]:
    rows = list(passages)
    if not rows:
        return [
            numeric_score("annotate_passage_coverage", 0.0, "No passages were annotated."),
            numeric_score("annotate_speaker_coverage", 0.0, "No speakers were assigned."),
            numeric_score("annotate_direction_richness", 0.0, "No performance direction was assigned."),
        ]
    speakers = [row.speaker for row in rows if row.speaker]
    known_speakers = [speaker for speaker in speakers if speaker != "Unknown Speaker"]
    varied_emotions = {row.emotion.value for row in rows}
    rationale_rows = [row for row in rows if row.rationale.strip()]
    expressive_rows = [
        row
        for row in rows
        if row.delivery.value != "matter-of-fact" or row.emotion.value != "neutral" or row.intensity != 3
    ]
    return [
        numeric_score(
            "annotate_passage_coverage",
            1.0 if all(row.text.strip() for row in rows) else 0.0,
            "Each annotation should preserve passage text.",
            {"passage_count": len(rows)},
        ),
        numeric_score(
            "annotate_speaker_coverage",
            len(known_speakers) / len(rows),
            "Speaker labels should avoid Unknown Speaker when the text gives enough evidence.",
            {"known_speaker_count": len(known_speakers), "passage_count": len(rows)},
        ),
        numeric_score(
            "annotate_emotion_variety",
            min(len(varied_emotions), 4) / 4,
            "A chapter should usually contain a useful range of emotion tags.",
            {"emotion_count": len(varied_emotions), "emotions": sorted(varied_emotions)},
        ),
        numeric_score(
            "annotate_direction_richness",
            (len(rationale_rows) + len(expressive_rows)) / (2 * len(rows)),
            "Annotations should include rationales and performance direction beyond neutral defaults.",
            {"rationale_count": len(rationale_rows), "expressive_count": len(expressive_rows)},
        ),
    ]


def evaluate_cast(cast: Cast, memory: StoryMemory) -> list[Score]:
    expected = {"Narrator", *memory.characters.keys()}
    assigned = set(cast.assignments.keys())
    covered = expected & assigned
    voice_ids = [assignment.voice_id for assignment in cast.assignments.values() if assignment.voice_id]
    reasons = [assignment for assignment in cast.assignments.values() if assignment.reason.strip()]
    return [
        numeric_score(
            "cast_assignment_coverage",
            len(covered) / len(expected) if expected else 0.0,
            "Every known character plus the narrator should have a voice assignment.",
            {"expected_count": len(expected), "covered_count": len(covered)},
        ),
        numeric_score(
            "cast_voice_diversity",
            min(len(set(voice_ids)), max(1, min(len(voice_ids), 4))) / max(1, min(len(voice_ids), 4)),
            "The cast should use a small but distinct palette rather than one voice for everyone.",
            {"voice_count": len(set(voice_ids)), "assignment_count": len(voice_ids)},
        ),
        numeric_score(
            "cast_reason_coverage",
            len(reasons) / len(cast.assignments) if cast.assignments else 0.0,
            "Voice assignments should include reasons tied to character or narration needs.",
            {"reason_count": len(reasons), "assignment_count": len(cast.assignments)},
        ),
    ]

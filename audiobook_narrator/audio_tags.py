from __future__ import annotations

import re

from audiobook_narrator.models import Delivery, Emotion, Passage


ELEVENLABS_AUDIO_TAGS = {
    "angry",
    "cheerfully",
    "clears throat",
    "crying",
    "curious",
    "dramatically",
    "excited",
    "fearful",
    "happily",
    "laughs",
    "mischievously",
    "long pause",
    "pause",
    "sad",
    "serious",
    "short pause",
    "sighs",
    "slowly",
    "softly",
    "stuttering",
    "tense",
    "whispers",
    "shouts",
}

TAG_ALIASES = {
    "anger": "angry",
    "anxious": "tense",
    "anxiously": "tense",
    "clipped": "serious",
    "comic": "mischievously",
    "dramatic": "dramatically",
    "fear": "fearful",
    "grief": "sad",
    "grieving": "sad",
    "intimate": "softly",
    "lyrical": "softly",
    "matter-of-fact": "serious",
    "matter of fact": "serious",
    "quick": "excited",
    "quickly": "excited",
    "reflective": "softly",
    "solemn": "serious",
    "suspense": "tense",
    "suspenseful": "tense",
    "tender": "softly",
    "urgent": "shouts",
    "whispering": "whispers",
    "shouting": "shouts",
    "wonder": "curious",
}

TAG_RE = re.compile(r"\[([^\[\]]{1,40})\]")


def allowed_audio_tags_prompt() -> str:
    return ", ".join(f"[{tag}]" for tag in sorted(ELEVENLABS_AUDIO_TAGS))


def normalize_audio_tags(value: object, *, max_tags: int = 3) -> list[str]:
    raw_tags: list[str] = []
    if isinstance(value, str):
        bracketed = TAG_RE.findall(value)
        raw_tags = bracketed if bracketed else re.split(r"[,;/|]", value)
    elif isinstance(value, list):
        raw_tags = [str(item) for item in value]
    else:
        return []

    normalized: list[str] = []
    seen = set()
    for raw_tag in raw_tags:
        tag = raw_tag.strip().lower().replace("_", " ").replace("-", " ")
        tag = re.sub(r"\s+", " ", tag.strip("[] "))
        tag = TAG_ALIASES.get(tag, tag)
        if tag in ELEVENLABS_AUDIO_TAGS and tag not in seen:
            normalized.append(f"[{tag}]")
            seen.add(tag)
        if len(normalized) >= max_tags:
            break
    return normalized


def extract_inline_tags(text: str) -> list[str]:
    """Extract and normalize ElevenLabs audio tags embedded inline in text."""
    return normalize_audio_tags(TAG_RE.findall(text))


def strip_inline_tags(text: str) -> str:
    """Remove recognized ElevenLabs audio tags from text, collapsing extra spaces."""
    def _replace(m: re.Match) -> str:
        tag = m.group(1).strip().lower()
        return "" if tag in ELEVENLABS_AUDIO_TAGS else m.group(0)
    return re.sub(r" {2,}", " ", TAG_RE.sub(_replace, text)).strip()


def audio_tags_for_passage(passage: Passage) -> list[str]:
    explicit_tags = normalize_audio_tags(passage.audio_tags)
    if explicit_tags:
        return explicit_tags
    candidates: list[str] = []
    candidates.extend(tags_for_emotion(passage.emotion))
    candidates.extend(tags_for_delivery(passage.delivery))
    if passage.pace == "slow":
        candidates.append("[slowly]")
    elif passage.pace == "quick":
        candidates.append("[excited]")
    if passage.intensity >= 5 and passage.emotion in {Emotion.angry, Emotion.urgent}:
        candidates.append("[shouts]")
    return normalize_audio_tags(candidates)


def tags_for_emotion(emotion: Emotion) -> list[str]:
    return {
        Emotion.angry: ["[angry]"],
        Emotion.fearful: ["[fearful]"],
        Emotion.grief: ["[sad]"],
        Emotion.tender: ["[softly]"],
        Emotion.urgent: ["[shouts]"],
        Emotion.wonder: ["[curious]"],
        Emotion.comic: ["[mischievously]"],
        Emotion.solemn: ["[serious]"],
        Emotion.tense: ["[tense]"],
        Emotion.neutral: [],
    }.get(emotion, [])


def tags_for_delivery(delivery: Delivery) -> list[str]:
    return {
        Delivery.dramatic: ["[dramatically]"],
        Delivery.intimate: ["[softly]"],
        Delivery.reflective: ["[softly]"],
        Delivery.clipped: ["[serious]"],
        Delivery.lyrical: ["[softly]"],
        Delivery.suspenseful: ["[tense]"],
        Delivery.conversational: [],
        Delivery.matter_of_fact: [],
    }.get(delivery, [])

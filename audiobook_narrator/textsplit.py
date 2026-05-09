from __future__ import annotations

import re


SENTENCE_END = "。！？!?；;"


def split_passages(text: str, max_chars: int = 260) -> list[str]:
    """Split Chinese prose into narratable chunks while preserving dialogue punctuation."""
    units = merge_dialogue_attribution(split_sentences(text))
    passages: list[str] = []
    current = ""
    for unit in units:
        if not current:
            current = unit
        elif is_dialogue(current) or is_dialogue(unit):
            passages.append(current)
            current = unit
        elif len(current) + len(unit) <= max_chars:
            current += unit
        else:
            passages.append(current)
            current = unit
    if current:
        passages.append(current)
    return passages


def split_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    start = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char in SENTENCE_END:
            end = index + 1
            if end < len(text) and text[end] in "”」』":
                end += 1
            sentence = text[start:end].strip()
            if sentence:
                sentences.append(sentence)
            start = end
        index += 1
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def merge_dialogue_attribution(sentences: list[str]) -> list[str]:
    merged: list[str] = []
    for sentence in sentences:
        if merged and is_speech_attribution(sentence):
            merged[-1] = f"{merged[-1]}{sentence}"
        else:
            merged.append(sentence)
    return merged


def is_speech_attribution(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return bool(re.fullmatch(r"[\u4e00-\u9fff]{2,6}(轻声|低声|大声|忽然|冷冷)?(说|问|道|喊|叫|回答)[。！？]?", compact))


def is_dialogue(text: str) -> bool:
    return bool(re.search(r"[“「『].+?[”」』]", text) or re.search(r"^\".+\"$", text.strip()))


def extract_dialogue_text(text: str) -> str | None:
    match = re.search(r"[“「『](.+?)[”」』]", text)
    if match:
        return match.group(1)
    match = re.search(r'^"(.+)"$', text.strip())
    return match.group(1) if match else None

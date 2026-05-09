from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Protocol

from dotenv import load_dotenv

from audiobook_narrator.models import JsonDict, Passage

load_dotenv()


class LLMProvider(Protocol):
    def complete_json(self, system: str, user: str) -> JsonDict:
        ...


class HeuristicLLMProvider:
    def complete_json(self, system: str, user: str) -> JsonDict:
        return {"provider": "heuristic", "system": system[:120], "notes": user[:400]}


class OpenAILLMProvider:
    def __init__(self, model: str | None = None) -> None:
        from openai import OpenAI

        self.client = OpenAI()
        self.model = model or os.getenv("NARRATION_LLM_MODEL", "gpt-4.1")

    def complete_json(self, system: str, user: str) -> JsonDict:
        response = self.client.responses.create(
            model=self.model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            text={"format": {"type": "json_object"}},
        )
        return json.loads(response.output_text)


def get_llm_provider(prefer_openai: bool = True) -> LLMProvider:
    if prefer_openai and os.getenv("OPENAI_API_KEY"):
        try:
            return OpenAILLMProvider()
        except Exception:
            return HeuristicLLMProvider()
    return HeuristicLLMProvider()


class TTSProvider(Protocol):
    def synthesize(self, passages: list[Passage], output_path: Path, voice_by_speaker: dict[str, str]) -> Path:
        ...


class ScriptOnlyTTSProvider:
    def synthesize(self, passages: list[Passage], output_path: Path, voice_by_speaker: dict[str, str]) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            f"[{p.speaker} | {voice_by_speaker.get(p.speaker, 'default')} | "
            f"{p.emotion} | {p.delivery} | {p.pace}]\n{p.text}"
            for p in passages
        ]
        output_path.write_text("\n\n".join(rows) + "\n", encoding="utf-8")
        return output_path


class MacOSSayTTSProvider:
    def synthesize(self, passages: list[Passage], output_path: Path, voice_by_speaker: dict[str, str]) -> Path:
        parts_dir = output_path.parent / f"{output_path.stem}_parts"
        parts_dir.mkdir(parents=True, exist_ok=True)
        manifest = []
        for passage in passages:
            part_path = parts_dir / f"{passage.index:04d}_{safe_filename(passage.speaker)}.aiff"
            voice = voice_by_speaker.get(passage.speaker, voice_by_speaker.get("Narrator", "Ting-Ting"))
            subprocess.run(["say", "-v", voice, passage.text, "-o", str(part_path)], check=True)
            manifest.append(part_manifest_row(passage, voice, part_path))
        manifest_path = output_path.with_suffix(".parts.json")
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return manifest_path


class OpenAITTSProvider:
    OPENAI_VOICES = {"alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer"}

    def __init__(self, model: str | None = None) -> None:
        from openai import OpenAI

        self.client = OpenAI()
        self.model = model or os.getenv("NARRATION_TTS_MODEL", "gpt-4o-mini-tts")

    def synthesize(self, passages: list[Passage], output_path: Path, voice_by_speaker: dict[str, str]) -> Path:
        parts_dir = output_path.parent / f"{output_path.stem}_parts"
        parts_dir.mkdir(parents=True, exist_ok=True)
        manifest = []
        for passage in passages:
            voice = self.openai_voice(voice_by_speaker.get(passage.speaker, "alloy"))
            part_path = parts_dir / f"{passage.index:04d}_{safe_filename(passage.speaker)}.mp3"
            prompt = (
                f"Read in Mandarin Chinese. Speaker: {passage.speaker}. "
                f"Emotion: {passage.emotion.value}. Delivery: {passage.delivery.value}. "
                f"Pace: {passage.pace}. Intensity: {passage.intensity}/5.\n\n{passage.text}"
            )
            with self.client.audio.speech.with_streaming_response.create(
                model=self.model,
                voice=voice,
                input=prompt,
            ) as response:
                response.stream_to_file(part_path)
            manifest.append(part_manifest_row(passage, voice, part_path))
        manifest_path = output_path.with_suffix(".parts.json")
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return manifest_path

    def openai_voice(self, requested: str) -> str:
        return requested if requested in self.OPENAI_VOICES else "alloy"


class ElevenLabsTTSProvider:
    """ElevenLabs dialogue streaming backend for multi-speaker audiobook chunks."""

    API_BASE = "https://api.elevenlabs.io/v1"
    MAX_DIALOGUE_CHARS = 1900
    MAX_UNIQUE_VOICES = 10

    def __init__(
        self,
        api_key: str | None = None,
        model_id: str | None = None,
        output_format: str | None = None,
        language_code: str | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("ELEVENLABS_API_KEY")
        if not self.api_key:
            raise RuntimeError("ELEVENLABS_API_KEY is required for the elevenlabs backend.")
        self.model_id = model_id or os.getenv("ELEVENLABS_MODEL_ID", "eleven_v3")
        self.output_format = output_format or os.getenv("ELEVENLABS_OUTPUT_FORMAT", "mp3_44100_128")
        self.language_code = language_code or os.getenv("ELEVENLABS_LANGUAGE_CODE", "zh")
        self.default_voice_id = os.getenv("ELEVENLABS_DEFAULT_VOICE_ID", "")
        self.env_voice_map = self._load_env_voice_map()

    def synthesize(self, passages: list[Passage], output_path: Path, voice_by_speaker: dict[str, str]) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        chunks = self._dialogue_chunks(passages, voice_by_speaker)
        manifest = []
        for chunk_index, chunk in enumerate(chunks):
            part_path = output_path.parent / f"{output_path.stem}_{chunk_index:03d}.mp3"
            self._stream_dialogue(chunk["inputs"], part_path)
            manifest.append(
                {
                    "chunk_index": chunk_index,
                    "path": str(part_path),
                    "model_id": self.model_id,
                    "output_format": self.output_format,
                    "language_code": self.language_code,
                    "passages": chunk["manifest"],
                }
            )
        manifest_path = output_path.with_suffix(".parts.json")
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return manifest_path

    def _dialogue_chunks(
        self, passages: list[Passage], voice_by_speaker: dict[str, str]
    ) -> list[dict[str, list[dict]]]:
        chunks: list[dict[str, list[dict]]] = []
        inputs: list[dict] = []
        manifest: list[dict] = []
        char_count = 0
        voices: set[str] = set()
        for passage in passages:
            voice_id = self.voice_id_for(passage.speaker, voice_by_speaker)
            text = passage.text.strip()
            would_exceed_chars = char_count + len(text) > self.MAX_DIALOGUE_CHARS
            would_exceed_voices = voice_id not in voices and len(voices) >= self.MAX_UNIQUE_VOICES
            if inputs and (would_exceed_chars or would_exceed_voices):
                chunks.append({"inputs": inputs, "manifest": manifest})
                inputs = []
                manifest = []
                char_count = 0
                voices = set()
            inputs.append({"text": text, "voice_id": voice_id})
            manifest.append(part_manifest_row(passage, voice_id, Path("")) | {"text_chars": len(text)})
            char_count += len(text)
            voices.add(voice_id)
        if inputs:
            chunks.append({"inputs": inputs, "manifest": manifest})
        return chunks

    def voice_id_for(self, speaker: str, voice_by_speaker: dict[str, str]) -> str:
        voice_id = self.env_voice_map.get(speaker) or voice_by_speaker.get(speaker)
        if voice_id and not self._looks_like_local_voice_name(voice_id):
            return voice_id
        if self.default_voice_id:
            return self.default_voice_id
        raise RuntimeError(
            f"No ElevenLabs voice id configured for speaker '{speaker}'. Set ELEVENLABS_VOICE_MAP_JSON "
            "or ELEVENLABS_DEFAULT_VOICE_ID."
        )

    def _stream_dialogue(self, inputs: list[dict], output_path: Path) -> None:
        query = urllib.parse.urlencode({"output_format": self.output_format})
        url = f"{self.API_BASE}/text-to-dialogue/stream?{query}"
        payload = {"inputs": inputs, "model_id": self.model_id}
        if self.language_code:
            payload["language_code"] = self.language_code
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "xi-api-key": self.api_key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                output_path.write_bytes(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"ElevenLabs request failed ({exc.code}): {detail}") from exc

    def list_voices(self, page_size: int = 100) -> list[dict]:
        query = urllib.parse.urlencode({"page_size": page_size})
        url = f"{self.API_BASE.replace('/v1', '/v2')}/voices?{query}"
        request = urllib.request.Request(
            url,
            method="GET",
            headers={"xi-api-key": self.api_key, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"ElevenLabs voice list failed ({exc.code}): {detail}") from exc
        return payload.get("voices", [])

    def _load_env_voice_map(self) -> dict[str, str]:
        raw = os.getenv("ELEVENLABS_VOICE_MAP_JSON", "").strip()
        if not raw:
            return {}
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("ELEVENLABS_VOICE_MAP_JSON must be a JSON object.")
        return {str(key): str(value) for key, value in parsed.items()}

    @staticmethod
    def _looks_like_local_voice_name(value: str) -> bool:
        return value in {"Ting-Ting", "Sin-ji", "Mei-Jia"} or " " in value


def get_tts_provider(name: str | None = None) -> TTSProvider:
    backend = name or os.getenv("NARRATION_TTS_BACKEND", "script")
    if backend == "elevenlabs":
        return ElevenLabsTTSProvider()
    if backend == "openai" and os.getenv("OPENAI_API_KEY"):
        return OpenAITTSProvider()
    if backend == "macos_say":
        return MacOSSayTTSProvider()
    return ScriptOnlyTTSProvider()


def safe_filename(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value)[:40] or "speaker"


def part_manifest_row(passage: Passage, voice: str, part_path: Path) -> dict:
    return {
        "index": passage.index,
        "passage_id": passage.passage_id,
        "speaker": passage.speaker,
        "voice": voice,
        "emotion": passage.emotion.value,
        "delivery": passage.delivery.value,
        "pace": passage.pace,
        "path": str(part_path),
    }

"""
wyoming_client.py

Async Wyoming protocol client for:
  - Piper TTS  (wyoming-piper,  default port 10200)
  - faster-whisper STT (linuxserver/faster-whisper, default port 10300)

Wyoming wire format (per message):
  1. One UTF-8 JSON line ending with \n:
       {"type": "<event>", "data": {...}, "data_length": <N>, "payload_length": <P>}
  2. data_length bytes of additional JSON (rare, usually 0)
  3. payload_length bytes of raw binary payload

Key details:
  - Wyoming-Piper sends audio format in the "audio-start" event data field.
  - Wyoming-Piper "audio-chunk" payloads are raw PCM, not WAV.
  - wyoming_stt() accepts raw PCM directly — no WAV parsing needed.

Reference: https://github.com/rhasspy/wyoming
"""

import asyncio
import io
import json
import logging
import re
import wave
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hallucination filter
# ---------------------------------------------------------------------------
# faster-whisper (especially tiny/small models) commonly hallucinates these
# phrases on silence, background noise, or very short clips.  Any transcript
# that matches one of these patterns (case-insensitive, stripped) is treated
# as if nothing was said.
_HALLUCINATION_PATTERNS: list[re.Pattern] = [
    re.compile(r, re.IGNORECASE) for r in [
        r"^thanks? for watching[.!]*$",
        r"^please (like|subscribe|share)[.!]*$",
        r"^(like and )?subscribe[.!]*$",
        r"^\.{2,}$",                        # only dots/ellipsis
        r"^\s*$",                           # blank
        r"^\[[\w\s]+\]$",                  # [BLANK_AUDIO], [Music], etc.
        r"^(uh+|um+|ah+|hmm+)[.!,]*$",    # single filler words
        r"^you$",                           # lone "you" — common hallucination
        r"^www\.",                          # URL fragments
        r"^subtitles? by",
        r"^transcribed by",
        r"^captioned by",
        r"^the end\.?$",
    ]
]

# If faster-whisper reports no_speech_prob above this, discard the transcript.
# The server embeds this in the transcript event's data dict.
_NO_SPEECH_PROB_THRESHOLD = 0.60


def _is_hallucination(text: str, no_speech_prob: float = 0.0) -> bool:
    """Return True if the transcript should be discarded as a hallucination."""
    if no_speech_prob >= _NO_SPEECH_PROB_THRESHOLD:
        log.debug(
            f"[wyoming/stt] Discarding transcript — no_speech_prob={no_speech_prob:.3f} "
            f">= threshold {_NO_SPEECH_PROB_THRESHOLD}"
        )
        return True
    stripped = text.strip()
    for pattern in _HALLUCINATION_PATTERNS:
        if pattern.search(stripped):
            log.debug(f"[wyoming/stt] Discarding hallucination: {text!r}")
            return True
    return False


# ---------------------------------------------------------------------------
# Low-level framing helpers
# ---------------------------------------------------------------------------

async def _read_event(reader: asyncio.StreamReader) -> Optional[dict]:
    """Read one Wyoming event from the stream. Returns None on EOF."""
    try:
        line = await reader.readline()
    except asyncio.IncompleteReadError:
        return None
    if not line:
        return None

    try:
        header = json.loads(line.decode())
    except json.JSONDecodeError as e:
        log.error(f"[wyoming] Bad header JSON: {e} — raw: {line!r}")
        return None

    data_length    = header.get("data_length",    0)
    payload_length = header.get("payload_length", 0)

    if data_length:
        extra = await reader.readexactly(data_length)
        try:
            header["data"] = {**header.get("data", {}), **json.loads(extra)}
        except json.JSONDecodeError:
            pass

    payload = b""
    if payload_length:
        payload = await reader.readexactly(payload_length)

    header["_payload"] = payload
    return header


async def _write_event(writer: asyncio.StreamWriter, event_type: str,
                       data: dict = None, payload: bytes = b"") -> None:
    """Write one Wyoming event to the stream."""
    header = {
        "type":           event_type,
        "data":           data or {},
        "data_length":    0,
        "payload_length": len(payload),
    }
    line = (json.dumps(header) + "\n").encode()
    writer.write(line)
    if payload:
        writer.write(payload)
    await writer.drain()


def _pcm_to_wav(pcm: bytes, rate: int, width: int, channels: int) -> bytes:
    """Wrap raw PCM bytes in a WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        wf.writeframes(pcm)
    buf.seek(0)
    return buf.read()


# ---------------------------------------------------------------------------
# Piper TTS -> returns WAV bytes
# ---------------------------------------------------------------------------

async def wyoming_tts(
    text: str,
    host: str = "127.0.0.1",
    port: int = 10200,
    voice: Optional[str] = None,
    speaker: Optional[int] = None,
) -> bytes:
    """
    Send a synthesis request to a Wyoming-Piper server and return
    complete WAV audio as bytes, suitable for piping to FFmpeg.

    Piper sends the audio format (rate/width/channels) in the "audio-start"
    event, then sends raw PCM in "audio-chunk" payloads.
    This function collects all PCM chunks and wraps them in a WAV container.

    Returns:
        WAV bytes.

    Raises:
        RuntimeError on server error or no audio returned.
    """
    reader, writer = await asyncio.open_connection(host, port)
    log.debug(f"[wyoming/tts] Connected to {host}:{port}")

    try:
        synth_data: dict = {"text": text}
        if voice:
            synth_data["voice"] = {"name": voice}
        if speaker is not None:
            synth_data["voice"] = {**synth_data.get("voice", {}), "speaker": speaker}

        await _write_event(writer, "synthesize", data=synth_data)
        log.debug(f"[wyoming/tts] Sent synthesize: {text!r}")

        pcm_chunks: list[bytes] = []

        # Audio format — populated from audio-start, with safe defaults
        audio_rate:     int = 22050
        audio_width:    int = 2
        audio_channels: int = 1

        while True:
            event = await _read_event(reader)
            if event is None:
                log.warning("[wyoming/tts] Connection closed before audio-stop")
                break

            etype = event.get("type")
            edata = event.get("data", {})
            payload = event.get("_payload", b"")

            log.debug(f"[wyoming/tts] Event: {etype!r}  data={edata}  payload_len={len(payload)}")

            if etype == "audio-start":
                audio_rate     = edata.get("rate",     audio_rate)
                audio_width    = edata.get("width",    audio_width)
                audio_channels = edata.get("channels", audio_channels)
                log.debug(
                    f"[wyoming/tts] Audio format from audio-start: "
                    f"{audio_rate}Hz {audio_width*8}bit {audio_channels}ch"
                )

            elif etype == "audio-chunk":
                if payload:
                    pcm_chunks.append(payload)

            elif etype == "audio-stop":
                log.debug("[wyoming/tts] Received audio-stop, done.")
                break

            elif etype == "error":
                msg = edata.get("text", "unknown error")
                raise RuntimeError(f"Wyoming Piper error: {msg}")

        if not pcm_chunks:
            raise RuntimeError("Wyoming Piper returned no audio chunks")

        raw_pcm = b"".join(pcm_chunks)
        log.debug(
            f"[wyoming/tts] Collected {len(pcm_chunks)} chunks, "
            f"{len(raw_pcm)} PCM bytes. "
            f"Wrapping as WAV: {audio_rate}Hz {audio_width*8}bit {audio_channels}ch"
        )

        return _pcm_to_wav(raw_pcm, audio_rate, audio_width, audio_channels)

    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# faster-whisper STT -> returns transcript string
# ---------------------------------------------------------------------------

async def wyoming_stt(
    pcm: bytes,
    host: str = "127.0.0.1",
    port: int = 10300,
    rate: int = 48000,
    width: int = 2,
    channels: int = 2,
    language: Optional[str] = None,
) -> Optional[str]:
    """
    Send raw PCM audio to a Wyoming faster-whisper server and return
    the transcript string, or None if nothing was recognised.

    Takes raw PCM directly — no WAV container needed.
    Discord audio is 48000 Hz, stereo, 16-bit signed PCM.

    Hallucination filtering is applied: transcripts that match known
    false-positive patterns or have a high no_speech_prob are discarded.

    Args:
        pcm:      Raw PCM bytes.
        host:     Wyoming STT host.
        port:     Wyoming STT port.
        rate:     Sample rate in Hz (default 48000).
        width:    Sample width in bytes (default 2 = 16-bit).
        channels: Number of channels (default 2 = stereo).
        language: Optional BCP-47 language code e.g. "en". None = auto-detect.

    Returns:
        Transcript string, or None.
    """
    if not pcm:
        log.warning("[wyoming/stt] Called with empty PCM, skipping.")
        return None

    reader, writer = await asyncio.open_connection(host, port)
    log.debug(f"[wyoming/stt] Connected to {host}:{port}, sending {len(pcm)} PCM bytes")

    try:
        # 1. Start a recognition pipeline
        pipeline_data: dict = {"start_stage": "asr", "end_stage": "asr"}
        if language:
            pipeline_data["language"] = language
        await _write_event(writer, "run-pipeline", data=pipeline_data)

        # 2. Declare audio format
        audio_meta = {"rate": rate, "width": width, "channels": channels}
        await _write_event(writer, "audio-start", data=audio_meta)

        # 3. Stream PCM in chunks
        chunk_size = 4096
        for i in range(0, len(pcm), chunk_size):
            chunk = pcm[i:i + chunk_size]
            await _write_event(
                writer, "audio-chunk",
                data={**audio_meta, "timestamp": 0},
                payload=chunk,
            )

        # 4. Signal end of audio
        await _write_event(writer, "audio-stop", data=audio_meta)
        log.debug("[wyoming/stt] Sent audio-stop, waiting for transcript...")

        # 5. Wait for transcript or error
        transcript: Optional[str] = None
        while True:
            event = await _read_event(reader)
            if event is None:
                log.warning("[wyoming/stt] Connection closed before transcript received")
                break

            etype = event.get("type")
            edata = event.get("data", {})
            log.debug(f"[wyoming/stt] Event: {etype!r}  data={edata}")

            if etype in ("transcript", "stt-result"):
                raw_text = edata.get("text", "").strip()
                # Pull no_speech_prob if the server includes it (some builds do)
                no_speech_prob = float(edata.get("no_speech_prob", 0.0))

                log.debug(
                    f"[wyoming/stt] Raw transcript: {raw_text!r}  "
                    f"no_speech_prob={no_speech_prob:.3f}"
                )

                if raw_text and not _is_hallucination(raw_text, no_speech_prob):
                    transcript = raw_text
                else:
                    transcript = None
                break

            elif etype == "error":
                msg = edata.get("text", "unknown error")
                raise RuntimeError(f"Wyoming faster-whisper error: {msg}")

            # Ignore: "asr-audio-start", "info", "run-pipeline", etc.

        return transcript

    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

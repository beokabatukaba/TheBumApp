import array
import asyncio
import io
import logging
import os
import re
import wave
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext import voice_recv
from discord.opus import Decoder as OpusDecoder

from wyoming_client import wyoming_tts, wyoming_stt

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PIPER_HOST    = os.getenv("PIPER_HOST",   "127.0.0.1")
PIPER_PORT    = int(os.getenv("PIPER_PORT",  "10200"))
PIPER_VOICE   = os.getenv("PIPER_VOICE",  "en_US-PordanJetersonMoM2017-ep595-medium")

WHISPER_HOST  = os.getenv("WHISPER_HOST", "127.0.0.1")
WHISPER_PORT  = int(os.getenv("WHISPER_PORT", "10300"))
WHISPER_LANG  = os.getenv("WHISPER_LANG", "en")  # set to "" to auto-detect

OLLAMA_HOST   = os.getenv("OLLAMA_HOST",  "127.0.0.1")
OLLAMA_PORT   = int(os.getenv("OLLAMA_PORT", "11434"))
OLLAMA_MODEL  = os.getenv("OLLAMA_MODEL", "gemma3:latest")

# Seconds of silence that ends a speaking turn and triggers transcription
SILENCE_THRESHOLD_S = float(os.getenv("SILENCE_THRESHOLD_S", "1.5"))

# Minimum duration of audio (in seconds) before we even attempt STT.
# At 48000 Hz / stereo / 16-bit, 1 second = 192000 bytes.
# Clips shorter than this are almost always noise and cause hallucinations.
MIN_AUDIO_SECONDS = float(os.getenv("MIN_AUDIO_SECONDS", "0.75"))
_BYTES_PER_SECOND = 48000 * 2 * 2  # rate * channels * width
MIN_PCM_BYTES = int(MIN_AUDIO_SECONDS * _BYTES_PER_SECOND)

# Discord voice PCM format constants
DISCORD_SAMPLE_RATE  = 48000
DISCORD_CHANNELS     = 2
DISCORD_SAMPLE_WIDTH = 2  # bytes (16-bit)

SYSTEM_PROMPT = os.getenv(
    "BOT_SYSTEM_PROMPT",
    (
        "You are Christotron, a sardonic yet learned theologian who speaks in a mixture "
        "of archaic King James English and modern internet slang. You give short, punchy "
        "responses (2-4 sentences max) unless the user explicitly asks for more. "
        "You occasionally drop unsolicited biblical references. Stay in character always."
    ),
)

# ---------------------------------------------------------------------------
# PCM endianness helper
# ---------------------------------------------------------------------------
# discord.opus.Decoder is a C extension wrapping libopus.  When you call
# .decode() yourself (i.e. wants_opus=True), libopus returns 16-bit signed
# PCM in **big-endian** (network) byte order.  WAV files and Whisper both
# require **little-endian**.  The garbled/digital sound in the debug WAVs —
# and the poor transcription quality — are both caused by this byte swap.
#
# We fix it once here, immediately after decode, so every downstream
# consumer (buffer accumulation, WAV dump, Wyoming STT) gets correct LE PCM.

def _be_to_le_pcm(pcm: bytes) -> bytes:
    """Swap bytes of a big-endian 16-bit PCM buffer to little-endian."""
    a = array.array("h", pcm)   # interpret as signed 16-bit (native)
    a.byteswap()                # flip each sample's bytes
    return bytes(a)


# ---------------------------------------------------------------------------
# Debug audio dumping
# ---------------------------------------------------------------------------
# Set AUDIO_DEBUG_DIR to a directory path to enable WAV dumping, e.g.:
#   AUDIO_DEBUG_DIR=./debug_audio
#
# Each flushed utterance produces up to two files:
#   <username>_<YYYYMMDD_HHMMSS_mmm>_prefilter.wav   — every clip, raw as received
#   <username>_<YYYYMMDD_HHMMSS_mmm>_postfilter.wav  — only clips that pass the
#                                                        MIN_PCM_BYTES length gate
#                                                        (i.e. what Whisper actually sees)
#
# Both files are standard 48kHz / stereo / 16-bit little-endian WAV, playable
# directly in VS Code or any media player.
#
# Leave AUDIO_DEBUG_DIR unset or empty to disable (default).

_DEBUG_DIR_RAW = os.getenv("AUDIO_DEBUG_DIR", "")
AUDIO_DEBUG_DIR: Path | None = Path(_DEBUG_DIR_RAW).resolve() if _DEBUG_DIR_RAW else None

if AUDIO_DEBUG_DIR:
    AUDIO_DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    logging.info(f"[listen_cmd] Audio debug dump ENABLED → {AUDIO_DEBUG_DIR}")


def _safe_username(name: str) -> str:
    """Strip characters that are invalid in filenames."""
    return re.sub(r"[^\w\-]", "_", name)


def _dump_wav(pcm: bytes, username: str, label: str) -> None:
    """
    Write little-endian PCM to a WAV file in AUDIO_DEBUG_DIR.
    No-ops silently if AUDIO_DEBUG_DIR is not set.

    Args:
        pcm:      Raw PCM bytes (48kHz, stereo, 16-bit signed little-endian).
        username: Discord username — used in the filename.
        label:    Short tag appended to the filename, e.g. "prefilter" or "postfilter".
    """
    if not AUDIO_DEBUG_DIR:
        return
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_") + f"{datetime.now().microsecond // 1000:03d}"
        filename = AUDIO_DEBUG_DIR / f"{_safe_username(username)}_{ts}_{label}.wav"
        with wave.open(str(filename), "wb") as wf:
            wf.setnchannels(DISCORD_CHANNELS)
            wf.setsampwidth(DISCORD_SAMPLE_WIDTH)
            wf.setframerate(DISCORD_SAMPLE_RATE)
            wf.writeframes(pcm)
        duration_s = len(pcm) / _BYTES_PER_SECOND
        logging.debug(
            f"[listen_cmd/debug] Wrote {label} WAV: {filename.name} "
            f"({len(pcm):,} bytes / {duration_s:.2f}s)"
        )
    except Exception as e:
        logging.warning(f"[listen_cmd/debug] Failed to dump WAV: {e}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def wav_to_discord_audio_source(wav_bytes: bytes) -> discord.FFmpegPCMAudio:
    """Pipe WAV bytes (from wyoming_tts) into FFmpegPCMAudio for Discord playback."""
    buf = io.BytesIO(wav_bytes)
    buf.seek(0)
    return discord.FFmpegPCMAudio(buf, pipe=True)


async def query_llm(conversation_history: list) -> str | None:
    """Send conversation history to Ollama /api/chat. Blocking I/O run in a thread."""
    import requests

    def _call():
        url = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/chat"
        payload = {
            "model": OLLAMA_MODEL,
            "messages": conversation_history,
            "stream": False,
        }
        try:
            resp = requests.post(url, json=payload, timeout=60)
            if resp.status_code == 404:
                logging.error(
                    f"[listen_cmd] Ollama returned 404 for model '{OLLAMA_MODEL}'. "
                    f"Run `ollama list` to check available models, or set the "
                    f"OLLAMA_MODEL env var to the correct name (e.g. 'gemma3:latest')."
                )
                return None
            resp.raise_for_status()
            return resp.json()["message"]["content"].strip()
        except requests.exceptions.ConnectionError:
            logging.error(
                f"[listen_cmd] Cannot reach Ollama at {OLLAMA_HOST}:{OLLAMA_PORT}. "
                f"Is Ollama running?"
            )
            return None
        except Exception as e:
            logging.error(f"[listen_cmd] Ollama request failed: {e}")
            return None

    return await asyncio.to_thread(_call)


# ---------------------------------------------------------------------------
# AudioSink
# ---------------------------------------------------------------------------

class ConversationSink(voice_recv.AudioSink):
    """
    Receives raw Opus payloads (wants_opus=True bypasses voice_recv's built-in
    decoder, which crashes on comfort-noise packets). Decodes per-user with
    our own OpusDecoder instances, byte-swaps to little-endian, accumulates
    PCM, and fires an async callback after SILENCE_THRESHOLD_S seconds of
    quiet from a given user.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, callback):
        super().__init__()
        self._loop = loop
        self._callback = callback  # async def callback(user, pcm: bytes)
        self._decoders:      dict[int, OpusDecoder]         = {}
        self._buffers:       dict[int, bytearray]           = defaultdict(bytearray)
        self._flush_handles: dict[int, asyncio.TimerHandle] = {}

    def wants_opus(self) -> bool:
        return True

    def _get_decoder(self, uid: int) -> OpusDecoder:
        if uid not in self._decoders:
            self._decoders[uid] = OpusDecoder()
        return self._decoders[uid]

    def write(self, user, data: voice_recv.VoiceData) -> None:
        if user is None:
            return
        if data.packet.is_silence():
            return

        opus_bytes = data.opus
        if not opus_bytes:
            return

        uid = user.id
        try:
            # libopus decode() returns big-endian 16-bit PCM when called
            # directly (wants_opus=True path).  Swap to little-endian so
            # WAV files and Whisper both receive correctly ordered samples.
            pcm_be = self._get_decoder(uid).decode(opus_bytes, fec=False)
            pcm = _be_to_le_pcm(pcm_be)
        except Exception as e:
            logging.debug(f"[listen_cmd] Opus decode error from {getattr(user, 'name', uid)}: {e}")
            return

        self._buffers[uid].extend(pcm)

        handle = self._flush_handles.get(uid)
        if handle is not None:
            handle.cancel()
        self._flush_handles[uid] = self._loop.call_later(
            SILENCE_THRESHOLD_S, self._do_flush, uid, user
        )

    def _do_flush(self, uid: int, user) -> None:
        pcm = bytes(self._buffers.pop(uid, b""))
        self._flush_handles.pop(uid, None)
        if pcm:
            asyncio.ensure_future(self._callback(user, pcm), loop=self._loop)

    @voice_recv.AudioSink.listener()
    def on_voice_member_disconnect(self, member: discord.Member, ssrc) -> None:
        uid = member.id
        handle = self._flush_handles.pop(uid, None)
        if handle is not None:
            handle.cancel()
        pcm = bytes(self._buffers.pop(uid, b""))
        self._decoders.pop(uid, None)
        if pcm:
            asyncio.ensure_future(self._callback(member, pcm), loop=self._loop)

    def cleanup(self) -> None:
        for handle in self._flush_handles.values():
            handle.cancel()
        self._flush_handles.clear()
        self._buffers.clear()
        self._decoders.clear()


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Listen_Commands(bot))


class Listen_Commands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.voice_client: voice_recv.VoiceRecvClient | None = None
        self.sink: ConversationSink | None = None
        self._text_channel: discord.TextChannel | None = None
        self._histories: dict[int, list] = defaultdict(
            lambda: [{"role": "system", "content": SYSTEM_PROMPT}]
        )

    # ------------------------------------------------------------------
    # Pipeline: Opus -> PCM -> Wyoming STT -> Ollama -> Wyoming TTS -> Discord
    # ------------------------------------------------------------------

    async def _handle_audio(self, user, pcm: bytes) -> None:
        username = getattr(user, "name", str(user))
        logging.info(f"[listen_cmd] Processing {len(pcm)} PCM bytes from {username}.")

        # Debug dump — always write the raw clip first so you can hear exactly
        # what arrived from Discord, before any filtering is applied.
        _dump_wav(pcm, username, "prefilter")

        # Gate: drop clips that are too short to contain real speech.
        if len(pcm) < MIN_PCM_BYTES:
            logging.info(
                f"[listen_cmd] Clip from {username} is too short "
                f"({len(pcm):,} < {MIN_PCM_BYTES:,} bytes / {MIN_AUDIO_SECONDS}s), skipping."
            )
            return

        # Debug dump — write the clip that actually reaches Whisper.
        _dump_wav(pcm, username, "postfilter")

        # 1. Transcribe
        try:
            transcript = await wyoming_stt(
                pcm,
                host=WHISPER_HOST,
                port=WHISPER_PORT,
                rate=DISCORD_SAMPLE_RATE,
                width=DISCORD_SAMPLE_WIDTH,
                channels=DISCORD_CHANNELS,
                language=WHISPER_LANG or None,
            )
        except Exception as e:
            logging.error(f"[listen_cmd] STT failed for {username}: {e}")
            return

        if not transcript:
            logging.info(f"[listen_cmd] Empty transcript from {username}, ignoring.")
            return

        logging.info(f"[listen_cmd] {username}: {transcript!r}")

        if self._text_channel:
            await self._text_channel.send(f"🎙️ **{username}**: {transcript}")

        # 2. Query Ollama
        uid = user.id
        history = self._histories[uid]
        history.append({"role": "user", "content": transcript})

        reply = await query_llm(history)
        if not reply:
            logging.warning(f"[listen_cmd] LLM returned nothing for {username}.")
            return
        history.append({"role": "assistant", "content": reply})
        logging.info(f"[listen_cmd] Reply to {username}: {reply!r}")

        if self._text_channel:
            await self._text_channel.send(f"🤖 **Christotron**: {reply}")

        # 3. Synthesise via Wyoming Piper
        try:
            wav_out = await wyoming_tts(
                reply,
                host=PIPER_HOST,
                port=PIPER_PORT,
                voice=PIPER_VOICE,
            )
        except Exception as e:
            logging.error(f"[listen_cmd] TTS failed: {e}")
            return

        source = wav_to_discord_audio_source(wav_out)

        # 4. Play back — wait if already playing so responses don't overlap
        if self.voice_client and self.voice_client.is_connected():
            while self.voice_client.is_playing():
                await asyncio.sleep(0.2)
            self.voice_client.play(
                source,
                after=lambda e: logging.error(f"[listen_cmd] Playback error: {e}") if e else None,
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _join_channel(self, interaction: discord.Interaction) -> voice_recv.VoiceRecvClient | None:
        channel = interaction.user.voice.channel if interaction.user.voice else None
        if not channel:
            await interaction.channel.send("Thou art not in a voice channel, foolish mortal.")
            return None

        existing = interaction.client.voice_clients
        if existing:
            vc = existing[0]
            if isinstance(vc, voice_recv.VoiceRecvClient) and vc.channel == channel:
                return vc
            await vc.disconnect()

        return await channel.connect(cls=voice_recv.VoiceRecvClient, timeout=60.0, reconnect=True)

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------

    @app_commands.command(name="listen", description="Start listening and responding in your voice channel")
    async def listen(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Lend me thine ears, for I am LISTENING.", ephemeral=True)

        vc = await self._join_channel(interaction)
        if vc is None:
            return

        if vc.is_listening():
            vc.stop_listening()
        if self.sink:
            self.sink.cleanup()

        self.voice_client = vc
        self._text_channel = interaction.channel

        self.sink = ConversationSink(self.bot.loop, self._handle_audio)
        vc.listen(self.sink)

        debug_note = f" | 🎙️ Dumping audio → `{AUDIO_DEBUG_DIR}`" if AUDIO_DEBUG_DIR else ""
        await interaction.channel.send(
            f"👂 Christotron is now listening in **{vc.channel.name}**. "
            f"Speak thy piece — model: `{OLLAMA_MODEL}`.{debug_note}"
        )

    @app_commands.command(name="unlisten", description="Stop listening and leave the voice channel")
    async def unlisten(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Silence! The Lord commands it.", ephemeral=True)

        if self.voice_client:
            if self.voice_client.is_listening():
                self.voice_client.stop_listening()
            if self.sink:
                self.sink.cleanup()
                self.sink = None
            await self.voice_client.disconnect()
            self.voice_client = None

        await interaction.channel.send("🔇 Christotron hath ceased his vigil. Go in peace.")

    @app_commands.command(name="forget", description="Clear Christotron's memory of your conversation")
    async def forget(self, interaction: discord.Interaction) -> None:
        uid = interaction.user.id
        self._histories[uid] = [{"role": "system", "content": SYSTEM_PROMPT}]
        await interaction.response.send_message(
            "Thy conversational sins are absolved. We begin anew.", ephemeral=False
        )

    @app_commands.command(name="setprompt", description="Override Christotron's personality system prompt")
    async def setprompt(self, interaction: discord.Interaction, prompt: str) -> None:
        global SYSTEM_PROMPT
        SYSTEM_PROMPT = prompt
        await interaction.response.send_message(
            f"✍️ System prompt updated. New personality:\n> {prompt}", ephemeral=False
        )
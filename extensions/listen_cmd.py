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
OLLAMA_MODEL  = os.getenv("OLLAMA_MODEL", "gemma3:12b")

# Seconds of silence that ends a speaking turn and triggers transcription
SILENCE_THRESHOLD_S = float(os.getenv("SILENCE_THRESHOLD_S", "1.5"))

# Minimum duration of audio (in seconds) before we even attempt STT.
# At 48000 Hz / stereo / 16-bit, 1 second = 192000 bytes.
# Clips shorter than this are almost always noise and cause hallucinations.
MIN_AUDIO_SECONDS = float(os.getenv("MIN_AUDIO_SECONDS", "0.75"))
_BYTES_PER_SECOND = 48000 * 2 * 2  # rate * channels * width
MIN_PCM_BYTES = int(MIN_AUDIO_SECONDS * _BYTES_PER_SECOND)

# Derived from OpusDecoder so they always stay in sync with the library.
DISCORD_CHANNELS     = OpusDecoder.CHANNELS
DISCORD_SAMPLE_WIDTH = OpusDecoder.SAMPLE_SIZE // OpusDecoder.CHANNELS
DISCORD_SAMPLE_RATE  = OpusDecoder.SAMPLING_RATE

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


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

def _strip_reasoning(text: str) -> str:
    """Remove <think>...</think> reasoning blocks produced by CoT models."""
    return _THINK_RE.sub("", text).strip()


async def query_llm(conversation_history: list) -> str | None:
    """Send conversation history to Ollama /api/chat. Blocking call run in a thread."""
    import requests

    def _call():
        url = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/chat"
        try:
            resp = requests.post(
                url,
                json={
                    "model": OLLAMA_MODEL,
                    "messages": conversation_history,
                    "stream": False,
                    "think": False,  # suppresses reasoning for models that support it (e.g. qwen3)
                },
                timeout=60,
            )
            resp.raise_for_status()
            content = resp.json()["message"]["content"].strip()
            return _strip_reasoning(content) or None
        except Exception as e:
            logging.error(f"[listen_cmd] Ollama request failed: {e}")
            return None

    return await asyncio.to_thread(_call)


# ---------------------------------------------------------------------------
# AudioSink
# ---------------------------------------------------------------------------

class ConversationSink(voice_recv.AudioSink):
    """
    Receives raw Opus payloads (wants_opus=True) and decodes them per-user.

    We use wants_opus=True rather than letting voice_recv decode for us because
    the library's internal Opus decoder runs in the PacketRouter thread and will
    raise OpusError: corrupted stream if a DAVE-encrypted frame slips through
    before the session is fully ready — crashing the entire router. By owning
    the decode step ourselves we can catch and discard bad frames gracefully.

    DAVE E2EE decryption is handled transparently by the discord-ext-voice-recv
    library (PR #54) inside _process_packet before the Opus payload reaches us.

    Accumulates PCM per-user and fires an async callback after
    SILENCE_THRESHOLD_S seconds of quiet from a given user.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, callback, *, warmup_s: float = 0.5, target_user_id: int | None = None):
        super().__init__()
        self._loop = loop
        self._callback = callback  # async def callback(user, pcm: bytes)
        self._decoders:      dict[int, OpusDecoder]         = {}
        self._buffers:       dict[int, bytearray]           = defaultdict(bytearray)
        self._flush_handles: dict[int, asyncio.TimerHandle] = {}
        # If set, only audio from this Discord user ID is processed.
        self._target_user_id: int | None = target_user_id
        # Discard audio for a short period after creation so stale frames
        # buffered by the voice stack before the new sink was attached don't
        # sneak into the pipeline.
        self._muted: bool = True
        self._loop.call_later(warmup_s, self._unmute)

    def _unmute(self) -> None:
        self._muted = False
        logging.debug("[listen_cmd] ConversationSink warmup complete, now accepting audio.")

    def wants_opus(self) -> bool:
        # Take raw (post-DAVE-decrypt) Opus bytes so we can catch decode errors
        # ourselves rather than letting the router thread crash on bad frames.
        return True

    def _get_decoder(self, uid: int) -> OpusDecoder:
        if uid not in self._decoders:
            self._decoders[uid] = OpusDecoder()
        return self._decoders[uid]

    def write(self, user, data: voice_recv.VoiceData) -> None:
        if self._muted:
            return
        if user is None:
            return
        if self._target_user_id is not None and user.id != self._target_user_id:
            return
        if data.packet.is_silence():
            return

        opus_bytes = data.opus
        if not opus_bytes:
            return

        uid = user.id
        try:
            pcm = self._get_decoder(uid).decode(opus_bytes, fec=False)
        except Exception as e:
            # Drop corrupted / still-encrypted frames silently rather than crashing.
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
            asyncio.run_coroutine_threadsafe(self._callback(user, pcm), self._loop)

    @voice_recv.AudioSink.listener()
    def on_voice_member_disconnect(self, member: discord.Member, ssrc) -> None:
        uid = member.id
        handle = self._flush_handles.pop(uid, None)
        if handle is not None:
            handle.cancel()
        pcm = bytes(self._buffers.pop(uid, b""))
        self._decoders.pop(uid, None)
        if pcm:
            asyncio.run_coroutine_threadsafe(self._callback(member, pcm), self._loop)

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


    async def cog_unload(self) -> None:
        """Called automatically when the cog is unloaded (e.g. on bot shutdown).
        Stops listening and disconnects from voice so the AudioReader threads
        finish cleanly rather than blocking the event loop shutdown.
        """
        if self.voice_client is not None:
            if self.voice_client.is_listening():
                self.voice_client.stop_listening()
            if self.sink is not None:
                self.sink.cleanup()
                self.sink = None
            try:
                await self.voice_client.disconnect(force=True)
            except Exception as e:
                logging.warning(f"[listen_cmd] Error disconnecting on unload: {e}")
            self.voice_client = None
        logging.info("[listen_cmd] Cog unloaded cleanly.")

    # ------------------------------------------------------------------
    # Pipeline: Opus → PCM → Wyoming STT → Ollama → Wyoming TTS → Discord
    # ------------------------------------------------------------------

    async def _handle_audio(self, user, pcm: bytes) -> None:
        username = getattr(user, "name", str(user))
        logging.info(f"[listen_cmd] Processing {len(pcm)} PCM bytes from {username}.")

        _dump_wav(pcm, username, "prefilter")

        if len(pcm) < MIN_PCM_BYTES:
            logging.info(f"[listen_cmd] Clip too short from {username} ({len(pcm)} < {MIN_PCM_BYTES} bytes), ignoring.")
            return

        _dump_wav(pcm, username, "postfilter")

        # Stop listening immediately — one-shot mode means we don't resume
        # after responding. Use /listen again for the next exchange.
        vc = self.voice_client
        if not (vc and vc.is_connected()):
            return
        if vc.is_listening():
            vc.stop_listening()

        async def _cleanup_and_announce_done():
            """Clean up the sink after playback and signal the user to /listen again."""
            if self.sink is not None:
                self.sink.cleanup()
                self.sink = None
            if self._text_channel:
                await self._text_channel.send(
                    "✅ *Christotron hath spoken. Use* `/listen` *again when thou art ready.*"
                )

        if self._text_channel:
            await self._text_channel.send("🤔 *Christotron pondereth thy words...*")

        try:
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
                await _cleanup_and_announce_done()
                return

            if not transcript:
                logging.info(f"[listen_cmd] Empty transcript from {username}, ignoring.")
                await _cleanup_and_announce_done()
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
                await _cleanup_and_announce_done()
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
                await _cleanup_and_announce_done()
                return

            source = wav_to_discord_audio_source(wav_out)

            # 4. Play back; clean up after playback completes.
            if not (vc and vc.is_connected()):
                logging.warning("[listen_cmd] Voice client gone before playback, dropping reply.")
                return

            while vc.is_playing():
                await asyncio.sleep(0.1)

            def _after_play(err):
                if err:
                    logging.error(f"[listen_cmd] Playback error: {err}")
                asyncio.run_coroutine_threadsafe(_cleanup_and_announce_done(), self.bot.loop)

            vc.play(source, after=_after_play)

        except Exception:
            await _cleanup_and_announce_done()
            raise

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
    @app_commands.describe(
        target="Only listen to this user (leave blank to listen to everyone)",
        announce="Speak an announcement when Christotron starts listening (default: True)",
    )
    async def listen(
        self,
        interaction: discord.Interaction,
        target: discord.Member | None = None,
        announce: bool = True,
    ) -> None:
        await interaction.response.send_message("Lend me thine ears, for I am LISTENING.", ephemeral=True)

        vc = await self._join_channel(interaction)
        if vc is None:
            return

        if vc.is_listening():
            vc.stop_listening()
        if self.sink:
            self.sink.cleanup()
            self.sink = None
        # Small pause to let the voice stack drain any buffered packets from the
        # previous session before we attach the new sink. Without this, frames
        # that were already in-flight get delivered to the new sink immediately.
        await asyncio.sleep(0.3)

        self.voice_client = vc
        self._text_channel = interaction.channel

        target_id = target.id if target else None

        # warmup_s discards audio for the first 0.5s after the sink is attached,
        # catching any residual frames the sleep above didn't cover.
        self.sink = ConversationSink(
            self.bot.loop,
            self._handle_audio,
            warmup_s=0.5,
            target_user_id=target_id,
        )
        vc.listen(self.sink)

        target_note = f" (listening only to **{target.display_name}**)" if target else ""
        await interaction.channel.send(
            f"👂 Christotron is now listening in **{vc.channel.name}**{target_note}. "
            f"Speak thy piece — model: `{OLLAMA_MODEL}`."
        )

        if announce:
            announcement = (
                f"I am listening{f', {target.display_name}' if target else ''}. Speak."
            )
            try:
                wav_out = await wyoming_tts(
                    announcement,
                    host=PIPER_HOST,
                    port=PIPER_PORT,
                    voice=PIPER_VOICE,
                )
                vc.play(wav_to_discord_audio_source(wav_out))
            except Exception as e:
                logging.warning(f"[listen_cmd] Announce TTS failed: {e}")

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
            "Thy conversational sins are absolved. We begin anew.",
            ephemeral=True,
        )
import asyncio
import io
import logging
import time
import wave
from collections import defaultdict

import discord
import requests
from discord import app_commands
from discord.ext import commands

from utils import get_piper_audio_source_rest

# ---------------------------------------------------------------------------
# Configuration — override via environment variables or edit here directly
# ---------------------------------------------------------------------------
import os

WHISPER_HOST   = os.getenv("WHISPER_HOST",  "127.0.0.1")
WHISPER_PORT   = int(os.getenv("WHISPER_PORT", "9000"))
WHISPER_ENDPOINT = os.getenv("WHISPER_ENDPOINT", "/inference")   # whisper.cpp server default

OLLAMA_HOST    = os.getenv("OLLAMA_HOST",   "127.0.0.1")
OLLAMA_PORT    = int(os.getenv("OLLAMA_PORT", "11434"))
OLLAMA_MODEL   = os.getenv("OLLAMA_MODEL",  "llama3")

PIPER_HOST     = os.getenv("PIPER_HOST",    "127.0.0.1")
PIPER_PORT     = int(os.getenv("PIPER_PORT", "5000"))
PIPER_VOICE    = os.getenv("PIPER_VOICE",   "en_GB-semaine-medium")

# How long (seconds) of silence ends a "turn" and triggers transcription
SILENCE_THRESHOLD_S = float(os.getenv("SILENCE_THRESHOLD_S", "1.5"))

# Discord voice receives 48 kHz stereo 16-bit PCM
DISCORD_SAMPLE_RATE = 48000
DISCORD_CHANNELS    = 2
DISCORD_SAMPLE_WIDTH = 2  # bytes (int16)

# Personality prompt — edit freely
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
# Audio sink — accumulates PCM per user, fires a callback on silence
# ---------------------------------------------------------------------------

class VoiceSink(discord.AudioSink):
    """Collects raw PCM audio per user and fires an async callback when a
    speaker goes quiet for SILENCE_THRESHOLD_S seconds."""

    def __init__(self, callback):
        super().__init__()
        # callback: async def callback(user_id: int, pcm_bytes: bytes)
        self._callback = callback
        self._buffers: dict[int, bytearray] = defaultdict(bytearray)
        self._last_heard: dict[int, float] = {}
        self._flush_tasks: dict[int, asyncio.Task] = {}

    def write(self, user: discord.User, data: discord.AudioFrame):
        uid = user.id
        self._buffers[uid].extend(data.data)
        self._last_heard[uid] = time.monotonic()

        # (Re)start a flush timer each time we get audio
        if uid in self._flush_tasks and not self._flush_tasks[uid].done():
            self._flush_tasks[uid].cancel()
        self._flush_tasks[uid] = asyncio.get_event_loop().create_task(
            self._flush_after_silence(uid)
        )

    async def _flush_after_silence(self, uid: int):
        await asyncio.sleep(SILENCE_THRESHOLD_S)
        buf = bytes(self._buffers.pop(uid, b""))
        self._last_heard.pop(uid, None)
        if buf:
            await self._callback(uid, buf)

    def cleanup(self):
        for task in self._flush_tasks.values():
            task.cancel()
        self._buffers.clear()
        self._last_heard.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def pcm_to_wav(pcm: bytes,
               sample_rate: int = DISCORD_SAMPLE_RATE,
               channels: int = DISCORD_CHANNELS,
               sample_width: int = DISCORD_SAMPLE_WIDTH) -> bytes:
    """Wrap raw PCM bytes in a WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    buf.seek(0)
    return buf.read()


def transcribe(wav_bytes: bytes) -> str | None:
    """Send WAV audio to a local whisper.cpp HTTP server and return the transcript."""
    url = f"http://{WHISPER_HOST}:{WHISPER_PORT}{WHISPER_ENDPOINT}"
    try:
        resp = requests.post(
            url,
            files={"file": ("audio.wav", wav_bytes, "audio/wav")},
            data={"response_format": "json"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        # whisper.cpp server returns {"text": "..."}
        text = data.get("text", "").strip()
        return text or None
    except Exception as e:
        logging.error(f"[listen_cmd] Whisper transcription failed: {e}")
        return None


def query_llm(conversation_history: list[dict]) -> str | None:
    """Send conversation history to Ollama and return the assistant reply."""
    url = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/chat"
    payload = {
        "model": OLLAMA_MODEL,
        "messages": conversation_history,
        "stream": False,
    }
    try:
        resp = requests.post(url, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        return data["message"]["content"].strip()
    except Exception as e:
        logging.error(f"[listen_cmd] Ollama request failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Listen_Commands(bot))


class Listen_Commands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.listening: bool = False
        self.voice_client: discord.VoiceClient | None = None
        self.sink: VoiceSink | None = None
        # Per-user conversation histories: {user_id: [{"role": ..., "content": ...}]}
        self._histories: dict[int, list[dict]] = defaultdict(
            lambda: [{"role": "system", "content": SYSTEM_PROMPT}]
        )

    # ------------------------------------------------------------------
    # Internal pipeline: PCM → WAV → Whisper → Ollama → Piper → Discord
    # ------------------------------------------------------------------

    async def _handle_audio(self, user_id: int, pcm: bytes):
        """Full pipeline triggered when a user finishes speaking."""
        # Find the User object so we can log/send feedback
        user = self.bot.get_user(user_id)
        username = user.name if user else str(user_id)
        logging.info(f"[listen_cmd] Processing {len(pcm)} PCM bytes from {username}.")

        # 1. Convert to WAV
        wav = pcm_to_wav(pcm)

        # 2. Transcribe
        transcript = await asyncio.to_thread(transcribe, wav)
        if not transcript:
            logging.info(f"[listen_cmd] Empty transcript for {username}, skipping.")
            return
        logging.info(f"[listen_cmd] {username} said: {transcript!r}")

        # Optionally echo the transcript to the text channel the bot last used
        if self._text_channel:
            await self._text_channel.send(f"🎙️ **{username}**: {transcript}")

        # 3. Build prompt and query LLM
        history = self._histories[user_id]
        history.append({"role": "user", "content": transcript})
        reply = await asyncio.to_thread(query_llm, history)
        if not reply:
            logging.warning(f"[listen_cmd] LLM returned nothing for {username}.")
            return
        history.append({"role": "assistant", "content": reply})
        logging.info(f"[listen_cmd] LLM reply: {reply!r}")

        if self._text_channel:
            await self._text_channel.send(f"🤖 **Christotron**: {reply}")

        # 4. TTS via Piper
        source = await get_piper_audio_source_rest(reply, host=PIPER_HOST, port=PIPER_PORT, voice=PIPER_VOICE)

        # 5. Play back — queue if already playing
        if self.voice_client and self.voice_client.is_connected():
            while self.voice_client.is_playing():
                await asyncio.sleep(0.2)
            self.voice_client.play(
                source,
                after=lambda e: logging.error(f"[listen_cmd] Playback error: {e}") if e else None,
            )

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------

    @app_commands.command(name="listen", description="Start listening and responding in your voice channel")
    async def listen(self, interaction: discord.Interaction):
        """Join the caller's voice channel and start the listen→respond loop."""
        await interaction.response.send_message("Lend me thine ears, for I am LISTENING.", ephemeral=True)

        channel = interaction.user.voice.channel if interaction.user.voice else None
        if not channel:
            await interaction.channel.send("Thou art not in a voice channel, foolish mortal.")
            return

        # Join / reuse voice connection
        if interaction.client.voice_clients:
            vc = interaction.client.voice_clients[0]
            if vc.channel != channel:
                await vc.disconnect()
                vc = await channel.connect(timeout=60.0, reconnect=True)
        else:
            vc = await channel.connect(timeout=60.0, reconnect=True)

        self.voice_client = vc
        self._text_channel = interaction.channel
        self.listening = True

        self.sink = VoiceSink(self._handle_audio)
        vc.listen(self.sink)

        await interaction.channel.send(
            f"👂 Christotron is now listening in **{channel.name}**. "
            f"Speak, and receive divine wisdom (model: `{OLLAMA_MODEL}`)."
        )

    @app_commands.command(name="unlisten", description="Stop listening and leave the voice channel")
    async def unlisten(self, interaction: discord.Interaction):
        """Stop listening and disconnect."""
        await interaction.response.send_message("Silence! The Lord commands it.", ephemeral=True)
        self.listening = False

        if self.voice_client:
            self.voice_client.stop_listening()
            if self.sink:
                self.sink.cleanup()
                self.sink = None
            await self.voice_client.disconnect()
            self.voice_client = None

        await interaction.channel.send("🔇 Christotron hath stopped listening. Speak thy sins no more.")

    @app_commands.command(name="forget", description="Clear Christotron's memory of your conversation")
    async def forget(self, interaction: discord.Interaction):
        """Reset the conversation history for the calling user."""
        uid = interaction.user.id
        self._histories[uid] = [{"role": "system", "content": SYSTEM_PROMPT}]
        await interaction.response.send_message(
            "Thy sins are forgiven and forgotten. A new covenant begins.", ephemeral=False
        )

    @app_commands.command(name="setprompt", description="Override Christotron's personality system prompt")
    async def setprompt(self, interaction: discord.Interaction, prompt: str):
        """Replace the global system prompt (affects new conversations only)."""
        global SYSTEM_PROMPT
        SYSTEM_PROMPT = prompt
        await interaction.response.send_message(
            f"System prompt updated. New persona unlocked:\n> {prompt}", ephemeral=False
        )

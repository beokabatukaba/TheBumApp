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


async def query_llm(conversation_history: list) -> str | None:
    """Send conversation history to Ollama /api/chat. Blocking call run in a thread."""
    import requests

    def _call():
        url = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/chat"
        try:
            resp = requests.post(
                url,
                json={"model": OLLAMA_MODEL, "messages": conversation_history, "stream": False},
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json()["message"]["content"].strip()
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
    decoder). We manually decode Opus per-user with OpusDecoder.

    When DAVE E2EE is active, each Opus payload is a DAVE-encrypted frame.
    session.decrypt(user_id, MediaType.audio, packet) unwraps it to plain Opus
    bytes first, then we Opus-decode those to PCM.
    Order: DAVE decrypt -> Opus decode -> accumulate.

    Accumulates plaintext PCM and fires an async callback after
    SILENCE_THRESHOLD_S seconds of quiet from a given user.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, callback):
        super().__init__()
        self._loop = loop
        self._callback = callback  # async def callback(user, pcm: bytes)
        self._decoders:      dict[int, OpusDecoder]         = {}
        self._buffers:       dict[int, bytearray]           = defaultdict(bytearray)
        self._flush_handles: dict[int, asyncio.TimerHandle] = {}
        self._dave_session = None  # davey.DaveSession, set via set_dave_session()

    def set_dave_session(self, session) -> None:
        """Attach a davey.DaveSession for post-decode PCM decryption."""
        self._dave_session = session
        logging.info("[listen_cmd] DAVE session attached to ConversationSink.")

    def wants_opus(self) -> bool:
        # Must decode Opus ourselves — voice_recv's built-in decoder will crash
        # with OpusError: corrupted stream when DAVE is active, because the Opus
        # payload wraps DAVE-encrypted PCM bytes which aren't valid Opus data.
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
        uname = getattr(user, "name", str(uid))

        # 1. DAVE E2EE decryption (if session is active and ready).
        #    session.decrypt(user_id, MediaType.audio, packet) unwraps the DAVE
        #    frame to plain Opus bytes. user.id is the Discord user ID, not ssrc.
        if self._dave_session is not None and self._dave_session.ready:
            from davey import MediaType as DaveMediaType
            pre_len  = len(opus_bytes)
            pre_head = opus_bytes[:8].hex()
            pre_tail = opus_bytes[-4:].hex()
            try:
                opus_bytes = self._dave_session.decrypt(uid, DaveMediaType.audio, opus_bytes)
                post_len  = len(opus_bytes)
                post_head = opus_bytes[:8].hex()
                logging.debug(
                    f"[listen_cmd/dave] {uname} uid={uid} "
                    f"pre={pre_len}B head={pre_head} tail={pre_tail} "
                    f"post={post_len}B head={post_head}"
                )
            except Exception as e:
                logging.error(
                    f"[listen_cmd/dave] decrypt FAILED for {uname} "
                    f"uid={uid} pre={pre_len}B "
                    f"head={pre_head} tail={pre_tail}: {type(e).__name__}: {e}"
                )
                return
        elif self._dave_session is not None and not self._dave_session.ready:
            logging.debug(f"[listen_cmd/dave] Session not yet ready, dropping packet from {uname}")
            return
        else:
            logging.debug(f"[listen_cmd/dave] No DAVE session active for {uname}")

        # 2. Opus decode -> PCM
        try:
            pcm = self._get_decoder(uid).decode(opus_bytes, fec=False)
        except Exception as e:
            logging.warning(f"[listen_cmd] Opus decode error from {uname}: {e}")
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
        self._dave_session = None      # davey.DaveSession
        self._dave_ws_task = None      # asyncio.Task running the WS opcode handler

    # ------------------------------------------------------------------
    # DAVE session lifecycle
    #
    # DaveSession is a pure crypto object — there is no start()/stop().
    # We must manually wire it to the voice gateway by:
    #   1. Hooking the WS to receive DAVE opcodes (21-31)
    #   2. Sending our key package (op 22) after receiving op 21
    #   3. Processing proposals (op 27), welcome (op 30), commit (op 29)
    #   4. Decrypting audio with session.decrypt(user_id, MediaType.audio, pkt)
    #
    # DAVE gateway opcodes:
    #   21 = DAVE_PREPARE_TRANSITION   (server initiates, bot sends op 22)
    #   22 = DAVE_EXECUTE_TRANSITION   (bot sends key package)
    #   24 = DAVE_PREPARE_EPOCH        (server signals ready)
    #   25 = DAVE_MLS_EXTERNAL_SENDER  (server sends external sender blob)
    #   26 = DAVE_MLS_KEY_PACKAGE      (unused for receiving bot)
    #   27 = DAVE_MLS_PROPOSALS        (server sends proposals; bot must commit)
    #   28 = DAVE_MLS_COMMIT_WELCOME   (bot sends commit+optional welcome)
    #   29 = DAVE_MLS_ANNOUNCE_COMMIT  (server sends commit)
    #   30 = DAVE_MLS_WELCOME          (server sends welcome; bot processes it)
    #   31 = DAVE_MLS_INVALID_COMMIT_WELCOME (server signals bad commit)
    # ------------------------------------------------------------------

    async def _init_dave_session(self, vc: voice_recv.VoiceRecvClient) -> None:
        """Create a DaveSession and patch the voice_recv gateway hook to intercept DAVE opcodes."""
        try:
            from davey import DaveSession, DAVE_PROTOCOL_VERSION
        except ImportError:
            logging.warning(
                "[listen_cmd] 'davey' package not found — DAVE E2EE decryption disabled. "
                "Install it with: pip install davey"
            )
            return

        try:
            session = DaveSession(
                DAVE_PROTOCOL_VERSION,
                vc.guild.me.id,
                vc.channel.id,
            )
            self._dave_session = session

            if self.sink is not None:
                self.sink.set_dave_session(session)

            # --- Monkey-patch gateway.hook to intercept DAVE opcodes ---
            #
            # voice_recv's hook() is called by discord.py's DiscordVoiceWebSocket
            # for every inbound WS message.  We wrap it so that DAVE opcodes
            # (21-31) are also forwarded into a per-vc asyncio.Queue that our
            # _dave_ws_loop consumes.  The original hook still runs, so voice_recv
            # continues to function normally.
            #
            # We stash the queue on the vc object so _dave_ws_loop can find it,
            # and stash the orignal hook so _teardown_dave_session can restore it.

            import discord.ext.voice_recv.gateway as _gw

            dave_queue: asyncio.Queue = asyncio.Queue()
            vc._dave_queue = dave_queue  # type: ignore[attr-defined]

            _original_hook = _gw.hook

            async def _patched_hook(self_ws, msg):
                await _original_hook(self_ws, msg)
                op = msg.get('op', -1)
                if 21 <= op <= 31:
                    dave_queue.put_nowait(msg)

            _gw.hook = _patched_hook
            vc._dave_original_hook = _original_hook  # type: ignore[attr-defined]
            logging.info("[listen_cmd] gateway.hook patched for DAVE opcode interception.")

            # Also patch the already-instantiated VoiceConnectionState's ws hook,
            # because voice_recv passes `hook=hook` by reference at connect time —
            # the connection state captures it as a bound coroutine. We need to
            # update the live reference on the ws object itself.
            ws = getattr(getattr(vc, '_connection', None), 'ws', None)
            if ws is not None and hasattr(ws, '_hook'):
                async def _live_patched_hook(msg):
                    await _original_hook.__func__(ws, msg) if hasattr(_original_hook, '__func__') else await _original_hook(ws, msg)
                    op = msg.get('op', -1)
                    if 21 <= op <= 31:
                        dave_queue.put_nowait(msg)
                ws._hook = _live_patched_hook
                logging.info("[listen_cmd] Live ws._hook also patched.")

            self._dave_ws_task = asyncio.ensure_future(
                self._dave_ws_loop(vc, session)
            )
            logging.info("[listen_cmd] DAVE session created, WS loop started.")
        except Exception as e:
            logging.error(f"[listen_cmd] Failed to create DAVE session: {e}", exc_info=True)


    async def _dave_ws_loop(self, vc: voice_recv.VoiceRecvClient, session) -> None:
        """
        Consume DAVE opcodes forwarded into vc._dave_queue by the patched gateway hook.

        Replies are sent via vc._connection.ws.send_as_json(), which is the
        correct path — it serialises and writes to the underlying websocket
        without competing with discord.py's internal recv loop.
        """
        import base64
        import json
        from davey import ProposalsOperationType

        DAVE_OPCODES = {
            21: "PREPARE_TRANSITION",
            22: "EXECUTE_TRANSITION",
            24: "PREPARE_EPOCH",
            25: "EXTERNAL_SENDER",
            27: "PROPOSALS",
            28: "COMMIT_WELCOME",
            29: "ANNOUNCE_COMMIT",
            30: "WELCOME",
            31: "INVALID_COMMIT_WELCOME",
        }

        OP_EXECUTE_TRANSITION = 22
        OP_COMMIT_WELCOME     = 28

        queue: asyncio.Queue = getattr(vc, '_dave_queue', None)
        if queue is None:
            logging.error("[listen_cmd] No _dave_queue on vc — aborting DAVE WS loop.")
            return

        # Helper: send a dict as JSON through the live voice websocket.
        async def _send(payload: dict) -> None:
            ws = getattr(getattr(vc, '_connection', None), 'ws', None)
            if ws is None:
                logging.warning("[listen_cmd/dave] Cannot send — ws is None.")
                return
            # send_as_json is discord.py's standard method on DiscordVoiceWebSocket
            await ws.send_as_json(payload)

        logging.info("[listen_cmd] DAVE WS loop running.")
        try:
            while vc.is_connected() and self._dave_session is session:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                op: int = msg.get('op', -1)
                data: dict = msg.get('d') or {}
                op_name = DAVE_OPCODES.get(op, f"op{op}")
                logging.info(f"[listen_cmd/dave] Received {op_name} (op{op})")

                # ---- op21: server wants to start a DAVE transition ----
                if op == 21:
                    key_pkg = session.get_serialized_key_package()
                    await _send({
                        'op': OP_EXECUTE_TRANSITION,
                        'd': {'key_package': base64.b64encode(key_pkg).decode()}
                    })
                    logging.info("[listen_cmd/dave] op22 key package sent")

                # ---- op25: external sender blob ----
                elif op == 25:
                    raw = data.get('external_sender') or data.get('data') or b''
                    if isinstance(raw, str):
                        raw = base64.b64decode(raw)
                    elif isinstance(raw, list):
                        raw = bytes(raw)
                    session.set_external_sender(bytes(raw))
                    logging.info("[listen_cmd/dave] op25 external sender set")

                # ---- op27: MLS proposals — we must commit ----
                elif op == 27:
                    raw = data.get('proposals') or data.get('data') or b''
                    if isinstance(raw, str):
                        raw = base64.b64decode(raw)
                    elif isinstance(raw, list):
                        raw = bytes(raw)
                    op_type = ProposalsOperationType(data.get('operation_type', 0))
                    result = session.process_proposals(op_type, bytes(raw))
                    logging.info(f"[listen_cmd/dave] op27 proposals processed (op_type={op_type})")
                    if result is not None:
                        out: dict = {'commit': base64.b64encode(result.commit).decode()}
                        if result.welcome:
                            out['welcome'] = base64.b64encode(result.welcome).decode()
                        await _send({'op': OP_COMMIT_WELCOME, 'd': out})
                        logging.info("[listen_cmd/dave] op28 commit/welcome sent")

                # ---- op29: server's announce-commit ----
                elif op == 29:
                    raw = data.get('commit') or data.get('data') or b''
                    if isinstance(raw, str):
                        raw = base64.b64decode(raw)
                    elif isinstance(raw, list):
                        raw = bytes(raw)
                    session.process_commit(bytes(raw))
                    logging.info(f"[listen_cmd/dave] op29 commit processed, ready={session.ready}")

                # ---- op30: welcome (bot joined an existing group) ----
                elif op == 30:
                    raw = data.get('welcome') or data.get('data') or b''
                    if isinstance(raw, str):
                        raw = base64.b64decode(raw)
                    elif isinstance(raw, list):
                        raw = bytes(raw)
                    session.process_welcome(bytes(raw))
                    logging.info(f"[listen_cmd/dave] op30 welcome processed, ready={session.ready}")

                # ---- op24: epoch ready (informational) ----
                elif op == 24:
                    logging.info("[listen_cmd/dave] op24 PREPARE_EPOCH — session epoch advancing")

                # ---- op31: bad commit/welcome, session is broken ----
                elif op == 31:
                    logging.error("[listen_cmd/dave] op31 INVALID_COMMIT_WELCOME — DAVE session broken")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logging.error(f"[listen_cmd/dave] WS loop error: {e}", exc_info=True)

        logging.info("[listen_cmd] DAVE WS loop exited.")

    async def _teardown_dave_session(self) -> None:
        if self._dave_ws_task is not None:
            self._dave_ws_task.cancel()
            try:
                await self._dave_ws_task
            except asyncio.CancelledError:
                pass
            self._dave_ws_task = None

        # Restore the original gateway hook
        if self.voice_client is not None:
            original = getattr(self.voice_client, '_dave_original_hook', None)
            if original is not None:
                import discord.ext.voice_recv.gateway as _gw
                _gw.hook = original
                del self.voice_client._dave_original_hook
                logging.info("[listen_cmd] gateway.hook restored.")

        self._dave_session = None

    # ------------------------------------------------------------------
    # Pipeline: Opus -> PCM -> DAVE decrypt -> Wyoming STT -> Ollama -> Wyoming TTS -> Discord
    # ------------------------------------------------------------------

    async def _handle_audio(self, user, pcm: bytes) -> None:
        username = getattr(user, "name", str(user))
        logging.info(f"[listen_cmd] Processing {len(pcm)} PCM bytes from {username}.")

        _dump_wav(pcm, username, "prefilter")

        if len(pcm) < MIN_PCM_BYTES:
            logging.info(f"[listen_cmd] Clip too short from {username} ({len(pcm)} < {MIN_PCM_BYTES} bytes), ignoring.")
            return

        _dump_wav(pcm, username, "postfilter")

        # 1. Transcribe — pass raw PCM directly, wyoming_stt handles the framing
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

        # 4. Play back via the voice client, mirroring voice_cmd.py's speak_praise.
        #    Pause listening while we speak so we don't hear ourselves, then resume.
        vc = self.voice_client
        if not (vc and vc.is_connected()):
            logging.warning("[listen_cmd] Voice client gone before playback, dropping reply.")
            return

        # Stop listening so bot doesn't transcribe its own TTS output
        was_listening = vc.is_listening()
        if was_listening:
            vc.stop_listening()

        # Wait for any previous playback to finish
        while vc.is_playing():
            await asyncio.sleep(0.1)

        def _after_play(err):
            if err:
                logging.error(f"[listen_cmd] Playback error: {err}")
            # Resume listening after playback completes (called from a background thread)
            if was_listening and vc.is_connected() and self.sink is not None:
                vc.listen(self.sink)

        vc.play(source, after=_after_play)

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
        await self._teardown_dave_session()

        self.voice_client = vc
        self._text_channel = interaction.channel

        self.sink = ConversationSink(self.bot.loop, self._handle_audio)

        # Start DAVE session before attaching the sink so the MLS handshake
        # can begin while Discord sends the first frames.
        await self._init_dave_session(vc)

        vc.listen(self.sink)

        await interaction.channel.send(
            f"👂 Christotron is now listening in **{vc.channel.name}**. "
            f"Speak thy piece — model: `{OLLAMA_MODEL}`."
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
            await self._teardown_dave_session()
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
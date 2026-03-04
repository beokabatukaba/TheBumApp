import io
import os
import logging

import discord
from discord.ext import commands
from discord import app_commands

from utils import getShem, getShemm, getYee, getYeee, send_chunked_message
from resources.constants import *
from wyoming_client import wyoming_tts

# ---------------------------------------------------------------------------
# Configuration — override via environment variables if needed
# ---------------------------------------------------------------------------

VOICE_NAME  = os.getenv("PIPER_VOICE", "en_US-PordanJetersonMoM2017-ep595-medium")
PIPER_HOST  = os.getenv("PIPER_HOST",  "127.0.0.1")
PIPER_PORT  = int(os.getenv("PIPER_PORT", "10200"))


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

async def _tts_source(text: str) -> discord.FFmpegPCMAudio:
    """
    Call Wyoming Piper and wrap the returned WAV bytes in an FFmpegPCMAudio
    source ready for Discord playback.
    """
    wav_bytes = await wyoming_tts(
        text,
        host=PIPER_HOST,
        port=PIPER_PORT,
        voice=VOICE_NAME,
    )
    buf = io.BytesIO(wav_bytes)
    buf.seek(0)
    return discord.FFmpegPCMAudio(buf, pipe=True)


# ---------------------------------------------------------------------------
# Cog setup
# ---------------------------------------------------------------------------

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Voice_Commands(bot))


class Voice_Commands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def yeejoin(self, interaction: discord.Interaction):
        """Join the user's current voice channel (or reuse an existing connection)."""
        channel = interaction.user.voice.channel if interaction.user.voice else None
        if not channel:
            await interaction.channel.send("You are not connected to a voice channel.")
            return None

        if interaction.client.voice_clients:
            if channel in [vc.channel for vc in interaction.client.voice_clients]:
                logging.info("Already in correct channel.")
                return interaction.client.voice_clients[0]
            else:
                await interaction.client.voice_clients[0].disconnect()

        logging.info("Connecting to new channel.")
        return await channel.connect(timeout=60.0, reconnect=True)

    async def speak_praise(self, interaction: discord.Interaction, source: discord.FFmpegPCMAudio):
        """Join voice and play an audio source."""
        logging.info("Joining voice channel…")
        voice_client = await self.yeejoin(interaction)
        if voice_client is None:
            return
        logging.info("Speaking praise…")
        if not voice_client.is_playing():
            voice_client.play(
                source,
                after=lambda e: logging.error("Player error: %s", e) if e else None,
            )

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------

    @app_commands.command(name="sheme", description="A reading from the Holy Text.")
    async def sheme(self, interaction: discord.Interaction):
        await interaction.response.send_message("Have mercy on Shem.", ephemeral=True)
        async with interaction.channel.typing():
            text = getShem()
            source = await _tts_source(text)
        await send_chunked_message(interaction.channel, text)
        await self.speak_praise(interaction, source)

    @app_commands.command(name="shemme", description="A double reading from the Holy Text.")
    async def shemme(self, interaction: discord.Interaction):
        await interaction.response.send_message("Have mercy on Shem.", ephemeral=True)
        async with interaction.channel.typing():
            text = getShemm()
            source = await _tts_source(text)
        await send_chunked_message(interaction.channel, text)
        await self.speak_praise(interaction, source)

    @app_commands.command(name="yee_e", description="Hear ye, hear ye.")
    async def yee_e(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            f"Hear ye, hear ye, for this is the word of our lord.\nUsing voice: {VOICE_NAME}",
            ephemeral=True,
        )
        async with interaction.channel.typing():
            text = getYee()
            source = await _tts_source(text)
        await send_chunked_message(interaction.channel, text)
        await self.speak_praise(interaction, source)

    @app_commands.command(name="yeee_e", description="The sacred progenitor scripture.")
    async def yeee_e(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "Open your ears for the sacred progenitor scripture.", ephemeral=True
        )
        async with interaction.channel.typing():
            text = getYeee()
            source = await _tts_source(text)
        await send_chunked_message(interaction.channel, text)
        await self.speak_praise(interaction, source)

    @app_commands.command(name="bee_e", description="Say your dumb shit via Piper TTS.")
    async def bee_e(self, interaction: discord.Interaction, text: str):
        await interaction.response.send_message(
            "Fine, I guess I'll say your dumb shit.", ephemeral=True
        )
        async with interaction.channel.typing():
            source = await _tts_source(text)
        await send_chunked_message(interaction.channel, text)
        await self.speak_praise(interaction, source)

    @app_commands.command(name="yeevolume", description="Change the playback volume.")
    async def yeevolume(self, interaction: discord.Interaction, volume: int):
        if interaction.voice_client is None:
            return await interaction.channel.send("Not connected to a voice channel.")
        interaction.voice_client.source.volume = volume / 100
        await interaction.channel.send(f"Changed volume to {volume}%")

    @app_commands.command(name="yeestop", description="Stop playback and disconnect.")
    async def yeestop(self, interaction: discord.Interaction):
        await interaction.response.send_message("Aww :(.", ephemeral=True)
        if interaction.client.voice_clients:
            logging.info("Discovered existing voice client. Stopping.")
            interaction.client.voice_clients[0].stop()

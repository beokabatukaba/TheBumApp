import discord
from discord.ext import commands
from discord import app_commands
import logging
from discord.ext import commands
from utils import *
from resources.constants import *

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Voice_Commands(bot))

class Voice_Commands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def yeejoin(self, interaction: discord.Interaction):
        """Joins a voice channel"""
        channel = interaction.user.voice.channel
        if not channel:
            await interaction.channel.send("You are not connected to a voice channel.")
            return
        # Only allow one voice_client connection
        if interaction.client.voice_clients:
            if channel in [client.channel for client in interaction.client.voice_clients]:
                logging.info('Already in correct channel.')
                return interaction.client.voice_clients[0]
            else:
                await interaction.client.voice_clients[0].disconnect()
        
        logging.info('Connecting to new channel.')
        return await channel.connect(timeout=60.0, reconnect=True)
        
    @app_commands.command(name = "sheme", description = "My first application Command")
    async def sheme(self, interaction: discord.Interaction):
        """Plays a file from the local filesystem"""
        await interaction.response.send_message("Have mercy on Shem.", ephemeral=True)
        async with interaction.channel.typing():
            text = getShem()
            source = await load_praise(text)
        await interaction.channel.send(text)
        await self.speak_praise(interaction, source)
        
    @app_commands.command(name = "shemme", description = "My first application Command")
    async def shemme(self, interaction: discord.Interaction):
        """Plays a file from the local filesystem"""
        await interaction.response.send_message("Have mercy on Shem.", ephemeral=True)
        async with interaction.channel.typing():
            text = getShemm()
            source = await load_praise(text)
        await interaction.channel.send(text)
        await self.speak_praise(interaction, source)

    @app_commands.command(name="yee_e", description="My first application Command")
    async def yee_e(self, interaction: discord.Interaction):
        """Plays a file from the local filesystem"""
        await interaction.response.send_message("Hear ye, hear ye, for this is the word of our lord.", ephemeral=True)
        async with interaction.channel.typing():
            text = getYee()
            # source = await load_praise(text)
            source = await get_piper_audio_source_rest(text, voice="en_GB-semaine-medium")
        await interaction.channel.send(text)
        await self.speak_praise(interaction, source)

    @app_commands.command(name = "yeee_e", description = "My first application Command")
    async def yeee_e(self, interaction: discord.Interaction):
        """Plays a file from the local filesystem"""
        await interaction.response.send_message("Open your ears for the sacred progenitor scripture.", ephemeral=True)
        async with interaction.channel.typing():
            text = getYeee()
            source = await load_praise(text)
        await interaction.channel.send(text)
        await self.speak_praise(interaction, source)
        
    @app_commands.command(name = "yeevolume", description = "My first application Command")
    async def yeevolume(self, interaction: discord.Interaction, volume: int):
        """Changes the player's volume"""

        if interaction.voice_client is None:
            return await interaction.channel.send("Not connected to a voice channel.")

        interaction.voice_client.source.volume = volume / 100
        await interaction.channel.send("Changed volume to {}%".format(volume))

    @app_commands.command(name = "yeestop", description = "My first application Command")
    async def yeestop(self, interaction: discord.Interaction):
        """Stops and disconnects the bot from voice"""
        await interaction.response.send_message("Aww :(.", ephemeral=True)
        if interaction.client.voice_clients:
            logging.info('Discovered existing voice client. Stopping.')
            interaction.client.voice_clients[0].stop()

    # @app_commands.command(name = "stop", description = "My first application Command")
    # async def stop(self, interaction: discord.Interaction):
    #     """Stops and disconnects the bot from voice"""

    #     await interaction.voice_client.disconnect()
    # @fancy.before_invoke
    # @play.before_invoke
    # @yt.before_invoke
    # @stream.before_invoke
    # async def ensure_voice(interaction: discord.Interaction):
    #     if interaction.voice_client is None:
    #         if interaction.user.voice:
    #             await interaction.user.voice.channel.connect()
    #         else:
    #             await interaction.channel.send("You are not connected to a voice channel.")
    #             raise commands.CommandError("user not connected to a voice channel.")
    #     elif interaction.voice_client.is_playing():
    #         interaction.voice_client.stop()


    # @shemme.before_invoke
    # @sheme.before_invoke
    # @yee_e.before_invoke
    # async def YEEensure_voice(interaction: discord.Interaction):
    #     if interaction.voice_client is None:
    #         if interaction.user.voice:
    #             await interaction.user.voice.channel.connect()
    #         else:
    #             await interaction.channel.send("You are not connected to a voice channel.")
    #             # raise commands.CommandError("user not connected to a voice channel.")
    #     elif interaction.voice_client.is_playing():
    #         interaction.voice_client.stop()
   
    async def speak_praise(self, interaction: discord.Interaction, source):
        """Plays a file from the local filesystem"""
        logging.info('Joining voice channel...')
        voice_client = await self.yeejoin(interaction)
        logging.info('Speaking praise...')
        if not voice_client.is_playing():
            voice_client.play(source, after=lambda e: logging.info('Player error: %s' % e) if e else None)


            
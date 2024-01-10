import discord
import asyncio
from discord.ext import commands
from discord import app_commands
import youtube_dl
# import yt_dlp
import logging
import subprocess
from utils import to_thread

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music_Commands(bot))

class Music_Commands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.is_playing = False
        self.ctx = None
        self.voice = None
        self.song_queue = []
        self.YDL_OPTIONS = {'format': 'bestaudio', 'noplaylist':'True'}
        self.FFMPEG_OPTIONS = {'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5', 'options': '-vn'}
        # self.FFMPEG_OPTIONS = {'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5','options': '-vn -filter:a "volume=0.25"'}#optimised settings for ffmpeg for streaming

    def search_yt(self, item):
        
        with youtube_dl.YoutubeDL(self.YDL_OPTIONS) as ydl:
            try:
                info = ydl.extract_info(f"ytsearch:{item}", download=False)['entries'][0]
            except Exception:
                return False

        return {'source': info['formats'][0]['url'], 'title': info['title']}
    
    # def custom_probe(self, source, executable):
    #         # some analysis code here
    #         return 'copy', '96'
    
    # @to_thread
    async def play_music(self):
        
        self.is_playing = True

        while self.is_playing:
            if len(self.song_queue) > 0:
                m_url = self.song_queue[0]['source']
                self.song_queue.pop(0)
                audio = await discord.FFmpegOpusAudio.from_probe(m_url, **self.FFMPEG_OPTIONS)#, method='fallback')

                # video_id = m_url.split('=')[1]
                # filename = f'{video_id}.mp3'
                # subprocess.run(['yt-dlp', '-f', 'bestaudio', '-o', filename, m_url])
                # audio = discord.FFmpegOpusAudio(filename)

                # ydl_opts = {
                #     'format': 'm4a/bestaudio/best',
                #     # ℹ️ See help(yt_dlp.postprocessor) for a list of available Postprocessors and their arguments
                #     'postprocessors': [{  # Extract audio using ffmpeg
                #         'key': 'FFmpegExtractAudio',
                #         'preferredcodec': 'm4a',
                #     }]
                # }
                # with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                #     error_code = ydl.download(m_url)
                # video_id = m_url.split('=')[1]
                # filename = f'{video_id}.m4a'
                # audio = discord.FFmpegOpusAudio(filename)

                self.voice.play(audio, after=lambda e: self.bot.loop.create_task(self.play_music()))
                # while self.voice.is_playing():
                #     logging.info('is_playing')
                #     await asyncio.sleep(1)
            else:
                self.is_playing = False

    @app_commands.command(name="play")
    async def play(self, ctx: discord.Interaction, url: str):
        await ctx.response.send_message("May the lord bless these sick beats.", ephemeral=True)
        channel = ctx.user.voice.channel
        if not channel:
            await ctx.channel.send("You are not connected to a voice channel.")
            return
        # Only allow one voice_client connection
        if ctx.client.voice_clients:
            if channel in [client.channel for client in ctx.client.voice_clients]:
                self.voice = ctx.client.voice_clients[0]
                logging.info('Already in correct channel.')
            else:
                await ctx.client.voice_clients[0].disconnect()
                self.voice = await channel.connect(timeout=60.0, reconnect=True)
        else:
            self.voice = await channel.connect(timeout=60.0, reconnect=True)
        
        logging.info('Connecting to new channel.')
        self.ctx = ctx

        song = self.search_yt(url)
        if type(song) == type(True):
            await ctx.channel.send("Could not download the song. Incorrect format try another keyword.")
            return

        await ctx.channel.send(f"Added {song['title']} to the queue.\n{url}")
        self.song_queue.append(song)

        if self.is_playing == False:
            await self.play_music()

    @app_commands.command(name="stop")
    async def stop(self, ctx):
        await ctx.response.send_message("Forgive this humble servant for this sin.", ephemeral=True)
        self.song_queue = []
        self.is_playing = False
        self.voice.stop()
        await ctx.channel.send("Music stopped.")

    @app_commands.command(name="skip")
    async def skip(self, ctx):
        await ctx.response.send_message("Yeah I knew that one was arse.", ephemeral=True)
        if self.voice is not None and self.voice.is_playing():
            self.voice.stop()
            await ctx.channel.send("Skipped the song.")
        else:
            await ctx.channel.send("No song is currently playing.")

    @app_commands.command(name="queue")
    async def queue(self, ctx):
        await ctx.response.send_message("A new and powerful tune enters the chat.", ephemeral=True)
        if len(self.song_queue) == 0:
            await ctx.channel.send("The queue is empty.")
            return

        queue_list = ""
        for i, song in enumerate(self.song_queue):
            queue_list += f"{i+1}. {song['title']}\n"

        await ctx.channel.send(f"Current Queue:\n{queue_list}")

    @app_commands.command(name="pause")
    async def pause(self, ctx):
        await ctx.response.send_message("やめてください、兄さん。", ephemeral=True)
        if self.voice is not None and self.voice.is_playing():
            self.voice.pause()
            await ctx.channel.send("Paused the song.")
        else:
            await ctx.channel.send("No song is currently playing.")

    @app_commands.command(name="resume")
    async def resume(self, ctx):
        await ctx.response.send_message("お願いします、お兄さん。", ephemeral=True)
        if self.voice is not None and self.voice.is_paused():
            self.voice.resume()
            await ctx.channel.send("Resumed the song.")
        else:
            await ctx.channel.send("The song is not paused.")

    @app_commands.command(name="latency")
    async def latency(self, ctx):
        await ctx.response.send_message("Examining daddy musk's packets.", ephemeral=True)
        if self.voice:
            await ctx.channel.send(f"Instant latency is {self.voice.latency} seconds, average latency of the past 20 heartbeats is {self.voice.average_latency} seconds.")
        else:
            await ctx.channel.send("Not connected to a voice channel.")
        
# @client.tree.command(name = "fancy", description = "You know ;)")
# async def fancy(interaction, channel: discord.VoiceChannel):
#     """You know ;)"""
#     await yt(interaction, channel, url=FANCY_YT_LINK)
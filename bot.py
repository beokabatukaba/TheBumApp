#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot.py

Created on Sat May 30 11:23:35 2020

@author: The Mender of Arse Juice
"""

import os
import random
import discord
from dotenv import load_dotenv
import asyncio
import youtube_dl
from discord.ext import commands
from gtts import gTTS, lang

load_dotenv(verbose=True)
TOKEN = os.getenv('DISCORD_TOKEN')
GUILD = os.getenv('DISCORD_GUILD')
PARENT_DIRECTORY = os.path.dirname(os.path.abspath(__file__))
HOLY_SCRIPTURE_TXT = PARENT_DIRECTORY + '/' + "HolyScripture.txt"
USER_LEGEND_TXT = PARENT_DIRECTORY + '/' + "UserLegend.txt"
MAX_NUMBER_OF_HOLY_LINES = 10
ENOS_START_LINE_NUMBER = 103
ENOS_NUMBER_OF_LINES = 13
YEE_START_LINE_NUMBER = 196
YEE_NUMBER_OF_LINES = 6
SHEM_START_LINE_NUMBER = 299
SHEM_NUMBER_OF_LINES = 10
SHEM_PROBLEMATIC_NUMBER_OF_LINES = 13
DESCRIPTION = 'Praise be to our holy smut bot.'
LOCALIZATIONS = "com ad ae com.af com.ag com.ai al am co.ao com.ar as at com.au az ba com.bd be bf bg com.bh bi bj com.bn com.bo com.br bs bt co.bw by com.bz ca cd cf cg ch ci co.ck cl cm cn com.co co.cr com.cu cv com.cy cz de dj dk dm com.do dz com.ec ee com.eg es com.et fi com.fj fm fr ga ge gg com.gh com.gi gl gm gr com.gt gy com.hk hn hr ht hu co.id ie co.il im co.in iq is it je com.jm jo co.jp co.ke com.kh ki kg co.kr com.kw kz la com.lb li lk co.ls lt lu lv com.ly co.ma md me mg mk ml com.mm mn ms com.mt mu mv mw com.mx com.my co.mz com.na com.ng com.ni ne nl no com.np nr nu co.nz com.om com.pa com.pe com.pg com.ph com.pk pl pn com.pr ps pt com.py com.qa ro ru rw com.sa com.sb sc se com.sg sh si sk com.sl sn so sm sr st com.sv td tg co.th com.tj tl tm tn to com.tr tt com.tw co.tz com.ua co.ug co.uk com.uy co.uz com.vc co.ve vg co.vi com.vn vu ws rs co.za co.zm co.zw cat"

#intents = discord.Intents.default()
#intents.members = True

bot = commands.Bot(
    command_prefix = commands.when_mentioned_or("!"),
    description = DESCRIPTION
)

class YeeCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        
    async def on_ready(self, ctx):
        print(f'{bot.user.name} has connected to Discord!')
        # for server in client.guilds:
        #     for channel in server.text_channels:
        #         if channel.name == 'general':
        #             generalChannel = channel
        # print(generalChannel.name)
        # await generalChannel.send("Third time's the charm.")
    @commands.Cog.listener()
    async def on_member_join(self, member):
        await member.create_dm()
        await member.dm_channel.send(
            f'Hi {member.name}, welcome to my Discord server!'
        )
        
    @commands.Cog.listener()
    async def on_message(self, message):
        print('Message received via ' + repr(message.channel) + ' of type ' + repr(message.channel.type))
        print(message.content)
        if message.author == bot.user:
            return
        
        if repr(message.channel.type).find('private') != -1:
            print('Private message received.')
            if message.content == 'logout':
                print('Logging out.')
                await bot.close()
        
    @commands.command()
    async def YEE(self, ctx):            
        await ctx.channel.send('A reading from the Holy Text.'+ '\n\n' + getYee() + '\n' + 'This is the word of our Lord.')
        
    @commands.command()
    async def legend(self, ctx):        # Read in the user legend and print it. Maybe make this fancier later.
        userLegendFile = open(USER_LEGEND_TXT, "r", encoding="utf8")
        userLegendLines = userLegendFile.readlines()
        userLegendFile.close()
        
        await ctx.channel.send(''.join(userLegendLines))
    
    @commands.command()
    async def roles(self, ctx):
        
        # Read in the Holy Scripture pertaining to our roles
        holyScriptureFile = open(HOLY_SCRIPTURE_TXT, "r", encoding="utf8")
        holyScriptureReading = ''.join(holyScriptureFile.readlines()[ENOS_START_LINE_NUMBER:(ENOS_START_LINE_NUMBER + ENOS_NUMBER_OF_LINES)])
        holyScriptureFile.close()
        
        await ctx.channel.send(holyScriptureReading + '\n' + 'These are our sacred roles & duties, as given by our Lord.')
        
    @commands.command()
    async def YEEE(self, ctx):
        
        # Read in the Holy Scripture pertaining to YEE
        holyScriptureFile = open(HOLY_SCRIPTURE_TXT, "r", encoding="utf8")
        holyScriptureReading = ''.join(holyScriptureFile.readlines()[YEE_START_LINE_NUMBER:(YEE_START_LINE_NUMBER + YEE_NUMBER_OF_LINES)])
        holyScriptureFile.close()
        
        await ctx.channel.send(holyScriptureReading + '\n' + 'Amen.')
        
    @commands.command()
    async def helpplz(self, ctx):
        listOfCommands = """
    !YEE: Display a random snippet from our holy document.
    !YEEE: Display the inspiration for the whole YEEEEEEEEEEEEEEEEEE thing.
    !legend: Display the names of the actual humans(?) behind the masks.
    !roles: Display the primary scriptural inspiration for our roles.
    !wut: Display an explanation of this bot.
    !help/!commands/!list/!?: Display this very message.
    !SHEM: Display an excerpt about Shem.
    !SHEMM: Display a more problematic extension of the excerpt about Shem.
    """
        
        await ctx.channel.send(''.join(listOfCommands))
        
    @commands.command()
    async def wut(self, ctx):
        # Explain what this bot is about
        explanation = "I am an evangelist for YEEEEEEEEEEEEEEEEEE, the holy word we are commanded to proclaim by our Lord, Noah. This command can be found in Chapter 2 of our holy document, generated by a machine learning algorithm based on GPT-2, trained on a bdsm dataset scraped from Literotica, and fed sliding window prompts extracted from the King James Bible. For more information, see this link: https://www.reddit.com/r/MachineLearning/comments/fvwwzj/project_if_gpt2_read_erotica_what_would_be_its/"
        
        await ctx.channel.send(explanation)

    @commands.command()
    async def SHEM(self, ctx):
        
        # Read in the Holy Scripture pertaining to YEE
        holyScriptureFile = open(HOLY_SCRIPTURE_TXT, "r", encoding="utf8")
        holyScriptureReading = ''.join(holyScriptureFile.readlines()[SHEM_START_LINE_NUMBER:(SHEM_START_LINE_NUMBER + SHEM_NUMBER_OF_LINES)])
        holyScriptureFile.close()
        
        await ctx.channel.send(holyScriptureReading + '\n' + 'Lord have mercy on Shem.')

    @commands.command()
    async def SHEMM(self, ctx):
        
        # Read in the Holy Scripture pertaining to YEE
        holyScriptureFile = open(HOLY_SCRIPTURE_TXT, "r", encoding="utf8")
        holyScriptureReading = ''.join(holyScriptureFile.readlines()[SHEM_START_LINE_NUMBER:(SHEM_START_LINE_NUMBER + SHEM_PROBLEMATIC_NUMBER_OF_LINES)])
        holyScriptureFile.close()
        
        await ctx.channel.send(holyScriptureReading + '\n' + 'Amen.')


class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)

        self.data = data

        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))

        if 'entries' in data:
            # take first item from a playlist
            data = data['entries'][0]

        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)


class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def join(self, ctx, *, channel: discord.VoiceChannel):
        """Joins a voice channel"""

        if ctx.voice_client is not None:
            return await ctx.voice_client.move_to(channel)

        await channel.connect()

    @commands.command()
    async def play(self, ctx, *, query):
        """Plays a file from the local filesystem"""

        source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(query))
        ctx.voice_client.play(source, after=lambda e: print('Player error: %s' % e) if e else None)

        await ctx.send('Now playing: {}'.format(query))

    @commands.command()
    async def yt(self, ctx, *, url):
        """Plays from a url (almost anything youtube_dl supports)"""

        async with ctx.typing():
            player = await YTDLSource.from_url(url, loop=self.bot.loop)
            ctx.voice_client.play(player, after=lambda e: print('Player error: %s' % e) if e else None)

        await ctx.send('Now playing: {}'.format(player.title))

    @commands.command()
    async def stream(self, ctx, *, url):
        """Streams from a url (same as yt, but doesn't predownload)"""

        async with ctx.typing():
            player = await YTDLSource.from_url(url, loop=self.bot.loop, stream=True)
            ctx.voice_client.play(player, after=lambda e: print('Player error: %s' % e) if e else None)

        await ctx.send('Now playing: {}'.format(player.title))

    @commands.command()
    async def volume(self, ctx, volume: int):
        """Changes the player's volume"""

        if ctx.voice_client is None:
            return await ctx.send("Not connected to a voice channel.")

        ctx.voice_client.source.volume = volume / 100
        await ctx.send("Changed volume to {}%".format(volume))

    @commands.command()
    async def stop(self, ctx):
        """Stops and disconnects the bot from voice"""

        await ctx.voice_client.disconnect()

    @play.before_invoke
    @yt.before_invoke
    @stream.before_invoke
    async def ensure_voice(self, ctx):
        if ctx.voice_client is None:
            if ctx.author.voice:
                await ctx.author.voice.channel.connect()
            else:
                await ctx.send("You are not connected to a voice channel.")
                raise commands.CommandError("Author not connected to a voice channel.")
        elif ctx.voice_client.is_playing():
            ctx.voice_client.stop()
            
class YeeSpeech(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def YEEjoin(self, ctx, *, channel: discord.VoiceChannel):
        """Joins a voice channel"""

        if ctx.voice_client is not None:
            return await ctx.voice_client.move_to(channel)

        await channel.connect()

    @commands.command()
    async def YEEe(self, ctx):
        """Plays a file from the local filesystem"""
        text = 'A reading from the Holy Text.'+ '\n\n' + getYee() + '\n' + 'This is the word of our Lord.'
        languages = lang.tts_langs()
        language = random.choice(list(languages.keys()))
        print('Using language {}'.format(languages[language]))
        localization = random.choice(LOCALIZATIONS.split())
        print('Using localization {}'.format(localization))
        tts = gTTS(text, lang='en', tld='ie')
        file = './tmp.mp3'
        tts.save(file)
        source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(file))
        ctx.voice_client.play(source, after=lambda e: print('Player error: %s' % e) if e else None)

        await ctx.send(text)

    @commands.command()
    async def YEEvolume(self, ctx, volume: int):
        """Changes the player's volume"""

        if ctx.voice_client is None:
            return await ctx.send("Not connected to a voice channel.")

        ctx.voice_client.source.volume = volume / 100
        await ctx.send("Changed volume to {}%".format(volume))

    @commands.command()
    async def YEEstop(self, ctx):
        """Stops and disconnects the bot from voice"""

        await ctx.voice_client.disconnect()

    @YEEe.before_invoke
    async def YEEensure_voice(self, ctx):
        if ctx.voice_client is None:
            if ctx.author.voice:
                await ctx.author.voice.channel.connect()
                #await ctx.author.voice.channel.join()
            else:
                await ctx.send("You are not connected to a voice channel.")
                raise commands.CommandError("Author not connected to a voice channel.")
        elif ctx.voice_client.is_playing():
            ctx.voice_client.stop()
    
def initYoutubeDL():
    
    # Suppress noise about console usage from errors
    youtube_dl.utils.bug_reports_message = lambda: ''
    
    ytdl_format_options = {
        'format': 'bestaudio/best',
        'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
        'restrictfilenames': True,
        'noplaylist': True,
        'nocheckcertificate': True,
        'ignoreerrors': False,
        'logtostderr': False,
        'quiet': True,
        'no_warnings': True,
        'default_search': 'auto',
        'source_address': '0.0.0.0' # bind to ipv4 since ipv6 addresses cause issues sometimes
    }
    
    ffmpeg_options = {
        'options': '-vn'
    }
    
    ytdl = youtube_dl.YoutubeDL(ytdl_format_options)
    
    return ffmpeg_options, ytdl

def getYee():
    
    # Read in the Holy Scripture and get the number of lines
    holyScriptureFile = open(HOLY_SCRIPTURE_TXT, "r", encoding="utf8")
    holyScriptureLines = holyScriptureFile.readlines()
    holyScriptureFile.close()
    holySize = len(holyScriptureLines)
    
    # Generate a random length of lines to read from the holy scripture
    holyReadingLength = random.randint(1,MAX_NUMBER_OF_HOLY_LINES)
    
    # Subtract holyLength from holySize to avoid overflow
    holyStart  = random.randint(0,holySize-holyReadingLength)
    holyStop   = holyStart + holyReadingLength

    # Join the list into a single string, inserting empty characters between each element
    # The newline characters will be interpreted appropriately by the send() below
    holyScriptureReading = ''.join(holyScriptureLines[holyStart:holyStop])
    
    return holyScriptureReading    
        
ffmpeg_options, ytdl = initYoutubeDL()

bot.add_cog(YeeCommands(bot))
bot.add_cog(Music(bot))
bot.add_cog(YeeSpeech(bot))
bot.run(TOKEN)

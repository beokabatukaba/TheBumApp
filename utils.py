import asyncio
import random
import typing
import functools
from resources.constants import *
import os
from gtts import gTTS, lang
import io
import logging
import discord

PARENT_DIRECTORY = os.path.dirname(os.path.abspath(__file__))
HOLY_SCRIPTURE_TXT = f"{PARENT_DIRECTORY}/resources/HolyScripture.txt"
USER_LEGEND_TXT = f"{PARENT_DIRECTORY}/resources/UserLegend.txt"

def getShem():
    # Read in the Holy Scripture pertaining to SHEM
    holyScriptureFile = open(HOLY_SCRIPTURE_TXT, "r", encoding="utf8")
    holyScriptureReading = ''.join(holyScriptureFile.readlines()[SHEM_START_LINE_NUMBER:(SHEM_START_LINE_NUMBER + SHEM_NUMBER_OF_LINES)])
    holyScriptureFile.close()
    
    text = 'A reading from the Holy Text pertaining to Shem.' + '\n\n' + holyScriptureReading + '\n' + 'Lord have mercy on Shem.'
    
    return text
    
def getShemm():
    # Read in the Holy Scripture pertaining to SHEM (and more)
    holyScriptureFile = open(HOLY_SCRIPTURE_TXT, "r", encoding="utf8")
    holyScriptureReading = ''.join(holyScriptureFile.readlines()[SHEM_START_LINE_NUMBER:(SHEM_START_LINE_NUMBER + SHEM_PROBLEMATIC_NUMBER_OF_LINES)])
    holyScriptureFile.close()
    text = 'A reading from the Holy Text pertaining to Shem.' + '\n\n' + holyScriptureReading + '\n' + 'Lord have mercy on Shem.'
    
    return text

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
    
    return 'A reading from the Holy Text.' + '\n\n' + holyScriptureReading + '\n' + 'This is the word of our Lord.'  

def getYeee():
    # Read in the Holy Scripture pertaining to YEE
    holyScriptureFile = open(HOLY_SCRIPTURE_TXT, "r", encoding="utf8")
    holyScriptureReading = ''.join(holyScriptureFile.readlines()[YEE_START_LINE_NUMBER:(YEE_START_LINE_NUMBER + YEE_NUMBER_OF_LINES)])
    holyScriptureFile.close()

    return 'A reading of great glory from the Holy Text.' + '\n\n' + holyScriptureReading + '\n' + 'Amen.'  

def to_thread(func: typing.Callable) -> typing.Coroutine:
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        return await asyncio.to_thread(func, *args, **kwargs)
    return wrapper

@to_thread
def load_praise(text):
    """Create audio source from text using Google TTS"""
    languages = lang.tts_langs()
    language = random.choice(list(languages.keys()))
    logging.info('Using language {}'.format(languages[language]))
    localization = random.choice(LOCALIZATIONS.split())
    logging.info('Using localization {}'.format(localization))
    logging.info('Generating TTS.')
    # tts = gTTS(text, lang=language, tld=localization)
    tts = gTTS(text, lang='en', tld='co.uk')
    file = io.BytesIO()
    # with TemporaryFile() as file:
    logging.info('Saving TTS to buffer.')
    tts.write_to_fp(file)
    logging.info('Seeking to start of buffer.')
    file.seek(0)
    logging.info('Loading source from buffer.')
    source = discord.FFmpegPCMAudio(file, pipe=True)
    return source
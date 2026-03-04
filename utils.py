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

    # holyScriptureReading = holyScriptureReading.replace('.', '!')
    
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

@to_thread
def get_piper_audio_source_rest(text: str,
                          host: str = "127.0.0.1",
                          port: int = 5000,
                          endpoint: str = "/api/text-to-speech",
                          voice: typing.Optional[str] = None) -> discord.FFmpegPCMAudio:
    """Fetch synthesized audio from a Wyoming/Piper TTS HTTP server and return a Discord audio source.

    Assumptions made (adjustable via parameters):
    - The Piper server is reachable at http://{host}:{port}{endpoint}.
    - The server accepts a JSON POST with at least a "text" field and optionally "voice".
    - The server responds with raw audio bytes (wav/ogg/pcm). The handler will pass the bytes
      to FFmpeg which will decode them for Discord playback.

    If your Piper server uses a different HTTP contract (different path, query params or form encoding),
    change the `endpoint` or replace the request body construction below.
    """
    import requests
    url = f"http://{host}:{port}{endpoint}"
    headers = {"accept": "audio/wav"}
    # Remove literal newlines and collapse consecutive whitespace so the server
    # receives a single-line text string. This replaces newlines, tabs, etc.
    cleaned_text = ' '.join(text.split())
    data = cleaned_text
    if voice:
        url += f"?voice={voice}"

    logging.info(f"Requesting TTS from {url} with params {data}")
    response = requests.post(url, headers=headers, data=data)
    logging.info(f"Response status: {response.status_code}")
    response.raise_for_status()
    audio_bytes = response.content
    logging.info(f"Received {len(audio_bytes)} bytes of audio data from TTS server.")

    # Wrap bytes in a buffer and hand off to FFmpeg via pipe
    file = io.BytesIO(audio_bytes)
    file.seek(0)
    source = discord.FFmpegPCMAudio(file, pipe=True)
    return source

async def send_chunked_message(channel: discord.TextChannel, text: str, max_length: int = 2000) -> list:
    """
    Sends a message to a text channel, splitting it into multiple messages if it exceeds max_length.
    
    Args:
        channel: The Discord text channel to send messages to
        text: The text content to send
        max_length: Maximum length per message (default 2000, Discord's limit)
    
    Returns:
        A list of Message objects that were sent
    
    Raises:
        discord.errors.HTTPException: If the message cannot be sent
    """
    sent_messages = []
    
    # If the message fits in one chunk, send it directly
    if len(text) <= max_length:
        sent_messages.append(await channel.send(text))
        return sent_messages
    
    # Split the message into chunks
    chunks = []
    current_chunk = ""
    
    # Split by newlines first to preserve formatting
    lines = text.split('\n')
    
    for line in lines:
        # If a single line exceeds max_length, split it by spaces
        if len(current_chunk) + len(line) + 1 > max_length:
            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = ""
            
            # Handle lines longer than max_length
            if len(line) > max_length:
                words = line.split(' ')
                temp_chunk = ""
                for word in words:
                    if len(temp_chunk) + len(word) + 1 > max_length:
                        if temp_chunk:
                            chunks.append(temp_chunk)
                        temp_chunk = word
                    else:
                        if temp_chunk:
                            temp_chunk += " " + word
                        else:
                            temp_chunk = word
                if temp_chunk:
                    chunks.append(temp_chunk)
            else:
                current_chunk = line
        else:
            if current_chunk:
                current_chunk += "\n" + line
            else:
                current_chunk = line
    
    # Add the last chunk
    if current_chunk:
        chunks.append(current_chunk)
    
    # Send all chunks
    for chunk in chunks:
        sent_messages.append(await channel.send(chunk))
    
    return sent_messages
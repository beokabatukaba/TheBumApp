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

load_dotenv(verbose=True)
TOKEN = os.getenv('DISCORD_TOKEN')
GUILD = os.getenv('DISCORD_GUILD')
PARENT_DIRECTORY = os.path.dirname(os.path.abspath(__file__))
HOLY_SCRIPTURE_TXT = PARENT_DIRECTORY + '\\' + "HolyScripture.txt"
USER_LEGEND_TXT = PARENT_DIRECTORY + '\\' + "UserLegend.txt"
MAX_NUMBER_OF_HOLY_LINES = 10

client = discord.Client()

@client.event
async def on_ready():
    print(f'{client.user.name} has connected to Discord!')
    for server in client.guilds:
        for channel in server.text_channels:
            if channel.name == 'general':
                generalChannel = channel
    #print(generalChannel.name)
    #await generalChannel.send("Third time's the charm.")

@client.event
async def on_member_join(member):
    await member.create_dm()
    await member.dm_channel.send(
        f'Hi {member.name}, welcome to my Discord server!'
    )
    
@client.event
async def on_message(message):
    print('Message received via ' + repr(message.channel) + ' of type ' + repr(message.channel.type))
    if message.author == client.user:
        return
    
    if repr(message.channel.type).find('private') != -1:
        print('Private message received.')
        if message.content == 'logout':
            print('Logging out.')
            await client.close()

    if message.content == '!YEE':
        
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
        
        await message.channel.send(holyScriptureReading + '\n' + 'This is the word of our lord.')
        
    if message.content == '!legend' or message.content == '!Legend':
        # Read in the user legend and print it. Maybe make this fancier later.
        userLegendFile = open(USER_LEGEND_TXT, "r", encoding="utf8")
        userLegendLines = userLegendFile.readlines()
        userLegendFile.close()
        
        await message.channel.send(''.join(userLegendLines))
        
client.run(TOKEN)

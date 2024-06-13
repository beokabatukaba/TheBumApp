#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot.py

Created on Sat May 30 11:23:35 2020

@user: The Mender of Arse Juice
"""
import asyncio
import os
import io
import random
import discord
import youtube_dl
import watchgod
import logging
import logging.handlers
import ast
import functools
import typing
import platform
from discord.ext import commands
from dotenv import load_dotenv
from client import MyClient
from gtts import gTTS, lang
from resources.constants import *


load_dotenv(verbose=True)
DG_ID = os.getenv('DISCORD_GUILD_ID')
MY_GUILD = discord.Object(id=DG_ID)
TOKEN = os.getenv('DISCORD_TOKEN')
PARENT_DIRECTORY = os.path.dirname(os.path.abspath(__file__))
HOLY_SCRIPTURE_TXT = f"{PARENT_DIRECTORY}/resources/HolyScripture.txt"
USER_LEGEND_TXT = f"{PARENT_DIRECTORY}/resources/UserLegend.txt"

intents = discord.Intents.default()
intents.message_content = True
client = MyClient(intents=intents)

# @client.command(name="sync")
@client.tree.command(name = "sync", description = "My first application Command")
@commands.guild_only()
@commands.is_owner()
async def sync(interaction: discord.Interaction) -> None:
    logging.info("Syncing")
    await interaction.response.send_message('Syncing', ephemeral=True)
    synced = await client.tree.sync()
    logging.info(f"Synced {len(synced)} commands")
    await interaction.channel.send(f"Synced {len(synced)} commands")
    return

async def start_bot():
    await client.start(TOKEN)

async def stop_bot():
    await client.logout()

async def reload_extension(filename: str):
    try:
        await client.reload_extension(f"extensions.{filename[:-3]}")
        logging.info("Extension reloaded successfully.")
    except Exception as e:
        logging.warning(f"Failed to reload extension: {e}")

async def watch_extensions():
    async for changes in watchgod.awatch("./extensions/", watcher_cls=watchgod.DefaultWatcher):
        for change in changes:
            path = change[1].lstrip('./extensions/')
            print(path)
            logging.info(f"Detected change in {path}.")
            try:
                with open(change[1], "rb") as f:
                    ast.parse(f.read())
            except Exception as e:
                logging.error(f"Failed to reload extension on account of error {e} contains invalid Python code.")
            else:
                await reload_extension(path)

async def main():
    # Necessary for Linux I guess but not Windows
    if platform.system() != 'Windows':
        # This might be the path with sudo apt install libopus-dev
        discord.opus.load_opus('/usr/lib/x86_64-linux-gnu/libopus.so.0.9.0')
        if not discord.opus.is_loaded():
            raise RuntimeError('Opus failed to load')
    async with client:
        await asyncio.gather(start_bot(), watch_extensions())

if __name__ == '__main__':

    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())

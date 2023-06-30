#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot.py

Created on Thurs June 22

@author: The Mender of Arse Juice
"""
import os
from discord.ext import commands
import discord
from dotenv import load_dotenv


load_dotenv(verbose=True)
DG_ID = os.getenv('DISCORD_GUILD_ID')
MY_GUILD = discord.Object(id=DG_ID)

class MyClient(commands.Bot):
    def __init__(self, *, intents: discord.Intents):
        super().__init__(
            intents=intents,
            command_prefix="!",
            description='Praise be our holy smut bot',
            timeout=10
        )
        # A CommandTree is a special type that holds all the application command
        # state required to make it work. This is a separate class because it
        # allows all the extra state to be opt-in.
        # Whenever you want to work with application commands, your tree is used
        # to store and work with them.
        # Note: When using commands.Bot instead of discord.Client, the bot will
        # maintain its own tree instead.
        # self.tree = app_commands.CommandTree(self)
        print('client initialized')
        

    async def on_ready(self):
        await self.wait_until_ready()
        print('Logged in as: {0.user.name} Bots user id: {0.user.id}'.format(self))

    async def on_message(self, message):
        print(f'Message from {message.author}: {message.content}')
    # In this basic example, we just synchronize the app commands to one guild.
    # Instead of specifying a guild to every command, we copy over our global commands instead.
    # By doing so, we don't have to wait up to an hour until they are shown to the end-user.
    async def setup_hook(self):
        for filename in os.listdir('./extensions'):
            if filename.endswith('.py'):
                print(f"Loading extension: extensions.{filename[:-3]}")
                await self.load_extension(f'extensions.{filename[:-3]}')
        # await self.load_extension("music_copilot")
        # This copies the global commands over to your guild.
        self.tree.copy_global_to(guild=MY_GUILD)
        await self.tree.sync(guild=MY_GUILD)


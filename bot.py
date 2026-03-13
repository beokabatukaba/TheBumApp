#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot.py
"""
import asyncio
import os
import ast
import platform
import signal
import logging
import logging.handlers

import discord
import watchgod
from discord.ext import commands
from dotenv import load_dotenv
from client import MyClient
from resources.constants import *


load_dotenv(verbose=True)
DG_ID = os.getenv('DISCORD_GUILD_ID')
MY_GUILD = discord.Object(id=DG_ID)
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True
client = MyClient(intents=intents)


@client.tree.command(name="sync", description="Sync application commands")
@commands.guild_only()
@commands.is_owner()
async def sync(interaction: discord.Interaction) -> None:
    logging.info("Syncing")
    await interaction.response.send_message('Syncing', ephemeral=True)
    synced = await client.tree.sync()
    logging.info(f"Synced {len(synced)} commands")
    await interaction.channel.send(f"Synced {len(synced)} commands")


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
            logging.info(f"Detected change in {path}.")
            try:
                with open(change[1], "rb") as f:
                    ast.parse(f.read())
            except Exception as e:
                logging.error(f"Extension has invalid Python, skipping reload: {e}")
            else:
                await reload_extension(path)


async def main():
    if platform.system() != 'Windows':
        discord.opus.load_opus('/usr/lib/x86_64-linux-gnu/libopus.so.0.9.0')
        if not discord.opus.is_loaded():
            raise RuntimeError('Opus failed to load')

    loop = asyncio.get_running_loop()

    # --- Clean shutdown on Ctrl-C (SIGINT) or SIGTERM ---
    # Instead of letting KeyboardInterrupt nondeterministically interrupt a
    # coroutine mid-flight, we catch the signal, cancel all running tasks, and
    # let each one unwind through its normal exception handling path.
    shutdown_event = asyncio.Event()

    def _request_shutdown():
        logging.info("[bot] Shutdown signal received — stopping gracefully...")
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _request_shutdown)

    watcher_task = asyncio.create_task(watch_extensions(), name="watch_extensions")

    async with client:
        bot_task = asyncio.create_task(client.start(TOKEN), name="client_start")

        # Wait until either a shutdown signal fires or the bot task ends on its own.
        done, pending = await asyncio.wait(
            [bot_task, watcher_task, asyncio.create_task(shutdown_event.wait(), name="shutdown_sentinel")],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Cancel everything that's still running.
        for task in pending:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        logging.info("[bot] All tasks stopped. Goodbye.")


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
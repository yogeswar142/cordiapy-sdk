# Cordia Python SDK

Official analytics SDK for Discord bots using Cordia.

## Installation

```bash
pip install cordia
```

## Basic Usage

```python
import discord
from discord.ext import commands
import cordia
import os

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Initialize cordia client
cordia_client = cordia.CordiaClient(
    api_key=os.getenv("CORDIA_API_KEY"),
    bot_id=os.getenv("CORDIA_BOT_ID")
)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    # Report guild count asynchronously
    await cordia_client.post_guild_count(len(bot.guilds))
    
    # Start the async background heartbeat and queue processor
    cordia_client.start(bot.loop)

@bot.command()
async def ping(ctx):
    await ctx.send("Pong!")
    
    # Track command
    await cordia_client.track_command(
        command="ping",
        user_id=str(ctx.author.id),
        guild_id=str(ctx.guild.id) if ctx.guild else None
    )

bot.run(os.getenv("DISCORD_TOKEN"))
```

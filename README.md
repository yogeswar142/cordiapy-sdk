# Cordia

The official Python analytics SDK for Discord bots.

Async-first design built on `aiohttp`. Tracks commands, users, server count, and uptime with automatic batching and background heartbeat.

[![PyPI](https://img.shields.io/pypi/v/cordia)](https://pypi.org/project/cordia/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

## Install

```bash
pip install cordia
```

Requires Python 3.8+.

## Quick Start

```python
import cordia
import os

# pass your discord.py bot/client instance
client = cordia.CordiaClient(
    api_key=os.getenv("CORDIA_API_KEY"),
    bot=bot,  # bot_id auto-detected from bot.user.id
)
```

## Discord.py Example

```python
import discord
from discord.ext import commands
import cordia
import os

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

cordia_client = cordia.CordiaClient(
    api_key=os.getenv("CORDIA_API_KEY"),
    bot=bot,  # bot_id + shard metadata auto-detected
)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    await cordia_client.post_guild_count(len(bot.guilds))
    cordia_client.start(bot.loop)

@bot.command()
async def ping(ctx):
    await ctx.send("Pong!")
    await cordia_client.track_command(
        command="ping",
        user_id=str(ctx.author.id),
        guild_id=str(ctx.guild.id) if ctx.guild else None,
    )

bot.run(os.getenv("DISCORD_TOKEN"))
```

## Configuration

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `api_key` | `str` | required | API key from the Cordia dashboard |
| `bot` | `discord.Client` | recommended | Discord.py bot/client for bot_id + shard auto-detection |
| `bot_id` | `str` | optional | Manual bot ID override (rarely needed) |
| `heartbeat_interval` | `int` | `30000` | Heartbeat interval (ms) |
| `auto_heartbeat` | `bool` | `True` | Start heartbeat on `start()` |
| `batch_size` | `int` | `10` | Events before auto-flush |
| `flush_interval` | `int` | `5000` | Auto-flush interval (ms) |
| `debug` | `bool` | `False` | Enable debug logging |

## API

| Method | Description |
|--------|-------------|
| `start(loop)` | Start background tasks (flush + heartbeat) |
| `track_command(...)` | Queue a command event |
| `track_user(...)` | Queue a user activity event |
| `post_guild_count(count)` | Report server count (immediate) |
| `start_heartbeat(loop)` | Start heartbeat manually |
| `stop_heartbeat()` | Stop heartbeat |
| `get_uptime()` | Returns uptime in ms |
| `flush()` | Force-flush queued events |
| `close()` | Stop tasks, flush, and close session |

## Documentation

Full guides and API reference at [docs.cordialane.com](https://docs.cordialane.com).

## License

MIT

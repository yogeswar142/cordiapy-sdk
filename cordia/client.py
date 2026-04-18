import aiohttp
import asyncio
import time
import logging
from typing import List, Dict, Any, Optional
from .types import CordiaConfig

logger = logging.getLogger('cordia')

class CordiaClient:
    def __init__(self, api_key: str, bot_id: str, base_url: str = 'https://cordlane-brain.onrender.com/api/v1', debug: bool = False):
        self.config = CordiaConfig(api_key=api_key, bot_id=bot_id, base_url=base_url, debug=debug)
        self.headers = {
            'Authorization': f'Bearer {self.config.api_key}',
            'X-Bot-ID': self.config.bot_id,
            'Content-Type': 'application/json'
        }
        self.queue: List[Dict[str, Any]] = []
        self._session: Optional[aiohttp.ClientSession] = None
        self._tasks: List[asyncio.Task] = []
        self._start_time = time.time()
        self._running = False
        
        if debug:
            logger.setLevel(logging.DEBUG)
        
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers=self.headers)
        return self._session

    def start(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        """Start background tasks for flushing queue and heartbeat"""
        if self._running:
            return
            
        self._running = True
        loop = loop or asyncio.get_event_loop()
        self._tasks.append(loop.create_task(self._flush_loop()))
        
        if self.config.auto_heartbeat:
            self.start_heartbeat(loop)

    def start_heartbeat(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        """Manually start the heartbeat system."""
        if any(t.get_name() == 'cordia_heartbeat' for t in self._tasks):
            return
            
        loop = loop or asyncio.get_event_loop()
        task = loop.create_task(self._heartbeat_loop())
        task.set_name('cordia_heartbeat')
        self._tasks.append(task)

    def stop_heartbeat(self) -> None:
        """Stop the heartbeat system."""
        for task in self._tasks:
            if task.get_name() == 'cordia_heartbeat':
                task.cancel()
                self._tasks.remove(task)
                break

    @property
    def is_heartbeat_running(self) -> bool:
        return any(t.get_name() == 'cordia_heartbeat' for t in self._tasks)

    async def _flush_loop(self):
        while self._running:
            await asyncio.sleep(self.config.flush_interval / 1000.0)
            await self.flush()

    async def _heartbeat_loop(self):
        while True:
            await self._send_heartbeat()
            await asyncio.sleep(self.config.heartbeat_interval / 1000.0)

    async def _send_heartbeat(self):
        session = await self._get_session()
        payload = {
            'botId': self.config.bot_id,
            'uptime': self.get_uptime(),
            'timestamp': self._now()
        }
        try:
            async with session.post(f"{self.config.base_url}/heartbeat", json=payload) as resp:
                if resp.status >= 400:
                    logger.debug(f"Heartbeat failed: {resp.status}")
        except Exception as e:
            logger.debug(f"Heartbeat exception: {e}")

    def _now(self) -> str:
        return time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())

    async def post_guild_count(self, count: int):
        session = await self._get_session()
        payload = {
            'botId': self.config.bot_id,
            'count': count,
            'timestamp': self._now()
        }
        try:
            async with session.post(f"{self.config.base_url}/guild-count", json=payload) as resp:
                if self.config.debug:
                    logger.debug(f"Posted guild count: {count}, status: {resp.status}")
        except Exception as e:
            logger.debug(f"Failed to post guild count: {e}")

    async def track_command(self, command: str, user_id: Optional[str] = None, guild_id: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None):
        self.queue.append({
            'endpoint': '/track-command',
            'payload': {
                'botId': self.config.bot_id,
                'event': 'command_used',
                'command': command,
                'userId': user_id,
                'guildId': guild_id,
                'metadata': metadata,
                'timestamp': self._now()
            }
        })
        if len(self.queue) >= self.config.batch_size:
            await self.flush()

    async def track_user(self, user_id: str, action: str = 'interaction', guild_id: Optional[str] = None):
        self.queue.append({
            'endpoint': '/track-user',
            'payload': {
                'botId': self.config.bot_id,
                'event': 'user_active',
                'userId': user_id,
                'action': action,
                'guildId': guild_id,
                'timestamp': self._now()
            }
        })
        if len(self.queue) >= self.config.batch_size:
            await self.flush()

    def get_uptime(self) -> int:
        return int((time.time() - self._start_time) * 1000)

    async def flush(self):
        if not self.queue:
            return
            
        events_to_send = self.queue[:]
        self.queue = []
        
        session = await self._get_session()
        
        for event in events_to_send:
            try:
                async with session.post(f"{self.config.base_url}{event['endpoint']}", json=event['payload']) as resp:
                    if self.config.debug:
                        logger.debug(f"Flushed event to {event['endpoint']}, status: {resp.status}")
            except Exception as e:
                logger.debug(f"Failed to flush event: {e}")
                # Don't re-queue for now to match JS behavior of "fire and forget" on flush failure
                # but in production we might want to retry

    async def close(self):
        self._running = False
        self.stop_heartbeat()
        for task in self._tasks:
            if not task.done():
                task.cancel()
        await self.flush()
        if self._session and not self._session.closed:
            await self._session.close()

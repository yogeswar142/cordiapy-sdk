import aiohttp
import asyncio
import time
import logging
import signal
import os
from typing import List, Dict, Any, Optional
from .schema import CordiaConfig

logger = logging.getLogger('cordia')

class CordiaClient:
    def __init__(self, api_key: str, bot_id: str, base_url: Optional[str] = None, debug: bool = False, heartbeat_interval: int = 30000, batch_size: int = 10, flush_interval: int = 60000, auto_scale: bool = False):
        # Resolve base_url: Arg > Env Var > Default
        if base_url is None:
            base_url = os.getenv("CORDIA_API_URL", "https://cordlane-brain.onrender.com/api/v1")
            
        if heartbeat_interval < 30000:
             heartbeat_interval = 30000
             logger.warning("heartbeat_interval cannot be less than 30000ms. Setting to 30000ms.")
             
        if flush_interval < 60000:
             flush_interval = 60000
             logger.warning("flush_interval cannot be less than 60000ms. Setting to 60000ms.")

        self.config = CordiaConfig(api_key=api_key, bot_id=bot_id, base_url=base_url, debug=debug, heartbeat_interval=heartbeat_interval, batch_size=batch_size, flush_interval=flush_interval, auto_scale=auto_scale)
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
        self._tasks.append(loop.create_task(self._verify_credentials()))
        
        if self.config.auto_heartbeat:
            self.start_heartbeat(loop)
            
        # Register graceful shutdown handlers
        try:
            loop.add_signal_handler(signal.SIGINT, lambda: asyncio.create_task(self.close()))
            loop.add_signal_handler(signal.SIGTERM, lambda: asyncio.create_task(self.close()))
        except NotImplementedError:
            pass # Windows doesn't fully support loop.add_signal_handler

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
                elif self.config.debug:
                    logger.debug("Heartbeat sent successfully.")
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
            
        # Enforce backpressure limit
        if len(self.queue) > 1000:
            if self.config.debug:
                logger.debug(f"Queue exceeded 1000 items, dropping oldest {len(self.queue) - 1000} items.")
            self.queue = self.queue[-1000:]
            
        events_to_send = self.queue[:]
        self.queue = []
        
        session = await self._get_session()
        
        # Extract payload from queued objects
        batch_payloads = [event['payload'] for event in events_to_send]
        
        try:
            async with session.post(f"{self.config.base_url}/track-batch", json={"botId": self.config.bot_id, "events": batch_payloads}) as resp:
                if resp.status == 429:
                    if self.config.auto_scale:
                        self.config.batch_size = int(self.config.batch_size * 1.5) + 10
                        self.config.flush_interval += 10000
                        logger.warning(f"Rate limited! Auto-scaled batch_size to {self.config.batch_size} and flush_interval to {self.config.flush_interval}ms")
                    else:
                        logger.warning("Rate limit exceeded. Consider increasing batch_size or enabling auto_scale.")
                    self.queue = events_to_send + self.queue # Re-queue
                elif resp.status >= 400:
                    response_text = await resp.text()
                    logger.debug(f"Batch flush failed ({resp.status}): {response_text}")
                elif self.config.debug:
                    logger.debug(f"Flushed {len(batch_payloads)} events to /track-batch, status: {resp.status}")
        except Exception as e:
            if self.config.debug:
                logger.debug(f"Failed to flush events: {e}")
            # Re-queue on failure for backpressure retry
            self.queue = events_to_send + self.queue

    async def close(self):
        self._running = False
        self.stop_heartbeat()
        for task in self._tasks:
            if not task.done():
                task.cancel()
        await self.flush()
        if self._session and not self._session.closed:
            await self._session.close()

    async def _verify_credentials(self):
        """Verify API credentials and disable SDK if they are invalid (401/404)."""
        session = await self._get_session()
        try:
            async with session.get(f"{self.config.base_url}/auth/verify") as resp:
                if resp.status == 200:
                    logger.info(f"Cordia SDK verified successfully for bot: {self.config.bot_id}")
                elif resp.status in (401, 404):
                    error_data = await resp.json()
                    error_msg = error_data.get('error', 'Invalid API Key')
                    print(f"\n\n🚨 CORDIA SDK DISABLED: {error_msg}")
                    print("Please check your API key and Bot ID in the Cordia dashboard.\n")
                    logger.error(f"Cordia SDK Disabled: {error_msg}")
                    
                    # Disable the SDK
                    self._running = False
                    self.stop_heartbeat()
                    for task in self._tasks:
                        if not task.done() and task.get_name() != asyncio.current_task().get_name():
                            task.cancel()
                    await self.flush()
                else:
                    logger.warning(f"Cordia verification skipped (Status: {resp.status}). The SDK will continue to attempt tracking.")
        except Exception as e:
            logger.debug(f"Verification exception: {e}")

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
    def __init__(
        self,
        api_key: str,
        bot_id: Optional[str] = None,
        debug: bool = False,
        heartbeat_interval: int = 30000,
        batch_size: int = 10,
        flush_interval: int = 60000,
        auto_scale: bool = False,
        bot: Optional[Any] = None,
        shard_id: int = 0,
        total_shards: int = 1,
    ):
        # API production URL
        base_url = "https://api.cordialane.com/api/v1"
            
        if heartbeat_interval < 30000:
             heartbeat_interval = 30000
             
        if flush_interval < 60000:
             flush_interval = 60000

        self.config = CordiaConfig(
            api_key=api_key,
            bot_id=bot_id,
            base_url=base_url,
            debug=debug,
            heartbeat_interval=heartbeat_interval,
            batch_size=batch_size,
            flush_interval=flush_interval,
            auto_scale=auto_scale,
            shard_id=shard_id,
            total_shards=total_shards,
        )
        self._discord_client = bot
        self.headers = {
            'Authorization': f'Bearer {self.config.api_key}',
            'Content-Type': 'application/json',
            'X-Cordia-Sdk-Version': '1.2.0',
            'User-Agent': 'cordia-py/1.2.0'
        }
        self.queue: List[Dict[str, Any]] = []
        self._session: Optional[aiohttp.ClientSession] = None
        self._tasks: List[asyncio.Task] = []
        self._start_time = time.time()
        self._running = False
        self._logged_shard_detection = False
        self._warned_missing_shard = False
        
        if debug:
            logger.setLevel(logging.DEBUG)
        
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers={
                'Authorization': f'Bearer {self.config.api_key}',
                'Content-Type': 'application/json',
                'X-Cordia-Sdk-Version': '1.2.1',
                'User-Agent': 'cordia-py/1.2.1'
            })
        return self._session

    def _resolve_bot_id(self) -> Optional[str]:
        if self.config.bot_id:
            return str(self.config.bot_id)
        user = getattr(self._discord_client, "user", None)
        runtime_bot_id = getattr(user, "id", None)
        if runtime_bot_id is not None:
            self.config.bot_id = str(runtime_bot_id) # Persist detected ID
            return self.config.bot_id
        return None

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
        bot_id = self._resolve_bot_id()
        if not bot_id:
            logger.debug("Cordia bot_id unavailable. Skipping heartbeat until bot is ready.")
            return
        shard_meta = self._resolve_shard_meta()
        self._log_shard_detection_once(shard_meta)
        payload = {
            'botId': bot_id,
            'uptime': self.get_uptime(),
            'timestamp': self._now(),
            **shard_meta,
        }
        try:
            async with session.post(f"{self.config.base_url}/heartbeat", json=payload, headers={'X-Bot-ID': bot_id}) as resp:
                if resp.status >= 400:
                    logger.debug(f"Heartbeat failed: {resp.status}")
                elif self.config.debug:
                    logger.debug("Heartbeat sent successfully.")
        except Exception as e:
            logger.debug(f"Heartbeat exception: {e}")

    def _now(self) -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

    async def post_guild_count(self, count: int, shard_id: Optional[int] = None, total_shards: Optional[int] = None):
        session = await self._get_session()
        bot_id = self._resolve_bot_id()
        if not bot_id:
            logger.debug("Cordia bot_id unavailable. Skipping guild count until bot is ready.")
            return
        shard_meta = self._resolve_shard_meta(shard_id=shard_id, total_shards=total_shards)
        payload = {
            'botId': bot_id,
            'count': count,
            'timestamp': self._now(),
            **shard_meta,
        }
        try:
            async with session.post(f"{self.config.base_url}/guild-count", json=payload, headers={'X-Bot-ID': bot_id}) as resp:
                if self.config.debug:
                    logger.debug(f"Posted guild count: {count}, status: {resp.status}")
        except Exception as e:
            logger.debug(f"Failed to post guild count: {e}")

    async def track_command(
        self,
        command: str,
        user_id: Optional[str] = None,
        guild_id: Optional[str] = None,
        shard_id: Optional[int] = None,
        total_shards: Optional[int] = None,
    ):
        bot_id = self._resolve_bot_id()
        if not bot_id:
            logger.debug("Cordia bot_id unavailable. Skipping command tracking until bot is ready.")
            return
        shard_meta = self._resolve_shard_meta(shard_id=shard_id, total_shards=total_shards)
        self.queue.append({
            'endpoint': '/track-command',
            'payload': {
                'botId': bot_id,
                'event': 'command_used',
                'command': command,
                'userId': user_id,
                'guildId': guild_id,
                'timestamp': self._now(),
                **shard_meta,
            }
        })
        if len(self.queue) >= self.config.batch_size:
            # ADAPTIVE LOGIC: Speed up if busy
            if self.current_flush_interval > 15000:
                self.current_flush_interval = max(15000, self.current_flush_interval - 5000)
            await self.flush()

    async def track_user(
        self,
        user_id: str,
        action: str = 'interaction',
        guild_id: Optional[str] = None,
        shard_id: Optional[int] = None,
        total_shards: Optional[int] = None,
    ):
        bot_id = self._resolve_bot_id()
        if not bot_id:
            logger.debug("Cordia bot_id unavailable. Skipping user tracking until bot is ready.")
            return
        shard_meta = self._resolve_shard_meta(shard_id=shard_id, total_shards=total_shards)
        self.queue.append({
            'endpoint': '/track-user',
            'payload': {
                'botId': bot_id,
                'event': 'user_active',
                'userId': user_id,
                'action': action,
                'guildId': guild_id,
                'timestamp': self._now(),
                **shard_meta,
            }
        })
        if len(self.queue) >= self.config.batch_size:
            # ADAPTIVE LOGIC: Speed up if busy
            if self.current_flush_interval > 15000:
                self.current_flush_interval = max(15000, self.current_flush_interval - 5000)
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
            
        # Hard limit of 500 events per batch to stay under API validation limits
        batch_limit = 500
        events_to_send = self.queue[:batch_limit]
        self.queue = self.queue[batch_limit:]
        
        session = await self._get_session()
        
        # Extract payload from queued objects
        batch_payloads = [event['payload'] for event in events_to_send]
        bot_id = self._resolve_bot_id()
        if not bot_id:
            self.queue = events_to_send + self.queue
            return

        # Batch optimization: if all events share the same shard meta, hoist it to the root
        root_shard_id = None
        root_total_shards = None
        all_same_shard = True
        for p in batch_payloads:
            sid = p.get("shardId")
            tss = p.get("totalShards")
            if not isinstance(sid, int) or not isinstance(tss, int) or tss <= 0:
                all_same_shard = False
                break
            if root_shard_id is None and root_total_shards is None:
                root_shard_id, root_total_shards = sid, tss
                continue
            if root_shard_id != sid or root_total_shards != tss:
                all_same_shard = False
                break

        if all_same_shard and root_shard_id is not None and root_total_shards is not None:
            cleaned = []
            for p in batch_payloads:
                cp = dict(p)
                cp.pop("shardId", None)
                cp.pop("totalShards", None)
                cleaned.append(cp)
            batch_payloads_to_send = cleaned
        else:
            batch_payloads_to_send = batch_payloads
        
        try:
            async with session.post(
                f"{self.config.base_url}/track-batch",
                json={
                    "botId": bot_id,
                    "shardId": root_shard_id if all_same_shard else None,
                    "totalShards": root_total_shards if all_same_shard else None,
                    "events": batch_payloads_to_send
                },
                headers={'X-Bot-ID': bot_id}
            ) as resp:
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
                    logger.debug(f"Flushed {len(batch_payloads_to_send)} events to /track-batch, status: {resp.status}")
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
        bot_id = self._resolve_bot_id()
        if not bot_id:
            logger.warning("Cordia verification skipped: bot_id unavailable until client is ready.")
            return
        try:
            async with session.get(f"{self.config.base_url}/auth/verify", headers={'X-Bot-ID': bot_id}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    logger.info(f"Cordia SDK verified successfully for bot: {bot_id}")
                    
                    # VERSION HANDSHAKE
                    versioning = data.get('versioning')
                    current_version = '1.2.1'
                    if versioning:
                        latest = versioning.get('latestSdkVersion')
                        if latest and latest != current_version:
                            print(f"\n[Cordia] 🆕 A new Python SDK version is available: {latest} (You are on {current_version})")
                            print(f"[Cordia] Update to stay optimized: pip install cordia --upgrade\n")
                            
                elif resp.status in (401, 404):
                    error_data = await resp.json()
                    error_msg = error_data.get('error', 'Invalid API Key')
                    print(f"\n🚨 CORDIA SDK DISABLED: {error_msg}")
                    print("Please check your API key and bot identity in the Cordia dashboard.\n")
                    
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

    def _resolve_shard_meta(self, shard_id: Optional[int] = None, total_shards: Optional[int] = None) -> Dict[str, int]:
        detected_shard_id = getattr(self._discord_client, "shard_id", None)
        detected_total_shards = getattr(self._discord_client, "shard_count", None)

        # AutoSharded bots may expose a `shards` mapping (e.g. discord.py AutoShardedClient)
        shards = getattr(self._discord_client, "shards", None)
        shard_keys = None
        try:
            if shards is not None:
                shard_keys = list(getattr(shards, "keys", lambda: [])())
        except Exception:
            shard_keys = None

        if not isinstance(detected_shard_id, int) and shard_keys and all(isinstance(k, int) for k in shard_keys):
            # If only one shard is active in this process, we can safely use it.
            if len(shard_keys) == 1:
                detected_shard_id = shard_keys[0]
            else:
                # Multiple shards in one process: default to the lowest id unless caller provides shard_id.
                detected_shard_id = min(shard_keys)

        if (not isinstance(detected_total_shards, int) or detected_total_shards <= 0) and shard_keys and all(isinstance(k, int) for k in shard_keys):
            detected_total_shards = len(shard_keys)

        resolved_shard_id = shard_id if isinstance(shard_id, int) else (
            detected_shard_id if isinstance(detected_shard_id, int) else self.config.shard_id
        )
        resolved_total_shards = total_shards if isinstance(total_shards, int) and total_shards > 0 else (
            detected_total_shards if isinstance(detected_total_shards, int) and detected_total_shards > 0 else self.config.total_shards
        )

        # Debug warning for "ready state" issues (client passed but shard info not ready)
        if self.config.debug and self._discord_client is not None and not self._warned_missing_shard:
            client_has_no_shard_info = (
                not isinstance(getattr(self._discord_client, "shard_id", None), int)
                and not isinstance(getattr(self._discord_client, "shard_count", None), int)
                and not shard_keys
            )
            using_fallback = resolved_shard_id == self.config.shard_id and resolved_total_shards == self.config.total_shards
            if client_has_no_shard_info and using_fallback:
                self._warned_missing_shard = True
                logger.warning(
                    "Discord client provided but shard info is not available yet. "
                    "Cordia will keep resolving shard meta lazily, but if you see shardId=0/totalShards=1 unexpectedly, "
                    "initialize Cordia after the bot is ready or pass shard_id/total_shards overrides."
                )

        return {
            "shardId": resolved_shard_id,
            "totalShards": resolved_total_shards,
        }

    def _log_shard_detection_once(self, shard_meta: Dict[str, int]) -> None:
        if self._logged_shard_detection:
            return
        self._logged_shard_detection = True
        if self.config.debug:
            logger.info(
                f"Detected shard meta: shardId={shard_meta.get('shardId')}, totalShards={shard_meta.get('totalShards')}"
            )

from typing import Optional, Dict, Any
from dataclasses import dataclass

@dataclass
class CordiaConfig:
    api_key: str
    bot_id: Optional[str] = None
    base_url: str = 'https://api.cordialane.com/api/v1'
    debug: bool = False
    heartbeat_interval: int = 30000
    batch_size: int = 10
    flush_interval: int = 60000
    auto_heartbeat: bool = True
    auto_scale: bool = False
    shard_id: int = 0
    total_shards: int = 1

@dataclass
class TrackCommandPayload:
    command: str
    user_id: Optional[str] = None
    guild_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    shard_id: Optional[int] = None
    total_shards: Optional[int] = None

@dataclass
class TrackUserPayload:
    user_id: str
    action: str = 'interaction'
    guild_id: Optional[str] = None
    shard_id: Optional[int] = None
    total_shards: Optional[int] = None

from typing import Optional, Dict, Any
from dataclasses import dataclass

@dataclass
class CordiaConfig:
    api_key: str
    bot_id: str
    base_url: str = 'https://api.cordialane.com/api/v1'
    debug: bool = False
    heartbeat_interval: int = 30000
    batch_size: int = 10
    flush_interval: int = 5000
    auto_heartbeat: bool = True

@dataclass
class TrackCommandPayload:
    command: str
    user_id: Optional[str] = None
    guild_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

@dataclass
class TrackUserPayload:
    user_id: str
    action: str = 'interaction'
    guild_id: Optional[str] = None

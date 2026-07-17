"""Pydantic config loader for config.yaml (specs.md §6).

Strict-mode Pydantic models mirror ``config.example.yaml``. Unknown keys,
missing required keys, or type errors cause the process to exit before any
external call is made (§6.1). The loader also computes ``config_hash`` — a
sha256 of the raw YAML bytes — for the runs audit log (§5).
"""

import hashlib
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IdentityConfig(_Strict):
    user_name: str
    timezone: str
    delivery_address: str
    run_time: str = "08:00"


class GmailConfig(_Strict):
    allowlist: list[str] = Field(default_factory=list)
    trusted_domains: list[str] = Field(default_factory=list)
    fetch_window_hours: int = 24
    max_body_chars: int = 2000
    max_unknown_sender_llm_calls: int = 20


class PlaneConfig(_Strict):
    base_url: str = "https://api.plane.so"
    project_ids: list[str]
    ignore_list: list[str] = Field(default_factory=list)
    rolling_window_days: int = 7


class ThresholdsConfig(_Strict):
    inactivity_days: int = 4
    overdue_grace_days: int = 0
    load_multiplier: float = 1.5
    due_soon_horizon_days: int = 7


class LLMConfig(_Strict):
    model: str = "claude-sonnet-4-6"
    max_tokens_per_node: int = 4096
    batch_mode: bool = True


class Config(_Strict):
    identity: IdentityConfig
    gmail: GmailConfig
    plane: PlaneConfig
    thresholds: ThresholdsConfig
    llm: LLMConfig

    config_hash: str


def load_config(path: Path) -> Config:
    raw_bytes = path.read_bytes()
    data = yaml.safe_load(raw_bytes) or {}
    data["config_hash"] = hashlib.sha256(raw_bytes).hexdigest()

    load_dotenv()  # long-lived tokens land as env vars; see SECURITY.md §2.1

    return Config.model_validate(data)

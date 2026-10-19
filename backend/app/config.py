"""全局配置：全部走环境变量，方便 Docker / 本地 / CI 三种环境无差别运行。"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "单易分 · 智能工单分拣台"
    db_path: str = "data/tickets.db"

    # auto: 有 key 就用大模型，没有就自动降级到规则引擎（保证 demo 永远能跑）
    analyzer_mode: Literal["auto", "llm", "rule"] = "auto"

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    llm_timeout: float = 30.0
    llm_temperature: float = 0.0

    # 低于该置信度自动转人工
    confidence_threshold: float = 0.62
    # 多标签场景下，如果有两个意图得分差小于该值，视为"意图打架"，也转人工
    margin_threshold: float = 0.18

    cors_origins: str = "*"

    @property
    def llm_enabled(self) -> bool:
        if self.analyzer_mode == "rule":
            return False
        if self.analyzer_mode == "llm":
            return bool(self.deepseek_api_key)
        return bool(self.deepseek_api_key)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

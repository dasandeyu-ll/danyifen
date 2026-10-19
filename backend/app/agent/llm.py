"""DeepSeek 结构化输出客户端。

三个工程细节，是这类"把 LLM 塞进业务系统"的项目里最容易翻车的地方：

1. **JSON 模式 + Pydantic 二次校验**
   大模型永远可能给你多一个字段、少一个字段、或者把数字写成字符串。
   所以拿到 JSON 后必须用 Pydantic 兜一遍，失败就当这次调用不成功，
   走降级路径。绝不让 LLM 的脏数据污染领域模型。

2. **超时 + 重试 + 统计**
   网络抖动是常态。带指数退避重试一次；同时把 token 用量记下来，
   否则月底账单会很惊喜。

3. **失败必须返回 None 而不是抛异常**
   调用方（LangGraph 节点）拿到 None 就走规则引擎。
   这样"LLM 挂了"退化成一个降级事件，而不是一个 500 错误。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from ..config import get_settings
from ..schemas import Intent
from .prompts import CLASSIFY_SYSTEM, PROMPT_VERSION, build_extract_system

logger = logging.getLogger("agent.llm")

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


# --------------------------------------------------------------------------- #
# LLM 原始输出的校验模型（和领域模型分开，避免 LLM 直接构造领域对象）
# --------------------------------------------------------------------------- #
class LLMIntentScore(BaseModel):
    name: str
    score: float = Field(ge=0.0, le=1.0)


class LLMSegmentResult(BaseModel):
    index: int
    intents: list[LLMIntentScore]


class LLMClassifyEnvelope(BaseModel):
    results: list[LLMSegmentResult]


class LLMSlot(BaseModel):
    value: Any
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    source: str = "text"


class LLMExtractEnvelope(BaseModel):
    intent: str | None = None
    slots: dict[str, LLMSlot] = Field(default_factory=dict)


@dataclass
class Usage:
    calls: int = 0
    failures: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    last_error: str | None = None
    engines_used: dict[str, int] = field(default_factory=dict)

    def record(self, engine: str) -> None:
        self.engines_used[engine] = self.engines_used.get(engine, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "failures": self.failures,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "last_error": self.last_error,
            "prompt_version": PROMPT_VERSION,
        }


USAGE = Usage()


def _strip_fence(raw: str) -> str:
    raw = _JSON_FENCE.sub("", raw.strip())
    start = raw.find("{")
    end = raw.rfind("}")
    return raw[start : end + 1] if start >= 0 and end > start else raw


def _coerce_intent(name: str) -> Intent | None:
    name = (name or "").strip()
    for intent in Intent:
        if intent.value == name:
            return intent
    aliases = {
        "退款": Intent.RETURN,
        "退货": Intent.RETURN,
        "退换货": Intent.RETURN,
        "物流": Intent.LOGISTICS,
        "快递查询": Intent.LOGISTICS,
        "开票": Intent.INVOICE,
        "催发货": Intent.URGE,
        "催促": Intent.URGE,
        "意见反馈": Intent.SUGGESTION,
        "表扬": Intent.SUGGESTION,
        "query": Intent.CONSULT,
    }
    return aliases.get(name)


class DeepSeekAnalyzer:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._client: Any | None = None

    @property
    def enabled(self) -> bool:
        return self.settings.llm_enabled

    @property
    def client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=self.settings.deepseek_api_key,
                base_url=self.settings.deepseek_base_url,
                timeout=self.settings.llm_timeout,
                max_retries=0,  # 重试逻辑我们自己做，方便统计
            )
        return self._client

    async def _chat_json(self, system: str, user: str, *, max_tokens: int = 800) -> dict[str, Any] | None:
        if not self.enabled:
            return None

        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                resp = await self.client.chat.completions.create(
                    model=self.settings.deepseek_model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    response_format={"type": "json_object"},
                    temperature=self.settings.llm_temperature,
                    max_tokens=max_tokens,
                )
                USAGE.calls += 1
                if resp.usage:
                    USAGE.prompt_tokens += resp.usage.prompt_tokens
                    USAGE.completion_tokens += resp.usage.completion_tokens
                return json.loads(_strip_fence(resp.choices[0].message.content or "{}"))
            except Exception as exc:  # noqa: BLE001 - 任何 LLM 异常都必须降级
                last_exc = exc
                USAGE.failures += 1
                USAGE.last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("DeepSeek 调用失败 (attempt %s): %s", attempt + 1, exc)
                if attempt == 0:
                    await asyncio.sleep(0.6)

        if last_exc:
            USAGE.last_error = f"{type(last_exc).__name__}: {last_exc}"
        return None

    # ------------------------------------------------------------------ #
    # 阶段一：多标签意图分类（一次请求处理所有片段，省 token 又保证上下文一致）
    # ------------------------------------------------------------------ #
    async def classify(self, segments: list[str]) -> list[list[tuple[Intent, float]] | None]:
        numbered = "\n".join(f"[{i}] {s}" for i, s in enumerate(segments))
        user = (
            "请对下面每一条留言片段做意图分类。\n"
            f"片段列表：\n{numbered}\n\n"
            "返回一个 JSON 对象，包含 results 数组，每个元素对应一个 index。"
        )
        data = await self._chat_json(CLASSIFY_SYSTEM, user, max_tokens=700)
        if not data:
            return [None] * len(segments)

        try:
            envelope = LLMClassifyEnvelope.model_validate(data)
        except ValidationError as exc:
            USAGE.last_error = f"classify schema mismatch: {exc.errors()[:1]}"
            logger.warning("分类结果不符合 schema，降级到规则引擎")
            return [None] * len(segments)

        out: list[list[tuple[Intent, float]] | None] = [None] * len(segments)
        for item in envelope.results:
            if not (0 <= item.index < len(segments)):
                continue
            pairs: list[tuple[Intent, float]] = []
            for s in sorted(item.intents, key=lambda x: -x.score):
                intent = _coerce_intent(s.name)
                if intent and all(intent != p[0] for p in pairs):
                    pairs.append((intent, s.score))
            out[item.index] = pairs or None
        return out

    # ------------------------------------------------------------------ #
    # 阶段二：槽位抽取（结构化输出）
    # ------------------------------------------------------------------ #
    async def extract(self, segment: str, intent: Intent) -> dict[str, dict[str, Any]] | None:
        user = (
            f"片段：{segment}\n"
            f"已判定意图：{intent.value}\n"
            "请抽取该意图需要的槽位，返回 JSON 对象（含 intent 和 slots 两个字段）。"
        )
        data = await self._chat_json(build_extract_system(), user, max_tokens=500)
        if not data:
            return None
        try:
            envelope = LLMExtractEnvelope.model_validate(data)
        except ValidationError as exc:
            USAGE.last_error = f"extract schema mismatch: {exc.errors()[:1]}"
            logger.warning("槽位结果不符合 schema，降级到规则引擎")
            return None

        cleaned: dict[str, dict[str, Any]] = {}
        for name, slot in envelope.slots.items():
            if slot.value in (None, "", "null", "N/A", "无", "-"):
                continue
            cleaned[name.strip()] = {
                "value": str(slot.value).strip(),
                "confidence": slot.confidence,
                "source": "inferred" if slot.source == "inferred" else "text",
            }
        return cleaned


analyzer = DeepSeekAnalyzer()

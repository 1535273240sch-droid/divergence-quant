"""M10 新闻证据链类型。

该文件是 AI Agent 的无副作用数据边界。正式数据层可在不改变这些类型的前提下，把
M02 的来源适配器接入 ``src/dquant/data/news_collector.py``。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import urlparse


class NewsValidationError(ValueError):
    """新闻记录不满足证据链要求。"""


@dataclass(frozen=True, slots=True)
class NewsSource:
    """新闻来源登记信息。"""

    name: str
    url: str
    official: bool = False
    default_credibility: float = 0.5

    def __post_init__(self) -> None:
        _validate_url(self.url)
        if not self.name.strip():
            raise NewsValidationError("source name must not be empty")
        if not 0 <= self.default_credibility <= 1:
            raise NewsValidationError("source credibility must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class NewsItem:
    """已经抓取并带有来源元数据的单条新闻。"""

    item_id: str
    title: str
    source_name: str
    url: str
    published_ts_ms: int
    fetched_ts_ms: int
    credibility: float
    official: bool = False
    inst_id: str | None = None
    event_type: str | None = None

    def __post_init__(self) -> None:
        if not self.item_id.strip() or not self.title.strip() or not self.source_name.strip():
            raise NewsValidationError("news id, title and source name must not be empty")
        _validate_url(self.url)
        if self.published_ts_ms < 0 or self.fetched_ts_ms < 0:
            raise NewsValidationError("news timestamps must not be negative")
        if self.published_ts_ms > self.fetched_ts_ms:
            raise NewsValidationError("published timestamp cannot be after fetched timestamp")
        if not 0 <= self.credibility <= 1:
            raise NewsValidationError("news credibility must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class EvidenceChain:
    """一个候选风险事件对应的可审计证据链。"""

    items: tuple[NewsItem, ...]
    official_count: int
    independent_sources: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    can_veto: bool


class NewsCollector:
    """标准化新闻并构建 VETO 证据链。"""

    def __init__(
        self,
        sources: Sequence[NewsSource] = (),
        *,
        min_credibility: float = 0.6,
    ) -> None:
        if not 0 <= min_credibility <= 1:
            raise NewsValidationError("min_credibility must be between 0 and 1")
        self._sources = {source.name: source for source in sources}
        self.min_credibility = min_credibility

    def normalize(self, raw_items: Iterable[Mapping[str, object]]) -> tuple[NewsItem, ...]:
        """把来源适配器返回的 mapping 转换成统一的 ``NewsItem``。"""

        normalized: list[NewsItem] = []
        for raw in raw_items:
            normalized.append(
                NewsItem(
                    item_id=_required_string(raw, "item_id"),
                    title=_required_string(raw, "title"),
                    source_name=_required_string(raw, "source_name"),
                    url=_required_string(raw, "url"),
                    published_ts_ms=_required_int(raw, "published_ts_ms"),
                    fetched_ts_ms=_required_int(raw, "fetched_ts_ms"),
                    credibility=_float_value(raw.get("credibility", 0.0), "credibility"),
                    official=_official_value(raw),
                    inst_id=_optional_string(raw.get("inst_id")),
                    event_type=_optional_string(raw.get("event_type")),
                )
            )
        return tuple(normalized)

    def recent(
        self,
        items: Iterable[NewsItem],
        *,
        now_ts_ms: int,
        max_age_ms: int,
        inst_id: str | None = None,
    ) -> tuple[NewsItem, ...]:
        """按发布时间过滤窗口内的新闻。"""

        if now_ts_ms < 0 or max_age_ms < 0:
            raise NewsValidationError("time window values must not be negative")
        lower_bound = max(0, now_ts_ms - max_age_ms)
        return tuple(
            item
            for item in items
            if lower_bound <= item.published_ts_ms <= now_ts_ms
            and (inst_id is None or item.inst_id in {None, inst_id})
        )

    def evidence_chain(self, items: Iterable[NewsItem]) -> EvidenceChain:
        """应用官方来源或双独立来源的 VETO 证据门槛。"""

        relevant = tuple(item for item in items if item.credibility >= self.min_credibility)
        official_count = sum(1 for item in relevant if self._is_official(item))
        independent_sources = tuple(sorted({item.source_name for item in relevant}))
        evidence_refs = tuple(item.url for item in relevant)
        return EvidenceChain(
            items=relevant,
            official_count=official_count,
            independent_sources=independent_sources,
            evidence_refs=evidence_refs,
            can_veto=official_count >= 1 or len(independent_sources) >= 2,
        )

    def _is_official(self, item: NewsItem) -> bool:
        source = self._sources.get(item.source_name)
        return item.official or (source is not None and source.official)


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise NewsValidationError("news source URL must be an HTTP(S) URL")


def _required_string(raw: Mapping[str, object], name: str) -> str:
    value = raw.get(name)
    if not isinstance(value, str) or not value.strip():
        raise NewsValidationError(f"{name} must be a non-empty string")
    return value


def _required_int(raw: Mapping[str, object], name: str) -> int:
    value = raw.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise NewsValidationError(f"{name} must be a non-negative integer")
    return value


def _float_value(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NewsValidationError(f"{name} must be numeric")
    result = float(value)
    if not 0 <= result <= 1:
        raise NewsValidationError(f"{name} must be between 0 and 1")
    return result


def _official_value(raw: Mapping[str, object]) -> bool:
    value = raw.get("official", False)
    if not isinstance(value, bool):
        raise NewsValidationError("official must be boolean")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise NewsValidationError("optional news fields must be non-empty strings")
    return value

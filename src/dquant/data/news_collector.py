"""新闻证据链兼容入口。

M10 的 Agent 在 ``dquant.ai.news_collector`` 中使用同一套类型。这里保留计划书规定的
``data/news_collector.py`` 路径，供 M02 的新闻来源适配器和后续整合层导入。
"""

from dquant.ai.news_collector import (
    EvidenceChain,
    NewsCollector,
    NewsItem,
    NewsSource,
    NewsValidationError,
)

__all__ = [
    "EvidenceChain",
    "NewsCollector",
    "NewsItem",
    "NewsSource",
    "NewsValidationError",
]

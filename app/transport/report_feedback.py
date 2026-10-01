"""Validate MCP feedback against search results from the active server process."""

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass


SEARCH_TOOLS = frozenset({"search_semantic", "search_lexical", "search_anchor", "search_meta"})
FEEDBACK_SOURCES = frozenset({"llm", "human", "test"})
FEEDBACK_PURPOSES = frozenset({"task", "evaluation", "test"})
MAX_REPORT_RATINGS = 1000
SEARCH_RECEIPT_TTL_SECONDS = 24 * 60 * 60
MAX_SEARCH_RECEIPTS = 10000


def analysis_group(source, purpose):
    """Separate declared test traffic from candidate task/evaluation feedback."""
    if source == "test" or purpose == "test":
        return "declared_test"
    if source == "llm" and purpose == "task":
        return "declared_llm_task"
    if source == "human" and purpose == "evaluation":
        return "declared_human_evaluation"
    return "unclassified"


class InvalidReport(ValueError):
    def __init__(self, reason: str, detail: str, invalid_ids=()):
        super().__init__(detail)
        self.reason = reason
        self.invalid_ids = list(invalid_ids)[:20]


@dataclass(frozen=True)
class SearchReceipt:
    tool: str
    ids: frozenset[str]
    namespace: str | None
    request_id: str | None
    transport: str | None
    created_mono: float


class SearchReceiptRegistry:
    """Bounded, process-local search receipts used to validate later reports.

    A report after a server restart or after receipt expiry is rejected. This
    keeps validation independent of audit rotation and avoids treating old
    audit events with unknown provenance as trusted search receipts.
    """

    def __init__(self, *, ttl=SEARCH_RECEIPT_TTL_SECONDS, max_receipts=MAX_SEARCH_RECEIPTS):
        self.ttl = ttl
        self.max_receipts = max_receipts
        self._lock = threading.Lock()
        self._receipts = OrderedDict()

    def _prune(self, now):
        while self._receipts:
            first = next(iter(self._receipts.values()))
            if now - first.created_mono < self.ttl:
                break
            self._receipts.popitem(last=False)
        while len(self._receipts) > self.max_receipts:
            self._receipts.popitem(last=False)

    def remember(self, search_id, tool, items, namespace, context):
        if tool not in SEARCH_TOOLS or not items:
            return
        now = time.monotonic()
        receipt = SearchReceipt(
            tool=tool,
            ids=frozenset(item["id"] for item in items),
            namespace=namespace,
            request_id=context.get("request_id"),
            transport=context.get("transport"),
            created_mono=now,
        )
        with self._lock:
            self._prune(now)
            self._receipts[search_id] = receipt
            self._prune(now)

    def validate(self, search_id, ratings, feedback_source=None, feedback_purpose=None):
        if not isinstance(search_id, str) or not search_id:
            raise InvalidReport("search_not_found", "search_id must identify a recent nonempty search")
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            receipt = self._receipts.get(search_id)
        if receipt is None:
            raise InvalidReport("search_not_found", "search_id was not found in this server process or has expired")
        if feedback_source is not None and (not isinstance(feedback_source, str)
                                            or feedback_source not in FEEDBACK_SOURCES):
            raise InvalidReport("invalid_source", "feedback_source must be llm, human, or test")
        if feedback_purpose is not None and (not isinstance(feedback_purpose, str)
                                             or feedback_purpose not in FEEDBACK_PURPOSES):
            raise InvalidReport("invalid_purpose", "feedback_purpose must be task, evaluation, or test")
        if not isinstance(ratings, list) or not 1 <= len(ratings) <= MAX_REPORT_RATINGS:
            raise InvalidReport("invalid_ratings", "ratings must contain 1–1000 entries")
        seen = set()
        invalid_ids = []
        for item in ratings:
            if not isinstance(item, dict):
                raise InvalidReport("invalid_ratings", "each rating must be an object with m_id and rating")
            memory_id = item.get("m_id")
            rating = item.get("rating")
            if not isinstance(memory_id, str) or not memory_id:
                raise InvalidReport("invalid_ratings", "each m_id must be a nonempty string")
            if type(rating) is not int or not -10 <= rating <= 10:
                raise InvalidReport("invalid_ratings", "each rating must be an integer from -10 to 10")
            if memory_id in seen:
                raise InvalidReport("duplicate_memory_id", "each m_id may be rated only once")
            seen.add(memory_id)
            if memory_id not in receipt.ids:
                invalid_ids.append(memory_id)
        if invalid_ids:
            raise InvalidReport("result_id_mismatch", "some m_id values were not returned by this search",
                                invalid_ids)
        return receipt

import json
import sys
import time
import secrets
import asyncio
import argparse
import logging
import weakref
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import (
    TransportSecuritySettings,
    TransportSecurityMiddleware,
)
import mcp.types as types
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.requests import Request
from starlette.responses import Response
import uvicorn

from .service import _prepare_for_llm
from . import audit
from .calls import INLINE_RUNNER
from .categories import CATEGORY_VOCABULARY, MAX_CATEGORIES
from .namespace_policy import NamespaceNotFound, NAMESPACE_NOT_FOUND
from .embed import EmbeddingError, EmbeddingInputTooLong
from .lexical import LexicalQueryError
from .anchors import InvalidAnchors, MAX_ANCHORS
from .maintenance import MaintenanceRejected
from .report_feedback import (InvalidReport, SearchReceiptRegistry, SEARCH_TOOLS,
                              FEEDBACK_SOURCES, FEEDBACK_PURPOSES, MAX_REPORT_RATINGS,
                              analysis_group)

logger = logging.getLogger("server")

_CATEGORIES_JOINED = ", ".join(CATEGORY_VOCABULARY)

_ANCHOR_GUIDANCE = (
    "When adding a memory or changing its text, provide metadata.anchors: 0-"
    + str(MAX_ANCHORS) + " distinctive identifiers present in the content that a future "
    "query could use to identify it (names, classes, APIs, error codes, config keys, "
    "versions). Keywords describe topics; anchors identify specific things. "
    "Select anchors from the content, preserve their spelling, and do not invent them "
    "or copy generic keywords. Pass [] when there are none. "
    "Explicit anchors replace the old list; [] clears it. Changing text without "
    "resubmitting anchors clears old anchors; metadata-only and whitespace-equivalent "
    "text updates preserve them when omitted. "
)
_ANCHOR_SCHEMA = {
    "type": "array",
    "items": {"type": "string"},
    "maxItems": MAX_ANCHORS,
    "description": _ANCHOR_GUIDANCE + "Each anchor must be non-empty and at most 128 "
                   "characters after NFKC and whitespace normalization; invalid input is rejected.",
}

class MCPApplication:
    def __init__(self, service=None, *, runtime=None):
        if service is None and runtime is None:
            raise ValueError("service or runtime is required")
        self._service = service
        self.runtime = runtime
        self._session_ids = weakref.WeakKeyDictionary()
        self._search_receipts = SearchReceiptRegistry()
        self.server = Server("memory-server")
        self.server.list_tools()(self.handle_list_tools)
        self.server.call_tool()(self.handle_call_tool)
        self.server.list_resources()(self.handle_list_resources)
        self.server.read_resource()(self.handle_read_resource)

    @property
    def service(self):
        return self._service if self._service is not None else self.runtime.service

    @property
    def audit(self):
        return self.runtime.audit if self.runtime is not None else self.service.audit

    def _audit_context(self):
        try:
            request = self.server.request_context
        except LookupError:
            context = {"source": "mcp", "request_id": "rq_" + secrets.token_hex(8)}
        else:
            session = request.session
            if session not in self._session_ids:
                self._session_ids[session] = "ss_" + secrets.token_hex(8)
            client = session.client_params.clientInfo if session.client_params else None
            context = {"source": "mcp", "request_id": str(request.request_id)[:128],
                       "session_id": self._session_ids[session],
                       "client_name": str(client.name)[:128] if client else None,
                       "client_version": str(client.version)[:128] if client else None}
        # Streamable HTTP requests carry a server-generated HTTP correlation id.
        # It is merged here explicitly because an explicit context wins over the
        # journal's request_audit_context fallback; it never replaces source=mcp
        # or the JSON-RPC request_id.
        transport_context = audit.http_transport_context.get()
        if transport_context:
            context["transport"] = transport_context.get("transport")
            context["http_request_id"] = transport_context.get("http_request_id")
        return context

    async def handle_list_tools(self) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[
            types.Tool(
                name="list_keywords",
                description="Browse the complete in-use keyword vocabulary or find existing words by meaning. "
                            "Before setting keywords in add/update, use semantic mode with 1–20 concepts in queries "
                            "and the target namespace (omit namespace when creating a new one); reuse candidates only when their meaning fits, otherwise new "
                            "words are allowed. Similarity is not synonym equivalence. Metadata updates without "
                            "keywords need no lookup. Literal mode matches one substring; browse takes no queries. "
                            "Counts are scoped to live memories in namespace (omit for all). Semantic results include "
                            "score and count, sorted by score; exact hits do not suppress related candidates. "
                            "No search_id/report feedback is required. Pass the returned keyword_query_id to a later "
                            "add/update/search when it informed your keyword choice. Unknown namespace returns namespace_not_found.",
                inputSchema={"type": "object", "additionalProperties": False, "properties": {
                    "namespace": {"type": "string", "description": "Optional live namespace; omitted means all."},
                    "mode": {"type": "string", "enum": ["browse", "literal", "semantic"], "default": "browse"},
                    "queries": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 20,
                                "description": "One nonblank string for literal, 1–20 for semantic; omit for browse."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100,
                              "description": "Browse/literal default 50, max 100; semantic per-query default 5, max 20."},
                    "offset": {"type": "integer", "minimum": 0, "default": 0,
                               "description": "Pagination offset; must be 0 in semantic mode."},
                    "sort_by": {"type": "string", "enum": ["count", "keyword", "score"],
                                "description": "Browse/literal: count (default) or keyword. Semantic: score only."},
                    "min_score": {"type": "number", "minimum": 0, "maximum": 1,
                                  "description": "Semantic only, default 0.65. Lower to broaden candidates."},
                }},
            ),
            types.Tool(
                name="add",
                description="Store a new memory. Text is automatically embedded for semantic search. "
                            "You MUST provide a concise, faithful summary in the summary parameter; "
                            "keep technical terms, ticket numbers, and business event times. The server "
                            "still accepts a missing or blank summary and stores a deterministic extract "
                            "from the leading sentence or line as a reliability fallback. "
                            "Deduplicated by content+namespace: if the same text already exists in the "
                            "target namespace, no duplicate is inserted and the existing record's id is "
                            "returned with created=false. "
                            "Before adding, call 'list_keywords' in semantic mode with your keyword concepts and target namespace; for a new namespace, omit the namespace during lookup. Call 'stats' for category_counts. "
                            "Reuse an existing keyword/category when semantically appropriate; "
                            "create a new one only when no existing value matches. Pass keywords as an "
                            "array in metadata.keywords — they are content concepts (e.g. entities, "
                            "topics). For each new memory, provide 1–3 relevant categories in "
                            "metadata.categories to describe its nature/classification; prefer "
                            "these canonical categories: " + _CATEGORIES_JOINED + ". "
                            + _ANCHOR_GUIDANCE +
                            "An unknown namespace is rejected without writing; call 'stats' to see "
                            "the available namespaces, then retry with "
                            'confirm_codes=["namespace_not_found"] to deliberately create a new one.',
                inputSchema={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "The memory text. Keep long project documents in the repository and store only a concise summary and file path here; oversized input is rejected rather than silently truncated."},
                        "summary": {
                            "type": "string",
                            "description": "A concise summary of text. LLM callers MUST provide this; missing or blank values are tolerated and replaced with a deterministic leading-text extract.",
                        },
                        "metadata": {
                            "type": "object",
                            "description": "Optional key-value metadata",
                            "properties": {
                                "keywords": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Content keywords (concrete concepts in the memory, not category/type). Prefer reusing existing keywords from list_keywords semantic candidates; create a new keyword only when none fits. Normalized to lowercase.",
                                },
                                "anchors": _ANCHOR_SCHEMA,
                                "categories": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "maxItems": MAX_CATEGORIES,
                                    "description": "For a new memory provide 1-%d relevant categories describing its nature/classification. Prefer canonical values; introduce a new one only when none fits: %s. Normalized to lowercase." % (MAX_CATEGORIES, _CATEGORIES_JOINED),
                                },
                            },
                        },
                        "namespace": {"type": "string", "description": "Namespace (default: $MEMORY_DEFAULT_NAMESPACE or 'default'). The configured default namespace is always valid; other namespaces must already exist unless confirm_codes includes 'namespace_not_found'."},
                        "confirm_codes": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": 'Confirmation codes for validation rules you deliberately override. Pass ["namespace_not_found"] to create a namespace that does not exist yet. Unknown codes are ignored; format/length/reserved-word checks cannot be bypassed.',
                        },
                        "keyword_query_id": {"type": "string", "description": "Optional ID from list_keywords when its candidates informed this write; audit linkage only."},
                    },
                    "required": ["text"],
                },
            ),
            types.Tool(
                name="search_semantic",
                description="Semantic search across memories by query text. "
                            "Use this for topic/keyword questions — e.g. \"what do we know about X\", "
                            "\"anything related to Y\" — where you want the most semantically similar memories. "
                            "Results return the memory summary (falling back to a deterministic leading-text extract when absent); "
                            "call 'get_details' with the returned IDs to fetch full content. "
                            "For time-based questions (e.g. \"what did we do recently / last session\") "
                            "use 'search_meta' with sort_by='updated_at' instead. "
                            "When you know the memory's keyword or category, pass keywords= and/or categories= to pre-filter candidates "
                            "before similarity scoring. Exact index values are used directly; unmatched values "
                            "are semantically resolved to the closest existing values in the same dimension and "
                            "namespace. For exploratory questions, omit filters to keep the full candidate set. "
                            "Use 'list_keywords' or 'stats' to inspect existing values. The response reports "
                            "the applied mapping in filter_resolution. "
                            "Values within each dimension use OR; keywords and "
                            "categories combine with each other and namespace using AND. "
                            "If no results are found, inspect filter_resolution when filters were supplied, "
                            "or call 'stats' to verify the namespace before retrying. If the response has response_code='namespace_not_found', "
                            "the requested namespace does not exist; use response_meta.known_namespaces or omit "
                            "the namespace to search all namespaces.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search text — automatically embedded for semantic similarity matching"},
                        "k": {"type": "integer", "description": "Max number of results to return (default: 5). Increase this (e.g. 20) when searching broadly or if results seem sparse."},
                        "threshold": {"type": "number", "description": "Minimum similarity score 0-1 (default: 0 = no filter). Higher values return fewer but more relevant results. Keep at 0 unless you want to narrow down."},
                        "namespace": {"type": "string", "description": "Only search within this namespace (optional). Leave empty to search all namespaces. An unknown namespace returns response_code='namespace_not_found' with the available list instead of an empty success; call 'stats' to see available namespaces."},
                        "keywords": {
                            "type": "array",
                            "maxItems": 20,
                            "items": {"type": "string"},
                            "description": "Use when the desired content concept is known. Restrict candidates to memories carrying any of these keywords (OR) before memory-text cosine scoring. Exact values win; unmatched words are semantically resolved within the same namespace. Omit for broad discovery. Combined with categories and namespace using AND.",
                        },
                        "categories": {
                            "type": "array",
                            "maxItems": 20,
                            "items": {"type": "string"},
                            "description": "Use when the desired memory nature/classification is known. Restrict candidates to memories carrying any of these categories (OR) before memory-text cosine scoring. Exact values win; unmatched words are semantically resolved within the same namespace. Omit for broad discovery. Combined with keywords and namespace using AND.",
                        },
                        "keyword_query_id": {"type": "string", "description": "Optional ID from list_keywords; audit linkage only."},
                    },
                    "required": ["query"],
                },
            ),
            types.Tool(
                name="search_lexical",
                description="Exact-term (lexical) search over memory text using full-text "
                            "matching (SQLite FTS5 / BM25). Use this for literal identifiers, "
                            "error codes, class or symbol names, version numbers and distinctive "
                            "phrases that semantic search may miss — e.g. "
                            "\"PoolAcquirePendingLimitException\", \"ORA-00060\", "
                            "\"subscriptionsPerConnection\". "
                            "It matches memory CONTENT only (not keywords/categories) and requires "
                            "an actual term match, so it does not generalise to paraphrases; use "
                            "'search_semantic' for topic or conceptual questions. Continuous Chinese "
                            "text is not segmented in this index, so use 'search_semantic' for Chinese "
                            "queries rather than 'search_lexical'. "
                            "Results return the memory summary; call 'get_details' with the returned "
                            "IDs for full content. Scores are BM25-based and are NOT comparable with "
                            "search_semantic scores. "
                            "If no results are found, call 'stats' to verify the namespace, or retry "
                            "with 'search_semantic'. If the response has response_code='namespace_not_found', "
                            "the requested namespace does not exist; use response_meta.known_namespaces "
                            "or omit the namespace to search all namespaces.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Literal text or terms to match. The query's terms are OR-combined; long identifiers may be split on punctuation, so include the distinctive parts."},
                        "k": {"type": "integer", "description": "Max number of results to return (default: 10, range 1-100)."},
                        "namespace": {"type": "string", "description": "Only search within this namespace (optional). Leave empty to search all namespaces. An unknown namespace returns response_code='namespace_not_found'."},
                    },
                    "required": ["query"],
                },
            ),
            types.Tool(
                name="search_anchor",
                description="Search the retrieval anchors a memory declared — distinctive "
                            "identifiers, names, class/API names, error codes, config keys, "
                            "versions. Use this when the query contains distinctive terms "
                            "likely to appear in a declared anchor (e.g. a class name or ticket id). "
                            "It matches the ANCHORS field only (not memory text, keywords or "
                            "categories), so it can be narrower than 'search_lexical' and "
                            "'search_semantic'; retry with those when it returns nothing. "
                            "Results return the memory summary; call 'get_details' with the "
                            "returned IDs for full content. Scores are BM25-based and are NOT "
                            "comparable with the other search scores. "
                            "If the response has response_code='namespace_not_found', the "
                            "requested namespace does not exist; use response_meta.known_namespaces "
                            "or omit the namespace to search all namespaces.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Anchor text to match. The query's terms are OR-combined."},
                        "k": {"type": "integer", "description": "Max number of results to return (default: 10, range 1-100)."},
                        "namespace": {"type": "string", "description": "Only search within this namespace (optional). Leave empty to search all namespaces. An unknown namespace returns response_code='namespace_not_found'."},
                    },
                    "required": ["query"],
                },
            ),
            types.Tool(
                name="search_meta",
                description="List memories by exact criteria: keywords, categories, namespace, "
                            "updated_at time range, "
                            "sort order and pagination. "
                            "Results return the memory summary (falling back to a deterministic leading-text extract when absent); "
                            "call 'get_details' with the returned IDs to fetch full content. "
                            "Use this for time-based questions such as \"what did we do recently / last session\" "
                            "— combine sort_by='updated_at' with a small limit to get the most recently "
                            "written or changed memories. For fuzzy/topic/keyword questions use 'search_semantic' instead. "
                            "For keyword filtering, call 'list_keywords'; use 'stats' for categories in category_counts. Multiple keywords match ANY (OR), "
                            "and multiple categories match ANY (OR). Keywords/categories combine with "
                            "namespace and time filters using AND. "
                            "For relative time windows (\"last week\", \"this month\"), call 'stats' first to get "
                            "server_time, then pass concrete updated_after/updated_before timestamps. "
                            "Ordered by the given field descending (default: created_at). If the response has "
                            "response_code='namespace_not_found', the requested namespace does not exist; use "
                            "response_meta.known_namespaces or omit the namespace to list all memories.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "namespace": {"type": "string", "description": "Only list memories in this namespace (optional). Leave empty to list all namespaces. An unknown namespace returns response_code='namespace_not_found' with the available list instead of an empty success."},
                        "limit": {"type": "number", "description": "Max results (default: 100). Use a small value (e.g. 5-10) for recent-memory lookups."},
                        "offset": {"type": "number", "description": "Pagination offset (default: 0)"},
                        "sort_by": {"type": "string", "enum": ["created_at", "updated_at"],
                                    "description": "Sort field, descending. Default: created_at. Use 'updated_at' for most recently written/changed first."},
                        "updated_after": {"type": "string", "description": "ISO8601 timestamp (e.g. '2026-07-01T00:00:00+00:00'). Only return memories updated after this moment (exclusive). If the user asks for a relative window (e.g. 'last week', 'this month') and you are unsure of the current date, call 'stats' first to get its 'server_time', then compute this value."},
                        "updated_before": {"type": "string", "description": "ISO8601 timestamp (e.g. '2026-07-01T00:00:00+00:00'). Only return memories updated before this moment (exclusive). If the user asks for a relative window (e.g. 'last week', 'this month') and you are unsure of the current date, call 'stats' first to get its 'server_time', then compute this value."},
                        "keywords": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Filter by global keywords — any match is sufficient (OR). Call 'list_keywords' to find available keywords.",
                        },
                        "categories": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Filter by memory categories — any match is sufficient (OR). Call 'stats' first to see canonical categories in category_counts.",
                        },
                        "keyword_query_id": {"type": "string", "description": "Optional ID from list_keywords; audit linkage only."},
                    },
                },
            ),
            types.Tool(
                name="get_details",
                description="Fetch full content for one or more memories by ID. "
                            "Pass an array of IDs, including for a single memory. "
                            "Returns the complete text of each found memory plus the list of IDs that "
                            "do not exist (partial success). Use this when a search result summary looks "
                            "relevant and you need the full original content.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "memory_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Array of memory IDs to fetch full content for; use a one-element array for a single ID.",
                        },
                        "source_search_id": {"type": "string", "description": "Optional search_id from the search whose results prompted this lookup; audit linkage only."},
                    },
                    "required": ["memory_ids"],
                },
            ),
            types.Tool(
                name="update",
                description="Update a memory's text, summary, metadata and/or namespace. "
                            "When changing text, you MUST also provide a concise, faithful summary for "
                            "the new text. The server still accepts an omitted or blank summary and "
                            "stores a deterministic leading-text extract as a reliability fallback. A summary may also "
                            "be corrected without changing text; metadata-only updates leave it untouched. "
                            "Long project documents belong in the repository; store only their concise "
                            "summary and path in memory. Oversized text is rejected, never silently truncated. "
                            "When changing the memory text, provide 1–3 relevant categories in metadata.categories "
                            "for the updated memory; metadata-only changes can preserve existing categories. "
                            "When updating metadata you may pass metadata.keywords — content concepts; "
                            "reuse existing keywords from list_keywords semantic candidates when one fits; an empty "
                            "array clears. You may also pass metadata.categories — memory "
                            "nature/classification, up to " + str(MAX_CATEGORIES) + " values, prefer the "
                            "canonical categories from stats' category_counts: " + _CATEGORIES_JOINED + ". "
                            "An empty array clears the memory's categories. "
                            + _ANCHOR_GUIDANCE +
                            "Moving a memory to an unknown namespace is rejected; call 'stats' to see "
                            "the available namespaces, then retry with "
                            'confirm_codes=["namespace_not_found"] to deliberately create a new one.',
                inputSchema={
                    "type": "object",
                    "properties": {
                        "memory_id": {"type": "string"},
                        "source_search_id": {"type": "string", "description": "Optional search_id that prompted this update; audit linkage only."},
                        "keyword_query_id": {"type": "string", "description": "Optional ID from list_keywords when its candidates informed this update; audit linkage only."},
                        "text": {"type": "string", "description": "New text. Oversized content is rejected; keep long documents in the repository and save a summary/path here."},
                        "summary": {"type": "string", "description": "New summary. LLM callers changing text MUST provide this; omission is tolerated and uses a deterministic leading-text extract."},
                        "metadata": {
                            "type": "object",
                            "description": "Metadata to merge",
                            "properties": {
                                "keywords": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Content keywords (concrete concepts in the memory, not category/type). Prefer reusing existing keywords from list_keywords semantic candidates; create a new keyword only when none fits. Normalized to lowercase. Empty array clears.",
                                },
                                "anchors": _ANCHOR_SCHEMA,
                                "categories": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "maxItems": MAX_CATEGORIES,
                                    "description": "When changing text, provide 1-%d relevant categories describing the updated memory's nature/classification. Prefer canonical values; introduce a new one only when none fits: %s. Metadata-only updates may omit this field; empty array clears." % (MAX_CATEGORIES, _CATEGORIES_JOINED),
                                },
                            },
                        },
                        "namespace": {"type": "string", "description": "New namespace. The configured default namespace is always valid; other namespaces must already exist unless confirm_codes includes 'namespace_not_found'. Blank/omitted leaves it unchanged."},
                        "confirm_codes": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": 'Confirmation codes for validation rules you deliberately override. Pass ["namespace_not_found"] to move the memory into a namespace that does not exist yet. Unknown codes are ignored; format/length/reserved-word checks cannot be bypassed.',
                        },
                    },
                    "required": ["memory_id"],
                },
            ),
            types.Tool(
                name="delete",
                description="Soft-delete a memory by ID. It is removed from all search results.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "memory_id": {"type": "string"},
                    },
                    "required": ["memory_id"],
                },
            ),
            types.Tool(
                name="stats",
                description="Storage statistics. Returns 'total' (record count), 'namespace_counts' "
                            "(map of namespace -> record count, e.g. check namespaces before search/add to "
                            "avoid missing results or creating duplicates), 'keyword_total' (the global "
                            "number of distinct in-use keywords; use list_keywords to browse or find words), "
                            "'category_counts' (seed categories, always listed with count 0 when unused, "
                            "plus any custom categories in use; prefer seed values, up to 3 per memory), "
                            "and 'server_time' (current UTC time). Check 'server_time' first when a user asks "
                            "a time-based question (e.g. \"what did we do last week / this month\") so you can "
                            "compute the correct updated_after/updated_before values for the 'search_meta' tool. The "
                            "default namespace is set via MEMORY_DEFAULT_NAMESPACE env var.",
                inputSchema={"type": "object", "properties": {}},
            ),
            types.Tool(
                name="report",
                description="Report how helpful a previous search's results were for the current "
                            "question or context. After using results from search_semantic, search_lexical, "
                            "search_anchor or search_meta, pass its search_id plus ratings per memory id. "
                            "Only IDs returned by that nonempty search are accepted. rating must be an integer "
                            "from -10 to 10: "
                            "positive = helpful (larger = more useful), negative = misleading/interference "
                            "(more negative = stronger interference), 0 = neither helpful nor harmful. "
                            "You may rate any nonempty subset of the returned ids. Declare feedback_source "
                            "and feedback_purpose so audit can separate task feedback from tests; these are "
                            "caller declarations, not verified human identity. Reports from an earlier server "
                            "process or expired searches are rejected.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "search_id": {"type": "string", "maxLength": 64,
                                      "description": "The search_id from a recent nonempty search_semantic/search_lexical/search_anchor/search_meta response in this server process"},
                        "ratings": {
                            "type": "array",
                            "minItems": 1, "maxItems": MAX_REPORT_RATINGS,
                            "description": "Rate 1–1000 distinct IDs returned by this search. Values outside -10..10 or non-integers are rejected.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "m_id": {"type": "string", "description": "Memory id from the search results"},
                                    "rating": {"type": "integer", "minimum": -10, "maximum": 10,
                                               "description": "-10..10; positive helpful, negative interfering, 0 neutral"},
                                },
                                "required": ["m_id", "rating"],
                            },
                        },
                        "feedback_source": {"type": "string", "enum": sorted(FEEDBACK_SOURCES),
                                            "description": "Optional caller-declared origin: llm, human, or test. Omitted means unspecified; this does not verify human identity."},
                        "feedback_purpose": {"type": "string", "enum": sorted(FEEDBACK_PURPOSES),
                                             "description": "Optional caller-declared use: task, evaluation, or test. Omitted means unspecified."},
                    },
                    "required": ["search_id", "ratings"],
                },
            ),
        ])

    def _call_runner(self):
        return self.runtime.calls if self.runtime is not None else INLINE_RUNNER

    async def handle_call_tool(self, name: str, arguments: dict) -> types.CallToolResult:
        # Read the SDK/transport context on the event loop, then carry it as a
        # value into the worker; workers never touch the SDK's async context.
        context = self._audit_context()
        write = name in ("add", "update", "delete")
        admission = (self.runtime.maintenance.access(write=write)
                     if self.runtime is not None else None)
        try:
            return await self._call_runner().run(
                lambda: self._call_tool_sync(name, arguments, context), admission=admission)
        except MaintenanceRejected as exc:
            data = {"response_code": exc.code, "detail": str(exc)}
            hint = ("Model is not ready. Open /admin/console to configure proxy, view download progress and retry; "
                    "exit maintenance only after the model is ready." if exc.code == "model_not_ready"
                    else "Maintenance is in progress. Retry this operation later.")
            self.audit.record(name, arguments, data, 0.0, response_code=exc.code,
                              context=context)
            return _ok(data, hint)

    def _call_tool_sync(self, name: str, arguments: dict, context: dict) -> types.CallToolResult:
        logger.info("Tool call: %s %s", name, arguments)
        start = time.perf_counter()
        memory_refs = []
        try:
            if self.runtime is not None and self.runtime.state != "running":
                raise RuntimeError("service is closing; no new work accepted")
            if name == "add":
                data = self.service.memory_add(arguments["text"], arguments.get("metadata"),
                                  arguments.get("namespace"), arguments.get("summary"),
                                  arguments.get("confirm_codes"))
                if data.get("id"):
                    memory_refs = [{"id": data["id"], "namespace": arguments.get("namespace") or self.service.settings.DEFAULT_NAMESPACE}]
            elif name == "search_semantic":
                search_result = self.service.memory_search(
                    arguments["query"], int(arguments.get("k", 5)),
                    float(arguments.get("threshold", 0.0)), arguments.get("namespace"),
                    keywords=arguments.get("keywords"),
                    categories=arguments.get("categories"), return_resolution=True)
                search_result["items"] = _prepare_for_llm(search_result["items"])
                data = _wrap_search_semantic(search_result)
            elif name == "search_lexical":
                items = _prepare_for_llm(self.service.memory_search_lexical(
                    arguments["query"], int(arguments.get("k", 10)),
                    arguments.get("namespace")))
                data = _wrap_search_lexical(items)
            elif name == "search_anchor":
                items = _prepare_for_llm(self.service.memory_search_anchor(
                    arguments["query"], int(arguments.get("k", 10)),
                    arguments.get("namespace")))
                data = _wrap_search_anchor(items)
            elif name == "search_meta":
                ns = arguments.get("namespace")
                limit = int(arguments.get("limit", 100))
                offset = int(arguments.get("offset", 0))
                sort_by = arguments.get("sort_by", "created_at")
                items = _prepare_for_llm(self.service.memory_list(ns, limit, offset, sort_by,
                                                     arguments.get("updated_after"), arguments.get("updated_before"),
                                                     keywords=arguments.get("keywords"),
                                                     categories=arguments.get("categories")))
                data = _wrap_search_meta(items, ns, limit, offset, sort_by)
            elif name == "get_details":
                data = self.service.memory_get_details(arguments["memory_ids"])
                memory_refs = [{"id": item.get("id"), "namespace": item.get("namespace")}
                               for item in data.get("found", [])[:100]]
            elif name == "update":
                old = self.service.storage.get(arguments["memory_id"])
                data = self.service.memory_update(arguments["memory_id"], arguments.get("text"),
                                     arguments.get("metadata"), arguments.get("namespace"),
                                     arguments.get("summary"), arguments.get("confirm_codes"))
                new = self.service.storage.get(arguments["memory_id"])
                memory_refs = [{"id": arguments["memory_id"],
                                "namespace_before": old.get("namespace") if old else None,
                                "namespace": new.get("namespace") if new else None}]
            elif name == "delete":
                data = self.service.memory_delete(arguments["memory_id"])
            elif name == "list_keywords":
                data = self.service.memory_list_keywords(**arguments)
                data["keyword_query_id"] = "kq_" + secrets.token_hex(8)
            elif name == "stats":
                data = _wrap_stats(self.service.memory_stats())
            elif name == "report":
                receipt = self._search_receipts.validate(
                    arguments.get("search_id"), arguments.get("ratings"),
                    arguments.get("feedback_source"), arguments.get("feedback_purpose"))
                data = self.service.memory_report(arguments["search_id"], arguments["ratings"])
                data["feedback_context"] = {
                    "declared_source": arguments.get("feedback_source") or "unspecified",
                    "declared_purpose": arguments.get("feedback_purpose") or "unspecified",
                    "declaration_trust": "caller_declared",
                    "analysis_group": analysis_group(arguments.get("feedback_source"),
                                                     arguments.get("feedback_purpose")),
                    "search_tool": receipt.tool,
                    "search_namespace": receipt.namespace,
                    "search_request_id": receipt.request_id,
                    "search_transport": receipt.transport,
                    "result_membership": "validated",
                }
            else:
                raise ValueError(f"Unknown tool: {name}")
            if name in SEARCH_TOOLS and data.get("items"):
                self._search_receipts.remember(data["search_id"], name, data["items"],
                                               arguments.get("namespace"), context)
            hint = _result_hint(name, arguments, data)
            self.audit.record(name, arguments, data, (time.perf_counter() - start) * 1000,
                              context=context, memory_refs=memory_refs)
            return _ok(data, hint)
        except NamespaceNotFound as e:
            # Not an MCP error: return a structured result so the LLM can read the
            # available namespaces and retry with confirm_codes when intended.
            data, hint = _namespace_error_result(
                e, search=name in ("search_semantic", "search_meta",
                                   "search_lexical", "search_anchor"))
            if name == "list_keywords":
                data.update({"namespace": e.requested, "mode": arguments.get("mode", "browse"), "results": []})
                hint = "Unknown namespace; choose a known namespace from response_meta or omit namespace."
            self.audit.record(name, arguments, data, (time.perf_counter() - start) * 1000,
                              context=context)
            return _ok(data, hint)
        except (EmbeddingError, RuntimeError) as exc:
            if isinstance(exc, EmbeddingInputTooLong):
                code, hint = "embedding_input_too_long", "Shorten the input and retry."
            elif isinstance(exc, EmbeddingError):
                code, hint = "embedding_unavailable", "Check the configured embedding provider, then retry."
            elif self.runtime is not None and self.runtime.state != "running":
                code, hint = "service_closing", "Reconnect after the service restarts."
            else:
                self.audit.record(name, arguments, None, (time.perf_counter() - start) * 1000,
                                  error={"type": type(exc).__name__, "message": str(exc)},
                                  context=context)
                raise
            data = {"response_code": code, "detail": str(exc)}
            self.audit.record(name, arguments, data, (time.perf_counter() - start) * 1000,
                              error={"type": type(exc).__name__, "message": str(exc)},
                              response_code=code, context=context)
            return _ok(data, hint)
        except InvalidAnchors as exc:
            # Caller anchors failed strict validation: rejected, never coerced to [].
            code = "invalid_anchors"
            data = {"response_code": code, "detail": str(exc)}
            hint = ("Fix metadata.anchors (0-5 distinct strings, each non-empty and at most "
                    "128 characters) and retry. Pass [] for no anchors. On update, omitting "
                    "anchors preserves them only when the text is unchanged or whitespace-equivalent; "
                    "a substantive text change clears them.")
            self.audit.record(name, arguments, data, (time.perf_counter() - start) * 1000,
                              error={"type": type(exc).__name__, "message": str(exc)},
                              response_code=code, context=context)
            return _ok(data, hint)
        except LexicalQueryError as exc:
            # Invalid lexical input (type/blank/over-limit/k): structured, correctable.
            code = "invalid_query"
            data = {"response_code": code, "detail": str(exc)}
            hint = "Fix the query or k and retry."
            self.audit.record(name, arguments, data, (time.perf_counter() - start) * 1000,
                              error={"type": type(exc).__name__, "message": str(exc)},
                              response_code=code, context=context)
            return _ok(data, hint)
        except InvalidReport as exc:
            data = {"recorded": False, "count": 0, "response_code": "invalid_report",
                    "response_meta": {"reason": exc.reason,
                                      **({"invalid_ids": exc.invalid_ids} if exc.invalid_ids else {})}}
            hint = ("Use a search_id from a recent nonempty search in this server process "
                    "and rate only distinct IDs returned by it, with integer ratings from -10 to 10. "
                    "Declare feedback_source and feedback_purpose if known.")
            self.audit.record(name, arguments, data, (time.perf_counter() - start) * 1000,
                              error={"type": "InvalidReport", "message": str(exc)},
                              response_code="invalid_report", context=context)
            return _ok(data, hint)
        except Exception as e:
            self.audit.record(name, arguments, None, (time.perf_counter() - start) * 1000,
                              error={"type": type(e).__name__, "message": str(e)},
                              context=context)
            logger.error("Tool call '%s' failed: %s", name, e, exc_info=True)
            raise

    async def handle_list_resources(self) -> types.ListResourcesResult:
        return types.ListResourcesResult(resources=[
            types.Resource(
                uri="memories://stats",
                name="Memory Stats",
                description="Storage statistics",
                mimeType="application/json",
            ),
            types.Resource(
                uri="memories://list",
                name="All Memories",
                description="All stored memories (no embeddings)",
                mimeType="application/json",
            ),
        ])

    async def handle_read_resource(self, uri: str) -> types.ReadResourceResult:
        admission = self.runtime.maintenance.access() if self.runtime is not None else None
        return await self._call_runner().run(
            lambda: self._read_resource_sync(uri), admission=admission)

    def _read_resource_sync(self, uri: str) -> types.ReadResourceResult:
        logger.info("Resource read: %s", uri)
        if uri == "memories://stats":
            return types.ReadResourceResult(contents=[
                types.TextResourceContents(uri=uri, mimeType="application/json",
                                           text=json.dumps(self.service.memory_stats(), ensure_ascii=False)),
            ])
        if uri == "memories://list":
            return types.ReadResourceResult(contents=[
                types.TextResourceContents(uri=uri, mimeType="application/json",
                                           text=json.dumps(_prepare_for_llm(self.service.memory_list(limit=1000)),
                                                           ensure_ascii=False, default=str)),
            ])
        raise ValueError(f"Unknown resource: {uri}")

def create_mcp_server(service=None, *, runtime=None):
    return MCPApplication(service, runtime=runtime)


# Guidance appended to each tool's result so the LLM knows what it can do next
# with the returned data. Kept intentionally concise.
_TOOL_HINTS: dict[str, str] = {
    "list_keywords": "Reuse a candidate only if it expresses your concept; new keywords are allowed. Counts reflect live memories in the requested namespace. Pass keyword_query_id to a later add/update/search when it informed your choice. No report is needed.",
    "add": "Memory stored. For future writes, use list_keywords to reuse fitting content concepts, 1–3 relevant metadata.categories from stats, and metadata.anchors for distinctive identifiers. Call get_details with memory_ids=[id] if you need to verify the stored record.",
    "search_semantic": "Results are ranked by memory-text cosine similarity. Use get_details(memory_ids=[...], source_search_id=this search_id) for full text; after using results, call report with this search_id and helpfulness ratings (-10..10).",
    "search_lexical": "Results are ranked by BM25; scores are not comparable with semantic scores. Use get_details(memory_ids=[...], source_search_id=this search_id) for full text; after using results, call report with this search_id and helpfulness ratings (-10..10).",
    "search_anchor": "Results match declared anchors and are ranked by BM25; scores are not comparable with other search scores. Use get_details(memory_ids=[...], source_search_id=this search_id) for full text; after using results, call report with this search_id and helpfulness ratings (-10..10).",
    "search_meta": "Results match exact filters, sorted by the requested time field. Use get_details(memory_ids=[...], source_search_id=this search_id) for full text; after using results, call report with this search_id and helpfulness ratings (-10..10). For conceptual lookup, use search_semantic.",
    "get_details": "Read the returned full text and metadata. To find related records, use search_meta for known exact keywords/categories, search_anchor for a declared identifier, or search_semantic for conceptual similarity.",
    "update": "Memory updated. Call get_details with memory_ids=[memory_id] if you need to verify the stored record. When changing text in future, provide an updated summary, 1–3 relevant metadata.categories, and fresh metadata.anchors when distinctive identifiers exist.",
    "delete": "Memory soft-deleted and removed from search results. Call stats if you need to verify the record count.",
    "stats": "Use namespace_counts to choose a namespace. Use search_meta for recent records or exact filters, search_semantic for concepts, search_lexical for literal text, and search_anchor for declared identifiers. Use list_keywords for the full keyword vocabulary, category_counts for category choices, and server_time for relative date ranges.",
    "report": "Feedback recorded. Continue with the task or search again if the results were unhelpful.",
}


def _result_hint(name: str, arguments: dict, data: dict) -> str:
    """Choose next-step guidance from the actual result, not only the tool name."""
    hint = _TOOL_HINTS[name]
    if name == "list_keywords":
        if data.get("mode") == "semantic":
            return (hint + " Scores measure similarity, not equivalence. Lower min_score "
                    "to broaden semantic candidates when needed.")
        return (hint + " Use offset and limit to page through browse/literal results; "
                "use semantic mode to find related terms by meaning.")
    if name == "add":
        if data.get("created") is False:
            return ("No new memory was stored: identical text already exists in this namespace. "
                    "Call get_details with memory_ids=[id] to inspect it; use update if it needs changes.")
    elif name in ("search_semantic", "search_lexical", "search_anchor", "search_meta"):
        if not data.get("items"):
            if name == "search_semantic":
                if data.get("filter_resolution") is not None:
                    return ("No memories matched. Inspect filter_resolution; adjust or remove "
                            "unresolved filters if present, otherwise broaden the query or filters. "
                            "Use stats to verify the namespace.")
                return ("No memories matched. Verify the namespace with stats; try a broader "
                        "semantic query, search_lexical for literal identifiers, or search_meta for recent records.")
            if name == "search_lexical":
                return ("No literal content match. Check the namespace with stats; try "
                        "search_anchor for a declared identifier or search_semantic for concepts/Chinese text.")
            if name == "search_anchor":
                return ("No declared anchor matched. Check the namespace with stats; try "
                        "search_lexical for content terms or search_semantic for concepts.")
            return ("No memories in this page. Check the namespace and exact filters or widen "
                    "the time range; reset offset if paginating. Try search_semantic for concepts.")
        if name == "search_semantic" and data.get("filter_resolution") is not None:
            hint += " Inspect filter_resolution to see which exact or semantic filter values were applied."
        if name == "search_meta":
            limit = int(arguments.get("limit", 100))
            if limit > 0 and len(data["items"]) == limit:
                hint += (f" This page is full; more records may exist. To continue, "
                         f"retry with offset={int(arguments.get('offset', 0)) + limit}.")
    elif name == "get_details":
        if not data.get("found"):
            return ("None of the requested IDs were found. Recheck the IDs or run a new search; "
                    "missing lists the unresolved IDs.")
        if data.get("missing"):
            hint += " Some IDs were missing; recheck those IDs or search again."
    elif name == "update":
        if not data.get("success"):
            return ("No memory was updated because the ID was not found. "
                    "Search again and use an existing ID before retrying.")
    elif name == "delete":
        if not data.get("success"):
            return ("No memory was deleted because the ID was not found or already deleted. "
                    "Search again if you need to locate it.")
    elif name == "report" and not data.get("count"):
        return ("No valid ratings were recorded. If you used a nonempty search result, "
                "pass ratings for IDs from that result and its search_id.")
    if name in ("add", "update") and data.get("categories_dropped"):
        hint += (f" Only the first {MAX_CATEGORIES} categories were kept; "
                 f"pass at most {MAX_CATEGORIES} in metadata.categories next time.")
    return hint


def _ok(data, hint: str = "") -> types.CallToolResult:
    """Serialize a tool result; attach a 'hint' field guiding the LLM on next steps.

    The hint is applied at the MCP tool boundary only — admin HTTP APIs are
    unaffected.
    """
    if isinstance(data, dict) and hint:
        data = {**data, "hint": hint}
    return types.CallToolResult(content=[
        types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False, default=str)),
    ])


def _new_search_id() -> str:
    """Generate a per-call search id for correlating searches with reports."""
    return "se_" + secrets.token_hex(16)


def _wrap_search_meta(items: list, namespace: str | None, limit: int, offset: int, sort_by: str) -> dict:
    """Attach usage guidance to search_meta results so the LLM knows how to consume them.

    The raw item list must stay an array for the admin UI (/api/memories);
    this wrapper is only applied at the MCP tool boundary.
    """
    scope = f"namespace={namespace!r}" if namespace else "all namespaces"
    if items:
        note = (
            f"{len(items)} memories in this page (limit={limit}, offset={offset}) from {scope}, "
            f"sorted by {sort_by} descending. Items carry summaries. For full text, call "
            f"get_details with memory_ids and source_search_id=this search_id. After using "
            f"these results, call report with this search_id and rate reviewed ids (-10..10)."
        )
    else:
        note = f"0 memories in this page (limit={limit}, offset={offset}) from {scope}; no ids to inspect or report."
    return {"search_id": _new_search_id(), "note": note, "total": len(items), "items": items}


def _wrap_search_semantic(search_result: dict) -> dict:
    """Attach usage guidance to search_semantic results (same rationale as _wrap_search_meta)."""
    # Accept a bare list for compatibility with small integrations/tests that
    # use this response helper directly.
    if isinstance(search_result, list):
        search_result = {"items": search_result, "filter_resolution": None}
    items = search_result["items"]
    if items:
        note = (
            f"{len(items)} results ranked by semantic similarity. Items carry summaries. "
            f"For full text, call get_details with memory_ids and source_search_id=this search_id. "
            f"After using these results, call report with this search_id and rate reviewed ids (-10..10)."
        )
    else:
        note = "0 semantic results; no ids to inspect or report."
    result = {"search_id": _new_search_id(), "note": note,
              "total": len(items), "items": items}
    if search_result.get("filter_resolution") is not None:
        result["filter_resolution"] = search_result["filter_resolution"]
    return result


def _wrap_search_lexical(items: list) -> dict:
    """Attach usage guidance to search_lexical results (same rationale as search_meta)."""
    if items:
        note = (
            f"{len(items)} results ranked by lexical BM25. Scores are not comparable with semantic scores. "
            f"For full text, call get_details with memory_ids and source_search_id=this search_id. "
            f"After using these results, call report with this search_id and rate reviewed ids (-10..10)."
        )
    else:
        note = "0 lexical results; no ids to inspect or report."
    return {"search_id": _new_search_id(), "note": note, "total": len(items), "items": items}


def _wrap_search_anchor(items: list) -> dict:
    """Attach usage guidance to search_anchor results (same rationale as search_meta)."""
    if items:
        note = (
            f"{len(items)} results ranked by anchor BM25. Scores are not comparable with other search scores. "
            f"For full text, call get_details with memory_ids and source_search_id=this search_id. "
            f"After using these results, call report with this search_id and rate reviewed ids (-10..10)."
        )
    else:
        note = "0 anchor results; no ids to inspect or report."
    return {"search_id": _new_search_id(), "note": note, "total": len(items), "items": items}


def _namespace_error_result(exc: NamespaceNotFound, search: bool = False) -> tuple[dict, str]:
    """Build the unified unknown-namespace payload and hint.

    Used by search_semantic/search_meta (empty-result envelope) and by
    add/update (write rejected; nothing stored). Kept in one place so both
    search tools report the same shape.
    """
    available = ", ".join(repr(ns) for ns in exc.known) or "(none)"
    meta = {"requested_namespace": exc.requested, "known_namespaces": exc.known}
    if search:
        note = (f"Namespace {exc.requested!r} does not exist; 0 results returned. "
                f"Available namespaces: {available}. Retry with one of these, or omit "
                f"the namespace to search all namespaces.")
        data = {"search_id": _new_search_id(), "note": note, "total": 0, "items": [],
                "response_code": NAMESPACE_NOT_FOUND, "response_meta": meta}
        hint = (f"Namespace {exc.requested!r} does not exist, so no search ran. "
                f"Available namespaces: {available}. Retry with one of them, or omit "
                f"namespace to search all namespaces. Call 'stats' to list namespaces.")
    else:
        data = {"response_code": NAMESPACE_NOT_FOUND, "response_meta": meta}
        hint = (f"Namespace {exc.requested!r} does not exist; nothing was written. "
                f"Available namespaces: {available}. To create it deliberately, retry "
                f"with confirm_codes=[\"{NAMESPACE_NOT_FOUND}\"].")
    return data, hint


def _wrap_stats(data: dict) -> dict:
    """Strip internal details from stats — LLM only needs counts + time.

    The admin HTTP API keeps the full payload (primary/secondary/backend/…);
    this wrapper is only applied at the MCP tool boundary.
    """
    out = {k: data[k] for k in ("total", "namespace_counts", "keyword_total", "server_time")}
    out["category_counts"] = data.get("category_counts", {})
    for key in ("embed_provider", "embed_model", "embed_dimension",
                "embed_profile", "embed_dtype"):
        if key in data:
            out[key] = data[key]
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

# Streamable HTTP transport security: loopback-only Host/Origin whitelist.
# The SDK disables DNS-rebinding protection when settings are omitted, so it is
# passed explicitly to both the adapter pre-check and the session manager.
STREAMABLE_HTTP_SECURITY = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=["127.0.0.1:*", "localhost:*"],
    allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
)

_READ_ME_TEMPLATE_PATH = Path(__file__).with_name("read_me.md")


def _read_me_base(port, scope):
    """Resolve the self-referenced base URL for /read-me.

    Host is fixed to loopback by design; only the port is resolved at runtime so
    a changed --port is reflected. Falls back to the ASGI server port when the
    configured port is missing or 0 (e.g. an ephemeral test port).
    """
    resolved = port
    if not resolved:
        server = scope.get("server") or ()
        if len(server) == 2 and server[1]:
            resolved = server[1]
    if not resolved:
        return None
    return f"http://127.0.0.1:{resolved}"


def create_http_app(runtime, *, port=None):
    """Own runtime lifespan; all transports bind the same explicit service."""
    from contextlib import asynccontextmanager
    from .admin import create_admin_routes
    import anyio
    connections = set()
    mcp = create_mcp_server(runtime=runtime)
    sse = SseServerTransport("/messages/")
    security = TransportSecurityMiddleware(STREAMABLE_HTTP_SECURITY)
    session_manager = StreamableHTTPSessionManager(
        app=mcp.server, stateless=True, json_response=True,
        security_settings=STREAMABLE_HTTP_SECURITY,
    )

    @asynccontextmanager
    async def lifespan(app):
        try:
            runtime.start(background_model=True)
            # Entered after start() and exited before close() so in-flight work
            # drains before storage/provider are released.
            async with session_manager.run():
                yield
        finally:
            await asyncio.to_thread(runtime.close)

    class SSEEndpoint:
        async def __call__(self, request_scope, receive, send):
            started = finished = False

            async def track_send(message):
                nonlocal started, finished
                await send(message)
                if message["type"] == "http.response.start":
                    started = True
                elif message["type"] == "http.response.body" and not message.get("more_body", False):
                    finished = True

            # Business handlers are synchronous within their async adapters:
            # cancellation cannot interrupt a store/provider operation.
            with anyio.CancelScope() as scope:
                connections.add(scope)
                try:
                    async with sse.connect_sse(request_scope, receive, track_send) as streams:
                        await mcp.server.run(streams[0], streams[1], mcp.server.create_initialization_options())
                finally:
                    connections.discard(scope)
            # The transport already sent the headers. End its body on shutdown;
            # returning another Response here would send duplicate headers.
            if started and not finished:
                await send({"type": "http.response.body", "body": b"", "more_body": False})

    class StreamableHTTPEndpoint:
        """ASGI adapter for the stateless Streamable HTTP transport."""

        async def __call__(self, request_scope, receive, send):
            request = Request(request_scope, receive)
            # Security is validated for every method, including the ones that
            # end in 405, so GET/DELETE cannot bypass the Host/Origin whitelist.
            error = await security.validate_request(request, is_post=False)
            if error is not None:
                await error(request_scope, receive, send)
                return
            if request.method != "POST":
                if request.method == "HEAD":
                    await send({"type": "http.response.start", "status": 405,
                                "headers": [(b"allow", b"POST")]})
                    await send({"type": "http.response.body", "body": b""})
                else:
                    response = Response("Method Not Allowed", status_code=405,
                                        headers={"Allow": "POST"})
                    await response(request_scope, receive, send)
                return
            # Per-request correlation id for audit; a fresh dict per request so
            # concurrent requests never share or mutate one another's context.
            request_id = (request_scope.get("state") or {}).get("audit_request_id")
            if not request_id:
                request_id = "rq_" + secrets.token_hex(8)
            token = audit.http_transport_context.set(
                {"transport": "streamable_http", "http_request_id": request_id})
            try:
                await session_manager.handle_request(request_scope, receive, send)
            finally:
                audit.http_transport_context.reset(token)

    class ReadMeEndpoint:
        """Static, LLM-facing configuration page with a self-referenced base URL."""

        async def __call__(self, request_scope, receive, send):
            base = _read_me_base(port, request_scope)
            if base is None:
                response = Response("cannot determine service port for /read-me",
                                    status_code=500)
                await response(request_scope, receive, send)
                return
            try:
                template = _READ_ME_TEMPLATE_PATH.read_text(encoding="utf-8")
            except OSError:
                logger.exception("failed to read /read-me template")
                response = Response("read-me template unavailable", status_code=500)
                await response(request_scope, receive, send)
                return
            response = Response(template.replace("{base}", base),
                                media_type="text/markdown; charset=utf-8")
            await response(request_scope, receive, send)

    app = Starlette(routes=[Route("/sse", SSEEndpoint()),
                            Route("/mcp", StreamableHTTPEndpoint()),
                            Route("/read-me", ReadMeEndpoint(), methods=["GET"]),
                            Mount("/messages/", app=sse.handle_post_message)] +
                    create_admin_routes(runtime=runtime), lifespan=lifespan)
    app.state.runtime = runtime

    def close_connections():
        for scope in tuple(connections):
            scope.cancel()
    app.state.close_connections = close_connections

    class AdmissionMiddleware:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http" and runtime.state != "running":
                response = Response(json.dumps({"detail": "service is closing"}), status_code=503,
                                    media_type="application/json")
                await response(scope, receive, send)
            else:
                if scope["type"] == "http":
                    request_id = "rq_" + secrets.token_hex(8)
                    scope.setdefault("state", {})["audit_request_id"] = request_id
                    token = audit.request_audit_context.set(
                        {"source": "admin_http", "request_id": request_id})
                    try:
                        await self.app(scope, receive, send)
                    finally:
                        audit.request_audit_context.reset(token)
                else:
                    await self.app(scope, receive, send)
    app.add_middleware(AdmissionMiddleware)
    return app


async def serve_stdio(runtime):
    import signal
    import os
    import stat

    async def stdin_lines(reader):
        # Chunked reads avoid StreamReader.readline's artificial line limit.
        pending = b""
        while chunk := await reader.read(65536):
            pending += chunk
            lines = pending.split(b"\n")
            pending = lines.pop()
            for line in lines:
                yield line.decode("utf-8", errors="replace")
        if pending:
            yield pending.decode("utf-8", errors="replace")

    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    previous_handler = signal.getsignal(signal.SIGTERM)
    signal_installed = False
    terminated = False
    input_transport = None

    def terminate():
        nonlocal terminated
        terminated = True
        runtime.request_shutdown()
        task.cancel()

    try:
        try:
            loop.add_signal_handler(signal.SIGTERM, terminate)
            signal_installed = True
        except (NotImplementedError, RuntimeError):
            pass  # Non-main-thread/platform embedding; caller owns signals.
        runtime.start()
        mcp = create_mcp_server(runtime=runtime)
        stdin = None
        mode = os.fstat(sys.stdin.fileno()).st_mode
        if stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode) or sys.stdin.isatty():
            # Native pipe reads are cancellable; the SDK's default threaded
            # readline waits indefinitely when a client keeps stdin open.
            reader = asyncio.StreamReader()
            input_transport, _ = await loop.connect_read_pipe(
                lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer)
            stdin = stdin_lines(reader)
        async with stdio_server(stdin=stdin) as (read_stream, write_stream):
            await mcp.server.run(read_stream, write_stream, mcp.server.create_initialization_options())
    except asyncio.CancelledError:
        if not terminated:
            raise
    finally:
        try:
            await asyncio.to_thread(runtime.close)
        finally:
            if input_transport is not None:
                input_transport.close()
            if signal_installed:
                loop.remove_signal_handler(signal.SIGTERM)
                signal.signal(signal.SIGTERM, previous_handler)


async def main(argv=None):
    import os
    from .settings import Settings
    from .runtime import create_runtime
    from .configuration import activate_environment
    parser = argparse.ArgumentParser(description="Memory MCP Server")
    parser.add_argument("--data-dir", help="Data directory; defaults to the per-user OS data directory "
                                          "and contains config.jsonc")
    transport = parser.add_mutually_exclusive_group()
    transport.add_argument("--stdio", action="store_true", help="Use subprocess stdin/stdout instead of HTTP")
    transport.add_argument("--port", type=int, default=None,
                        help="Port for HTTP mode (legacy SSE + Streamable HTTP)")
    args = parser.parse_args(argv)
    if args.port is not None and not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    try:
        settings = Settings.from_config(args.data_dir, create=True)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    activate_environment(settings.environment)
    runtime = create_runtime(settings)
    if args.stdio:
        await serve_stdio(runtime)
    else:
        port = settings.PORT if args.port is None else args.port
        app = create_http_app(runtime, port=port)
        config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
        class GracefulServer(uvicorn.Server):
            async def shutdown(self, sockets=None):
                runtime.request_shutdown(restart=runtime.restart_requested)
                app.state.close_connections()
                await super().shutdown(sockets=sockets)

        uv_server = GracefulServer(config)
        runtime.shutdown_callback = lambda: setattr(uv_server, "should_exit", True)
        try:
            await uv_server.serve()
        finally:
            await asyncio.to_thread(runtime.close)
    if runtime.restart_requested:
        os.execv(sys.executable, [sys.executable, "-m", "app.server"] +
                 (sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    asyncio.run(main())

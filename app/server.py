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
from .categories import CATEGORY_VOCABULARY, MAX_CATEGORIES
from .namespace_policy import NamespaceNotFound, NAMESPACE_NOT_FOUND
from .embed import EmbeddingError, EmbeddingInputTooLong
from .lexical import LexicalQueryError
from .anchors import InvalidAnchors, MAX_ANCHORS
from .maintenance import MaintenanceRejected

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
                            "and the target namespace; reuse candidates only when their meaning fits, otherwise new "
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
                            "Before adding, call 'list_keywords' in semantic mode with your keyword concepts and target namespace; call 'stats' for category_counts "
                            "and reuse an existing keyword/category when semantically appropriate; "
                            "create a new one only when no existing value matches. Pass keywords as an "
                            "array in metadata.keywords — they are content concepts (e.g. entities, "
                            "topics). Pass categories as an array in metadata.categories — memory "
                            "nature/classification, up to " + str(MAX_CATEGORIES) + " values, prefer "
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
                                    "description": "Memory nature/classification, 1-%d values. Prefer the canonical categories, only introduce a new one when none fits: %s. Normalized to lowercase." % (MAX_CATEGORIES, _CATEGORIES_JOINED),
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
                            "You may optionally pass keywords= and/or categories= to pre-filter candidates "
                            "before similarity scoring. Exact index values are used directly; unmatched values "
                            "are semantically resolved to the closest existing values in the same dimension and "
                            "namespace. The response reports the applied mapping in filter_resolution. "
                            "Values within each dimension use OR; keywords and "
                            "categories combine with each other and namespace using AND. "
                            "If no results are found, call 'stats' first to verify the correct namespace, "
                            "then try searching again. If the response has response_code='namespace_not_found', "
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
                            "description": "Restrict the search to memories carrying any of these keywords (OR). Exact values are preferred; natural-language or alternate forms are semantically resolved within the same namespace. Combined with categories and namespace using AND.",
                        },
                        "categories": {
                            "type": "array",
                            "maxItems": 20,
                            "items": {"type": "string"},
                            "description": "Restrict the search to memories carrying any of these categories (OR). Exact values are preferred; natural-language or alternate forms are semantically resolved within the same namespace. Combined with keywords and namespace using AND.",
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
                            "versions. Use this when the query is likely to match an anchor "
                            "exactly (e.g. a class name or ticket id stored as an anchor). "
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
                            "Accepts a single ID or an array of IDs. "
                            "Returns the complete text of each found memory plus the list of IDs that "
                            "do not exist (partial success). Use this when a search_semantic/search_meta summary looks "
                            "relevant and you need the full original content.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "memory_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Array of memory IDs to fetch full content for (a single ID also works)",
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
                                    "description": "Memory nature/classification, 1-%d values. Prefer the canonical categories, only introduce a new one when none fits: %s. Normalized to lowercase. Empty array clears." % (MAX_CATEGORIES, _CATEGORIES_JOINED),
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
                description="Soft-delete a memory by ID. It is removed from search results but can technically be restored via force_sync.",
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
                            "'category_counts' (the canonical category vocabulary — with the seed values "
                            "always listed, count 0 when unused; reuse these up to 3 values per memory), "
                            "and 'server_time' (current UTC time). Check 'server_time' first when a user asks "
                            "a time-based question (e.g. \"what did we do last week / this month\") so you can "
                            "compute the correct updated_after/updated_before values for the 'search_meta' tool. The "
                            "default namespace is set via MEMORY_DEFAULT_NAMESPACE env var.",
                inputSchema={"type": "object", "properties": {}},
            ),
            types.Tool(
                name="report",
                description="Report how helpful a previous search's results were for the current "
                            "question or context. You must call this AFTER using results from "
                            "search_semantic/search_meta — pass the search_id from that search's "
                            "response plus ratings per memory id. rating is an integer from -10 to 10: "
                            "positive = helpful (larger = more useful), negative = misleading/interference "
                            "(more negative = stronger interference), 0 = neither helpful nor harmful. "
                            "You may rate any subset of the returned ids. The feedback is recorded for "
                            "later search-quality analysis and takes one tool call — do not skip it.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "search_id": {"type": "string", "description": "The search_id from a previous search_semantic/search_meta response"},
                        "ratings": {
                            "type": "array",
                            "description": "Per-memory feedback. Rating -10..10 (out-of-range values are clamped).",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "m_id": {"type": "string", "description": "Memory id from the search results"},
                                    "rating": {"type": "integer", "description": "-10..10; positive helpful, negative interfering, 0 neutral"},
                                },
                                "required": ["m_id", "rating"],
                            },
                        },
                    },
                    "required": ["search_id", "ratings"],
                },
            ),
        ])

    async def handle_call_tool(self, name: str, arguments: dict) -> types.CallToolResult:
        if self.runtime is None:
            return await self._handle_call_tool_unchecked(name, arguments)
        try:
            with self.runtime.maintenance.access(write=name in ("add", "update", "delete")):
                return await self._handle_call_tool_unchecked(name, arguments)
        except MaintenanceRejected as exc:
            data = {"response_code": exc.code, "detail": str(exc)}
            hint = "Maintenance is in progress. Retry this operation later."
            self.audit.record(name, arguments, data, 0.0, response_code=exc.code,
                              context=self._audit_context())
            return _ok(data, hint)

    async def _handle_call_tool_unchecked(self, name: str, arguments: dict) -> types.CallToolResult:
        logger.info("Tool call: %s %s", name, arguments)
        start = time.perf_counter()
        context = self._audit_context()
        memory_refs = []
        try:
            if self.runtime is not None and self.runtime.state != "running":
                raise RuntimeError("service is closing; no new work accepted")
            if name == "add":
                data = self.service.memory_add(arguments["text"], arguments.get("metadata"),
                                  arguments.get("namespace"), arguments.get("summary"),
                                  arguments.get("confirm_codes"))
                hint = _TOOL_HINTS["add"]
                if data.get("categories_dropped"):
                    hint += (" You provided more than %d categories; only the first %d were kept — "
                             "next time pass at most %d in metadata.categories."
                             % (MAX_CATEGORIES, MAX_CATEGORIES, MAX_CATEGORIES))
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
                hint = _TOOL_HINTS["search_semantic"]
            elif name == "search_lexical":
                items = _prepare_for_llm(self.service.memory_search_lexical(
                    arguments["query"], int(arguments.get("k", 10)),
                    arguments.get("namespace")))
                data = _wrap_search_lexical(items)
                hint = _TOOL_HINTS["search_lexical"]
            elif name == "search_anchor":
                items = _prepare_for_llm(self.service.memory_search_anchor(
                    arguments["query"], int(arguments.get("k", 10)),
                    arguments.get("namespace")))
                data = _wrap_search_anchor(items)
                hint = _TOOL_HINTS["search_anchor"]
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
                hint = _TOOL_HINTS["search_meta"]
            elif name == "get_details":
                data = self.service.memory_get_details(arguments["memory_ids"])
                memory_refs = [{"id": item.get("id"), "namespace": item.get("namespace")}
                               for item in data.get("found", [])[:100]]
                hint = _TOOL_HINTS["get_details"]
            elif name == "update":
                old = self.service.storage.get(arguments["memory_id"])
                data = self.service.memory_update(arguments["memory_id"], arguments.get("text"),
                                     arguments.get("metadata"), arguments.get("namespace"),
                                     arguments.get("summary"), arguments.get("confirm_codes"))
                new = self.service.storage.get(arguments["memory_id"])
                memory_refs = [{"id": arguments["memory_id"],
                                "namespace_before": old.get("namespace") if old else None,
                                "namespace": new.get("namespace") if new else None}]
                hint = _TOOL_HINTS["update"]
                if data.get("categories_dropped"):
                    hint += (" You provided more than %d categories; only the first %d were kept — "
                             "next time pass at most %d in metadata.categories."
                             % (MAX_CATEGORIES, MAX_CATEGORIES, MAX_CATEGORIES))
            elif name == "delete":
                data = self.service.memory_delete(arguments["memory_id"])
                hint = _TOOL_HINTS["delete"]
            elif name == "list_keywords":
                data = self.service.memory_list_keywords(**arguments)
                data["keyword_query_id"] = "kq_" + secrets.token_hex(8)
                hint = _TOOL_HINTS["list_keywords"]
            elif name == "stats":
                data = _wrap_stats(self.service.memory_stats())
                hint = _TOOL_HINTS["stats"]
            elif name == "report":
                data = self.service.memory_report(arguments.get("search_id"), arguments.get("ratings"))
                hint = _TOOL_HINTS["report"]
            else:
                raise ValueError(f"Unknown tool: {name}")
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
                    "128 characters) and retry, or omit the field to keep existing anchors.")
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
        if self.runtime is not None:
            with self.runtime.maintenance.access():
                return await self._handle_read_resource_unchecked(uri)
        return await self._handle_read_resource_unchecked(uri)

    async def _handle_read_resource_unchecked(self, uri: str) -> types.ReadResourceResult:
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
    "list_keywords": "Reuse a candidate only if it accurately expresses your concept; score measures similarity, not equivalence. New keywords are allowed when none fits. Counts reflect live memories in the requested namespace. Pass this keyword_query_id to a later add/update/search when it informed your choice. Use browse/literal offset for more results; lower semantic min_score to broaden candidates. No report is needed.",
    "add": _ANCHOR_GUIDANCE + "When setting keywords, first use list_keywords semantic mode in the target namespace to find reusable terms. Memory stored. A supplied summary was saved directly; if it was missing or blank, a deterministic extract from the leading text was stored. If created=false, an identical record already exists and remains untouched — consider 'update' instead. Reuse canonical categories from stats' category_counts (up to 3). An unknown namespace is rejected unless you pass confirm_codes=[\"namespace_not_found\"].",
    "search_semantic": "Results are ranked by relevance (score). Use 'get_details' with an id to read the full original text, then call 'report' with this search_id to rate how helpful each result was (-10..10). filter_resolution shows how exact or semantic keywords/categories constrained candidates before scoring. Values within one dimension use OR, while dimensions combine using AND. If a whole supplied dimension could not be resolved, the result is empty. response_code='namespace_not_found' means the namespace does not exist (empty items is then not a real 'no match').",
    "search_lexical": "Results are exact-term matches ranked by BM25; the score is NOT comparable with semantic scores. Items carry a stored summary or deterministic leading-text extract. Use 'get_details' with an id to read the full original text, then call 'report' with this search_id to rate how helpful each result was (-10..10). response_code='namespace_not_found' means the namespace does not exist; use response_meta.known_namespaces or omit it. If nothing matched, retry with 'search_semantic'.",
    "search_anchor": "Results are anchor matches ranked by BM25; the score is NOT comparable with the other search scores. Items carry a stored summary or deterministic leading-text extract. Use 'get_details' with an id to read the full original text, then call 'report' with this search_id to rate how helpful each result was (-10..10). If nothing matched, retry with 'search_lexical' or 'search_semantic'. response_code='namespace_not_found' means the namespace does not exist; use response_meta.known_namespaces or omit it.",
    "search_meta": "Use 'get_details' with an id to read the full original text, or call 'search_semantic' for fuzzy/topic/keyword lookup. Filter further with keywords= and/or categories=. After using these results, call 'report' with this search_id to rate how helpful each id was (-10..10). response_code='namespace_not_found' means the namespace does not exist; the real available namespaces are in response_meta.known_namespaces.",
    "get_details": "You now have the full text, including metadata.keywords, metadata.categories and metadata.anchors. Pass source_search_id from the originating search to link this lookup in audit. If a memory is relevant, use search_meta(keywords=[...]) or search_meta(categories=[...]) to find related memories.",
    "update": _ANCHOR_GUIDANCE + "When replacing keywords, first use list_keywords semantic mode in the target namespace; other updates need no keyword lookup. Memory updated. A supplied summary was saved directly; changed text without one received a deterministic leading-text extract. Metadata-only and whitespace-equivalent text updates preserve the existing summary. Use 'get_details' with the id to verify the change. Categories (if any) were taken from metadata.categories — see stats' category_counts for the canonical list (up to 3). Moving to an unknown namespace is rejected unless you pass confirm_codes=[\"namespace_not_found\"].",
    "delete": "Memory soft-deleted. It no longer appears in 'search_semantic'/'search_meta'. Call 'stats' to confirm the count.",
    "stats": "Use 'namespace_counts' to pick a namespace, then 'search_meta' or 'search_semantic' within it. Use 'list_keywords' to find existing keywords and 'category_counts' as the canonical category vocabulary (up to 3 per memory) for search_meta/add. Embedding provider/model/profile describe the active comparable vector space. Use 'server_time' for time-based queries.",
    "report": "Feedback recorded. Continue with the task, or run another search if the results were unhelpful.",
}


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
    return "se_" + secrets.token_hex(4)


def _wrap_search_meta(items: list, namespace: str | None, limit: int, offset: int, sort_by: str) -> dict:
    """Attach usage guidance to search_meta results so the LLM knows how to consume them.

    The raw item list must stay an array for the admin UI (/api/memories);
    this wrapper is only applied at the MCP tool boundary.
    """
    scope = f"namespace={namespace!r}" if namespace else "all namespaces"
    note = (
        f"{len(items)} memories (limit={limit}, offset={offset}) from {scope}, "
        f"sorted by {sort_by} descending. Items carry a stored summary or a "
        f"deterministic leading-text extract. To see the complete original content, call 'get_details' "
        f"with the returned ids. After using these results, call 'report' with "
        f"this search_id and rate how helpful each id was for the current question "
        f"(-10..10, may rate a subset)."
    )
    return {"search_id": _new_search_id(), "note": note, "total": len(items), "items": items}


def _wrap_search_semantic(search_result: dict) -> dict:
    """Attach usage guidance to search_semantic results (same rationale as _wrap_search_meta)."""
    # Accept a bare list for compatibility with small integrations/tests that
    # use this response helper directly.
    if isinstance(search_result, list):
        search_result = {"items": search_result, "filter_resolution": None}
    items = search_result["items"]
    note = (
        f"{len(items)} results, ranked by semantic similarity (score). Items carry "
        f"a stored summary or deterministic leading-text extract. To see the complete original "
        f"content, call 'get_details' with the returned ids. After using these "
        f"results, call 'report' with this search_id and rate how helpful each id "
        f"was for the current question (-10..10, may rate a subset)."
    )
    result = {"search_id": _new_search_id(), "note": note,
              "total": len(items), "items": items}
    if search_result.get("filter_resolution") is not None:
        result["filter_resolution"] = search_result["filter_resolution"]
    return result


def _wrap_search_lexical(items: list) -> dict:
    """Attach usage guidance to search_lexical results (same rationale as search_meta)."""
    note = (
        f"{len(items)} results, ranked by lexical term match (BM25). Items carry a "
        f"stored summary or deterministic leading-text extract. Scores are not comparable "
        f"with semantic scores. To see the complete original content, call 'get_details' with "
        f"the returned ids. After using these results, call 'report' with this search_id and "
        f"rate how helpful each id was for the current question (-10..10, may rate a subset)."
    )
    return {"search_id": _new_search_id(), "note": note, "total": len(items), "items": items}


def _wrap_search_anchor(items: list) -> dict:
    """Attach usage guidance to search_anchor results (same rationale as search_meta)."""
    note = (
        f"{len(items)} results, ranked by anchor match (BM25). Items carry a stored "
        f"summary or deterministic leading-text extract. Scores are not comparable with "
        f"the other search scores. To see the complete original content, call 'get_details' "
        f"with the returned ids. After using these results, call 'report' with this "
        f"search_id and rate how helpful each id was for the current question "
        f"(-10..10, may rate a subset)."
    )
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
            runtime.start()
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
    parser = argparse.ArgumentParser(description="Memory MCP Server")
    parser.add_argument("--port", type=int, default=None,
                        help="Port for HTTP mode (legacy SSE + Streamable HTTP)")
    args = parser.parse_args(argv)
    runtime = create_runtime(Settings.from_env())
    if args.port is None:
        await serve_stdio(runtime)
    else:
        app = create_http_app(runtime, port=args.port)
        config = uvicorn.Config(app, host="0.0.0.0", port=args.port, log_level="info")
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

"""MCP tool definitions.

Tool schema and description data live here, apart from the transport lifecycle
in :mod:`app.server`. ``build_tools`` is the single source of truth for
``tools/list``; :mod:`app.transport.tools` binds each definition to a handler and policy.
"""

import mcp.types as types

from app.domain.anchors import MAX_ANCHORS
from app.domain.categories import CATEGORY_VOCABULARY, MAX_CATEGORIES
from app.embedding.index_value_resolver import MAX_FILTER_VALUES
from app.transport.report_feedback import (FEEDBACK_PURPOSES, FEEDBACK_SOURCES,
                              MAX_REPORT_RATINGS)



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



def build_tools() -> list[types.Tool]:
    return [
            types.Tool(
                name="list_keywords",
                description="Browse the complete in-use keyword vocabulary or find existing words by meaning. "
                            "Before setting keywords in add/update, use semantic mode with 1–20 concepts in queries "
                            "and the target namespace (omit namespace when creating a new one); reuse candidates only when their meaning fits, otherwise new "
                            "words are allowed. Similarity is not synonym equivalence. Metadata updates without "
                            "keywords need no lookup. Literal mode matches one substring; browse takes no queries. "
                            "Counts are scoped to live memories in namespace (omit for all). Semantic results include "
                            "score and count, sorted by score; exact hits do not suppress related candidates. "
                            "An optional categories filter (exact, OR) restricts results to keywords used by memories "
                            "carrying any of those categories; it combines with namespace using AND. "
                            "No search_id/report feedback is required. Pass the returned keyword_query_id to a later "
                            "add/update/search when it informed your keyword choice. Unknown namespace returns namespace_not_found.",
                inputSchema={"type": "object", "additionalProperties": False, "properties": {
                    "namespace": {"type": "string", "description": "Optional live namespace; omitted means all."},
                    "mode": {"type": "string", "enum": ["browse", "literal", "semantic"], "default": "browse"},
                    "queries": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 20,
                                "description": "One nonblank string for literal, 1–20 for semantic; omit for browse."},
                    "categories": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_FILTER_VALUES,
                                   "description": "Optional; restrict results to keywords used by live memories carrying any of these categories (exact, OR). Combines with namespace using AND."},
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
                            "use 'search_meta' with sort_by='updated_at'. For conceptual questions within "
                            "a time window, use updated_after/updated_before here: bounds filter updated_at "
                            "before similarity scoring and Top-K. Use stats.server_time to compute relative windows. "
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
                        "updated_after": {"type": "string", "description": "Optional timezone-aware ISO 8601 timestamp. Restrict candidates to memories updated strictly after this moment before ranking. Omit or leave blank for no lower bound."},
                        "updated_before": {"type": "string", "description": "Optional timezone-aware ISO 8601 timestamp. Restrict candidates to memories updated strictly before this moment before ranking. Must be later than updated_after when both are supplied."},
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
                        "updated_after": {"type": "string", "description": "ISO8601 timestamp (e.g. '2026-07-01T00:00:00+00:00'). Only return memories updated after this moment (exclusive, microsecond precision). A timezone is required; omit or leave blank for no lower bound. If the user asks for a relative window (e.g. 'last week', 'this month') and you are unsure of the current date, call 'stats' first to get its 'server_time', then compute this value."},
                        "updated_before": {"type": "string", "description": "ISO8601 timestamp (e.g. '2026-07-01T00:00:00+00:00'). Only return memories updated before this moment (exclusive, microsecond precision). A timezone is required; omit or leave blank for no upper bound. Must be later than updated_after when both are supplied. If the user asks for a relative window (e.g. 'last week', 'this month') and you are unsure of the current date, call 'stats' first to get its 'server_time', then compute this value."},
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
    ]

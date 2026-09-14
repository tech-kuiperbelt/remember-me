"""Semantic resolution of keyword/category filters to stored index values."""

from __future__ import annotations

MAX_FILTER_VALUES = 20


class SemanticIndexUnsupported(RuntimeError):
    """The selected storage backend cannot resolve an unmatched index value."""


class IndexValueResolver:
    """Resolve normalized filter values without mixing their scores into memory ranking."""

    def __init__(self, storage, embed_query, embed_documents, top_k: int = 3,
                 min_score: float = 0.65, max_gap: float = 0.05):
        self.storage = storage
        self.embed_query = embed_query
        self.embed_documents = embed_documents
        self.top_k = max(1, int(top_k))
        self.min_score = max(-1.0, min(1.0, float(min_score)))
        self.max_gap = max(0.0, float(max_gap))

    def prepare_embeddings(self, metadata: dict | None) -> dict:
        """Generate document vectors for new/missing index values before a write."""
        if not callable(getattr(type(self.storage), "missing_index_value_embeddings", None)):
            return {}
        missing = self.storage.missing_index_value_embeddings(metadata)
        if not missing:
            return {}
        vectors = self.embed_documents([value for _dimension, value in missing])
        if len(vectors) != len(missing):
            raise ValueError(
                f"provider returned {len(vectors)} index vectors for {len(missing)} values"
            )
        return {key: vector for key, vector in zip(missing, vectors)}

    def resolve(self, filters: dict[str, list[str] | None],
                namespace: str | None = None) -> tuple[dict[str, list[str]], dict]:
        applied = {}
        detail = {
            "config": {
                "top_k": self.top_k,
                "min_score": self.min_score,
                "max_gap": self.max_gap,
            }
        }
        for dimension, inputs in filters.items():
            if not inputs:
                continue
            resolved_values = []
            records = []
            exact = self._exact_values(dimension, inputs, namespace)
            for value in inputs:
                if value in exact:
                    resolved_values.append(value)
                    records.append({
                        "input": value,
                        "match_type": "exact",
                        "matches": [{"value": value, "score": None, "accepted": True}],
                    })
                    continue
                matches = self._semantic_values(dimension, value, namespace)
                if matches:
                    resolved_values.extend(match["value"] for match in matches)
                    records.append({"input": value, "match_type": "semantic",
                                    "matches": matches})
                else:
                    records.append({"input": value, "match_type": "none", "matches": []})
            # Preserve order while deduplicating expansions from multiple inputs.
            applied[dimension] = list(dict.fromkeys(resolved_values))
            detail[dimension] = records
        return applied, detail

    def _exact_values(self, dimension: str, values: list[str],
                      namespace: str | None) -> set[str]:
        if callable(getattr(type(self.storage), "exact_index_values", None)):
            return self.storage.exact_index_values(dimension, values, namespace)
        # Test doubles and legacy custom backends preserve the old exact-filter
        # behavior without accidentally invoking a dynamically-created mock method.
        return set(values)

    def _semantic_values(self, dimension: str, value: str,
                         namespace: str | None) -> list[dict]:
        if not callable(getattr(type(self.storage), "semantic_index_values", None)):
            raise SemanticIndexUnsupported(
                "semantic keyword/category resolution is supported only by SQLite/dual storage; "
                f"'{value}' has no exact {dimension} match"
            )
        query_embedding = self.embed_query(value)
        candidates = self.storage.semantic_index_values(
            dimension, query_embedding, namespace
        )[:self.top_k]
        if not candidates or candidates[0]["score"] < self.min_score:
            return []
        best = candidates[0]["score"]
        return [
            {"value": candidate["value"], "score": candidate["score"], "accepted": True}
            for candidate in candidates
            if candidate["score"] >= self.min_score
            and best - candidate["score"] <= self.max_gap
        ]


def resolver_for_configured_profile(storage):
    """Build a write resolver for an offline migration, or None for legacy stores."""
    if not callable(getattr(type(storage), "missing_index_value_embeddings", None)):
        return None
    from .embed import create_embedder

    provider = create_embedder()
    profile = storage.get_stats().get("embedding_profile") or {}
    active = profile.get("active")
    if not active:
        return None
    if provider.spec.profile_id != active:
        raise RuntimeError(
            "configured embedding provider does not match the active database profile: "
            f"configured={provider.spec.profile_id}, active={active}"
        )
    return IndexValueResolver(storage, provider.embed_query, provider.embed_documents)

"""Versioned binary encoding for dense embedding vectors.

The legacy database stored ``pickle.dumps(list[float])``.  That representation
uses roughly nine bytes per component and ties the persistent format to Python.
New writes use a small self-describing header followed by a contiguous
little-endian NumPy array.  Legacy pickle remains readable during migration.
"""

from __future__ import annotations

import pickle
import struct
from dataclasses import dataclass

import numpy as np


MAGIC = b"RMEV"
VERSION = 1
_HEADER = struct.Struct("<4sBBHI")
_DTYPES = {
    "float16": (1, np.dtype("<f2")),
    "float32": (2, np.dtype("<f4")),
}
_DTYPES_BY_CODE = {code: (name, dtype) for name, (code, dtype) in _DTYPES.items()}
FLAG_NORMALIZED = 1


class EmbeddingCodecError(ValueError):
    """The embedding BLOB is invalid or uses an unsupported format."""


@dataclass(frozen=True)
class EmbeddingBlobInfo:
    format: str
    version: int
    dtype: str
    dimension: int
    normalized: bool
    byte_length: int


def encode_embedding(vector, dtype: str = "float16", normalized: bool = False) -> bytes:
    """Encode one dense vector using the current portable BLOB format."""
    try:
        dtype_code, numpy_dtype = _DTYPES[dtype]
    except KeyError as exc:
        raise EmbeddingCodecError(f"unsupported embedding dtype: {dtype}") from exc
    array = np.asarray(vector, dtype=numpy_dtype)
    if array.ndim != 1 or not array.size:
        raise EmbeddingCodecError("embedding must be a non-empty one-dimensional vector")
    if not np.isfinite(array).all():
        raise EmbeddingCodecError("embedding contains non-finite values")
    flags = FLAG_NORMALIZED if normalized else 0
    header = _HEADER.pack(MAGIC, VERSION, dtype_code, flags, int(array.size))
    return header + array.tobytes(order="C")


def decode_embedding(blob: bytes | bytearray | memoryview | None) -> list[float]:
    """Decode the current format or a trusted legacy pickle BLOB."""
    if not blob:
        return []
    raw = bytes(blob)
    if raw.startswith(MAGIC):
        if len(raw) < _HEADER.size:
            raise EmbeddingCodecError("truncated embedding header")
        magic, version, dtype_code, _flags, dimension = _HEADER.unpack_from(raw)
        if magic != MAGIC or version != VERSION:
            raise EmbeddingCodecError(f"unsupported embedding format version: {version}")
        try:
            _dtype_name, numpy_dtype = _DTYPES_BY_CODE[dtype_code]
        except KeyError as exc:
            raise EmbeddingCodecError(f"unsupported embedding dtype code: {dtype_code}") from exc
        expected = _HEADER.size + dimension * numpy_dtype.itemsize
        if dimension <= 0 or len(raw) != expected:
            raise EmbeddingCodecError(
                f"embedding length mismatch: dimension={dimension}, bytes={len(raw)}"
            )
        return np.frombuffer(raw, dtype=numpy_dtype, offset=_HEADER.size).astype(
            np.float32, copy=False
        ).tolist()

    try:
        value = pickle.loads(raw)
    except Exception as exc:
        raise EmbeddingCodecError("unknown or corrupt embedding BLOB") from exc
    array = np.asarray(value)
    if array.ndim != 1:
        raise EmbeddingCodecError("legacy embedding is not one-dimensional")
    return [float(item) for item in value]


def inspect_embedding_blob(blob: bytes | bytearray | memoryview | None) -> EmbeddingBlobInfo:
    """Return lightweight format metadata without exposing vector values."""
    if not blob:
        return EmbeddingBlobInfo("empty", 0, "unknown", 0, False, 0)
    raw = bytes(blob)
    if raw.startswith(MAGIC):
        if len(raw) < _HEADER.size:
            raise EmbeddingCodecError("truncated embedding header")
        _magic, version, dtype_code, flags, dimension = _HEADER.unpack_from(raw)
        if version != VERSION:
            raise EmbeddingCodecError(f"unsupported embedding format version: {version}")
        try:
            dtype_name, numpy_dtype = _DTYPES_BY_CODE[dtype_code]
        except KeyError as exc:
            raise EmbeddingCodecError(f"unsupported embedding dtype code: {dtype_code}") from exc
        expected = _HEADER.size + dimension * numpy_dtype.itemsize
        if dimension <= 0 or len(raw) != expected:
            raise EmbeddingCodecError(
                f"embedding length mismatch: dimension={dimension}, bytes={len(raw)}"
            )
        return EmbeddingBlobInfo(
            "rmev", version, dtype_name, dimension,
            bool(flags & FLAG_NORMALIZED), len(raw),
        )
    vector = decode_embedding(raw)
    return EmbeddingBlobInfo("pickle", 0, "python-float", len(vector), False, len(raw))

#!/usr/bin/env python3
"""Verify the locked production venv; optionally infer from an existing cache.

Does not start a server or open a business database. Model verification forbids
socket connections and uses local_files_only, never downloading model weights.
"""
from __future__ import annotations

import argparse
import importlib.metadata as metadata
import json
import math
import os
from pathlib import Path
import platform
import re
import sys
import sysconfig
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "requirements/docker-cpu-py312-linux-amd64.lock"
MODEL = "BAAI/bge-m3"
REVISION = "5617a9f61b028005a4858fdac845db406aefb181"


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def lock_versions():
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name, parse_wheel_filename

    expected = {}
    for line in LOCK.read_text().splitlines():
        if not line or line.startswith(("#", " ", "--")):
            continue
        requirement = Requirement(line.removesuffix("\\").strip())
        if requirement.url:
            url = urlsplit(requirement.url)
            require(url.hostname == "download-r2.pytorch.org" and "/whl/cpu/" in url.path,
                    "Unexpected direct wheel source")
            name, version, _, _ = parse_wheel_filename(unquote(Path(url.path).name))
            require(canonicalize_name(requirement.name) == name == "torch", "Unexpected wheel")
            expected[name] = str(version)
        else:
            require(re.fullmatch(r"==[^*,]+", str(requirement.specifier)) is not None,
                    f"Requirement is not pinned: {line}")
            expected[canonicalize_name(requirement.name)] = str(requirement.specifier)[2:]
    require(bool(expected), "Empty production lock")
    return expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-cache", type=Path, help="Existing HF cache containing models--BAAI--bge-m3")
    parser.add_argument("--expected-profile-id", help="Optional known profile identity; never reads its database")
    args = parser.parse_args()
    require(sys.version_info[:2] == (3, 12), "CPython 3.12 is required")
    require(platform.python_implementation() == "CPython", "CPython is required")
    require(sys.platform == "linux" and platform.machine() in ("x86_64", "AMD64"),
            "This lock targets Linux AMD64")
    libc, libc_version = platform.libc_ver()
    require(libc == "glibc" and tuple(map(int, libc_version.split(".")[:2])) >= (2, 34),
            "This lock requires glibc >=2.34")
    require(sys.prefix != sys.base_prefix, "Use an isolated virtual environment")

    from packaging.utils import canonicalize_name
    expected = lock_versions()
    installed = {canonicalize_name(d.metadata["Name"]): d.version for d in metadata.distributions()}
    forbidden = sorted(name for name in installed if name.startswith(("nvidia-", "cuda-", "onnx", "fastembed"))
                       or "triton" in name or name == "pytest")
    require(not forbidden, f"Non-production packages present: {forbidden}")
    require(all(installed.get(name) == version for name, version in expected.items()),
            "Installed versions differ from the production lock")
    require(not (installed.keys() - expected.keys() - {"pip"}), "Unexpected packages outside the production lock")

    import torch
    require("+cpu" in metadata.version("torch"), "Torch is not an official CPU build")
    require(torch.version.cuda is None and getattr(torch.version, "hip", None) is None,
            "GPU Torch build detected")
    direct_url = json.loads(metadata.distribution("torch").read_text("direct_url.json") or "{}")
    require(direct_url.get("url", "").startswith("https://download-r2.pytorch.org/whl/cpu/"),
            "Torch install provenance does not point to the official CPU wheel")
    import sentence_transformers
    import sentencepiece
    import tokenizers
    import transformers
    import google.protobuf
    sys.path.insert(0, str(ROOT))
    import app.server
    from app.embedding.embed import SentenceTransformersEmbedder

    result = {"python": platform.python_version(), "platform": platform.platform(),
              "locked_packages": len(expected), "torch": torch.__version__,
              "torch_cuda": torch.version.cuda, "torch_hip": getattr(torch.version, "hip", None),
              "torch_source": direct_url, "sentence_transformers": sentence_transformers.__version__,
              "transformers": transformers.__version__, "forbidden_packages": forbidden,
              "model_validation": "not_requested"}
    if args.model_cache:
        cache = args.model_cache.resolve()
        hub = cache / "models--BAAI--bge-m3"
        require((hub / "refs/main").read_text() == REVISION,
                "Unexpected cached BGE-M3 revision/ref bytes (no trailing newline allowed)")
        snapshot = hub / "snapshots" / REVISION
        for filename in ("pytorch_model.bin", "modules.json", "config.json", "sentencepiece.bpe.model",
                         "tokenizer_config.json", "1_Pooling/config.json"):
            require((snapshot / filename).is_file(), f"Incomplete model snapshot: {filename}")
        os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_XET="1")

        def no_network(event, arguments):
            if event in ("socket.connect", "socket.getaddrinfo"):
                raise RuntimeError("Model validation attempted network access")

        sys.addaudithook(no_network)
        torch.set_num_threads(2)
        slow = transformers.AutoTokenizer.from_pretrained(MODEL, cache_dir=str(cache),
                                                         local_files_only=True, use_fast=False)
        require(bool(slow.encode("Remember Me 中文依赖验证")), "SentencePiece tokenizer failed")
        provider = SentenceTransformersEmbedder(MODEL, cache_dir=str(cache), device="cpu",
                                               offline=True, auto_download=False)
        try:
            modules = [type(module).__name__ for module in provider._model]
            require(modules == ["Transformer", "Pooling", "Normalize"],
                    f"Unexpected model modules (possible silent fallback): {modules}")
            pooling = provider._model[1].get_config_dict()
            require(pooling.get("pooling_mode") == "cls" or pooling.get("pooling_mode_cls_token") is True,
                    f"Expected BGE-M3 CLS pooling, got {pooling}")
            result["model_modules"] = modules
            result["pooling"] = pooling
            provider.warmup()
            vectors = provider.embed_documents(["Remember Me CPU dependency verification.", "中文语义检索验证。"])
            require(len(vectors) == 2 and all(len(v) == 1024 for v in vectors), "Expected 1024 dimensions")
            norms = [math.sqrt(sum(x * x for x in v)) for v in vectors]
            require(all(math.isfinite(n) and abs(n - 1) < 1e-4 for n in norms), "Invalid normalized vectors")
            require(provider.spec.model == MODEL, "Physical revision changed logical model identity")
            if args.expected_profile_id:
                require(provider.spec.profile_id == args.expected_profile_id, "Profile identity changed")
            result.update(model_validation="offline_inference_passed", model_revision=REVISION,
                          profile=provider.spec.as_dict(), vector_dimensions=[len(v) for v in vectors],
                          vector_norms=norms, slow_tokenizer=type(slow).__name__)
        finally:
            provider.close()
    else:
        require(not args.expected_profile_id, "--expected-profile-id requires --model-cache")
    site = Path(sysconfig.get_path("purelib"))
    result["site_packages_bytes"] = sum(p.stat().st_size for p in site.rglob("*") if p.is_file())
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

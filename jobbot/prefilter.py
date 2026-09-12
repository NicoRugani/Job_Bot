"""Free filtering and ranking that happens before any LLM call."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import PrefilterConfig, Roles


@dataclass
class TitleVerdict:
    group: str | None
    weight: float = 0.0
    reason: str | None = None

    @property
    def keep(self) -> bool:
        return self.group is not None


class TitleClassifier:
    def __init__(self, roles: Roles):
        flags = re.IGNORECASE
        # Stable sort keeps config order among equal weights.
        self.groups = sorted(
            ((name, group, [re.compile(p, flags) for p in group.patterns]) for name, group in roles.groups.items()),
            key=lambda item: -item[1].weight,
        )
        self.hard = [re.compile(p, flags) for p in roles.hard_exclude]
        self.soft = [re.compile(p, flags) for p in roles.soft_exclude]

    def classify(self, title: str) -> TitleVerdict:
        for pattern in self.hard:
            if pattern.search(title):
                return TitleVerdict(None, reason=f"title excluded: {pattern.pattern}")
        match = next(((name, g) for name, g, pats in self.groups if any(p.search(title) for p in pats)), None)
        can_override = match is not None and match[1].overrides_soft_exclude
        for pattern in self.soft:
            if pattern.search(title) and not can_override:
                return TitleVerdict(None, reason=f"title excluded: {pattern.pattern}")
        if match is None:
            return TitleVerdict(None, reason="title is not a target role")
        return TitleVerdict(match[0], match[1].weight)


class KeywordScorer:
    def __init__(self, keywords: list[str], saturation: int):
        unique = sorted({k.strip().lower() for k in keywords if k and k.strip()})
        self.saturation = max(1, saturation)
        self.patterns = [(k, re.compile(r"(?<![\w])" + re.escape(k) + r"(?![\w#+])")) for k in unique]

    def score(self, text: str) -> tuple[float, list[str]]:
        low = text.lower()
        hits = [k for k, pattern in self.patterns if pattern.search(low)]
        return min(1.0, len(hits) / self.saturation) * 100, hits


class Embedder:
    """Local ONNX sentence embeddings via fastembed (model downloads once, ~70MB)."""

    def __init__(self, model_name: str, cache_dir: Path | None = None):
        from fastembed import TextEmbedding

        self.model = TextEmbedding(model_name=model_name, cache_dir=str(cache_dir) if cache_dir else None)

    def embed(self, texts: list[str]) -> np.ndarray:
        vectors = np.array(list(self.model.embed(texts)), dtype=np.float32)
        return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def chunk_text(text: str, size: int = 1200, overlap: int = 200) -> list[str]:
    text = text.strip()
    if len(text) <= size:
        return [text]
    step = size - overlap
    return [text[i : i + size] for i in range(0, len(text) - overlap, step)]


class Prescorer:
    def __init__(self, cfg: PrefilterConfig, keywords: list[str], profile_text: str, cache_dir: Path | None = None):
        self.cfg = cfg
        self.keywords = KeywordScorer(keywords, cfg.keyword_saturation)
        self.embedder = Embedder(cfg.embedding_model, cache_dir) if cfg.use_embeddings else None
        self.profile_vector = self.embedder.embed([profile_text])[0] if self.embedder else None

    def similarity(self, title: str, body: str) -> float:
        """Best chunk similarity between the posting and the profile, mapped to 0-100."""
        assert self.embedder is not None
        chunks = [f"{title}\n{chunk}" for chunk in chunk_text(body)] if body else [title]
        best = float(np.max(self.embedder.embed(chunks) @ self.profile_vector))
        span = self.cfg.similarity_ceiling - self.cfg.similarity_floor
        return float(np.clip((best - self.cfg.similarity_floor) / span, 0.0, 1.0) * 100)

    def score(self, title: str, body: str, role_weight: float) -> float:
        keyword, _ = self.keywords.score(f"{title}\n{body}")
        if self.embedder is None:
            return round(role_weight * keyword, 1)
        combined = self.cfg.keyword_weight * keyword + self.cfg.embedding_weight * self.similarity(title, body)
        return round(role_weight * combined, 1)

"""Load topic-search queries while preserving their sampling provenance."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SearchQuery:
    category: str
    query_type: str
    query: str


def load_search_queries(
    path: Path,
    selected_categories: list[str] | None = None,
    manual_keywords: list[str] | None = None,
) -> tuple[dict[str, object], list[SearchQuery]]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Khong doc duoc file tu khoa {path}: {exc}") from exc
    if not isinstance(config, dict) or not isinstance(config.get("categories"), dict):
        raise ValueError("File tu khoa phai co truong categories dang object.")
    categories: dict[str, object] = config["categories"]
    requested = selected_categories or list(categories)
    unknown = [name for name in requested if name not in categories]
    if unknown:
        raise ValueError("Category khong ton tai: " + ", ".join(unknown))

    queries: list[SearchQuery] = []
    seen: set[tuple[str, str]] = set()
    fields = (("neutral_keywords", "neutral"), ("toxic_enriched_keywords", "toxic_enriched"), ("hashtags", "hashtag"))
    for category in requested:
        values = categories[category]
        if not isinstance(values, dict):
            raise ValueError(f"Category {category!r} khong dung dinh dang.")
        for field, query_type in fields:
            terms = values.get(field, [])
            if not isinstance(terms, list):
                raise ValueError(f"{category}.{field} phai la list.")
            for term in terms:
                if not isinstance(term, str) or not (query := term.strip()):
                    continue
                if query_type == "hashtag":
                    query = "#" + query.lstrip("#")
                key = (category, query.casefold())
                if key not in seen:
                    seen.add(key)
                    queries.append(SearchQuery(category, query_type, query))
    for keyword in manual_keywords or []:
        if keyword.strip():
            queries.append(SearchQuery("Manual", "manual", keyword.strip()))
    return config, queries

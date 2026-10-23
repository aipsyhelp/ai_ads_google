"""Does a negative keyword suppress a positive keyword?

Shared by the delivery audit (which reports groups that cannot serve) and the
semantic expander (which must never propose a keyword its own campaign already
blocks). One implementation so both agree on what "blocked" means.
"""
from __future__ import annotations


def phrase_blocks(negative: str, keyword: str) -> bool:
    """True when the negative's word sequence appears inside the keyword."""
    neg_words = negative.lower().split()
    kw_words = keyword.lower().split()
    if not neg_words or len(neg_words) > len(kw_words):
        return False
    for start in range(len(kw_words) - len(neg_words) + 1):
        if kw_words[start : start + len(neg_words)] == neg_words:
            return True
    return False


def blocks(negative: str, match_type: str, keyword: str) -> bool:
    neg = negative.strip().lower()
    kw = keyword.strip().lower()
    if not neg or not kw:
        return False
    if match_type == "EXACT":
        return neg == kw
    if match_type == "PHRASE":
        return phrase_blocks(neg, kw)
    # BROAD negatives block when every negative token appears in the keyword,
    # in any order.
    kw_words = set(kw.split())
    return all(token in kw_words for token in neg.split())


def blocked_by(
    keyword: str,
    negatives: list[dict[str, str]],
) -> list[str]:
    """Return the negative texts that suppress `keyword`."""
    return [
        negative["text"]
        for negative in negatives
        if blocks(negative.get("text", ""), negative.get("match_type", "BROAD"), keyword)
    ]

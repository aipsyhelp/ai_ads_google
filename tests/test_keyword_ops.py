from types import SimpleNamespace

from ads_ai_agents.intelligence.keyword_ops import (
    drop_positive_single_word_creates,
    is_positive_single_word_create,
)


def _create(text: str, *, negative: bool = False):
    return SimpleNamespace(
        create=SimpleNamespace(
            negative=negative,
            keyword=SimpleNamespace(text=text),
        ),
        update=SimpleNamespace(resource_name=""),
    )


def test_positive_single_word_is_blocked_for_ad_grants() -> None:
    assert is_positive_single_word_create(_create("депресія"))
    assert is_positive_single_word_create(_create("self-help"))


def test_multi_word_and_negative_single_word_are_allowed() -> None:
    assert not is_positive_single_word_create(_create("підтримка при стресі"))
    assert not is_positive_single_word_create(_create("психолог", negative=True))


def test_drop_records_warning() -> None:
    warnings: list[dict[str, str]] = []
    safe = drop_positive_single_word_creates(
        [_create("депресія"), _create("емоційна підтримка")],
        warnings,
    )
    assert len(safe) == 1
    assert warnings[0]["op"] == "keyword_blocked"

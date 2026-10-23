from ads_ai_agents.intelligence.semantic_expansion import (
    MAX_KEYWORD_WORDS,
    DemandSignal,
    campaign_locale,
    competitor_brands,
    coverage_report,
    cross_locale_signals,
    detect_locale,
    is_clinical_assessment,
    is_expandable_term,
    match_group,
    plan_expansion,
    proven_families,
    qualifies,
    score_signal,
    theme_overlap,
)


def _group(
    name: str,
    locale: str,
    *,
    resource_name: str | None = None,
    terms: set[str] | None = None,
    negatives: list[dict] | None = None,
) -> dict:
    return {
        "resource_name": resource_name or f"customers/1/adGroups/{abs(hash(name)) % 1000}",
        "name": name,
        "campaign_id": "1",
        "campaign_name": f"Ser / www.example.com / Articles / {locale} / Brand_Search",
        "locale": locale,
        "terms": terms or set(),
        "live_terms": terms or set(),
        "negatives": negatives or [],
    }


def test_detect_locale_separates_the_three_scripts():
    assert detect_locale("вигорання на роботі") == "UA"
    assert detect_locale("як подолати тривогу") == "UA"
    assert detect_locale("angst selbsthilfe") == "DE"
    assert detect_locale("stress bewältigen ohne medikamente") == "DE"
    assert detect_locale("anxiety self help") == "EN"
    assert detect_locale("") is None


def test_russian_queries_are_not_mistaken_for_ukrainian():
    """The account receives Russian queries; they must not enter UA campaigns."""
    assert detect_locale("депрессия симптомы у мужчин") == "RU"
    assert detect_locale("панические атаки лечение") == "RU"
    assert detect_locale("как избавиться от тревоги") == "RU"
    # Cyrillic that proves neither language is left unidentified, not guessed.
    assert detect_locale("тест на депрессию") is None


def test_russian_terms_never_reach_a_ukrainian_group():
    groups = [_group("боротьба з депресією", "UA", resource_name="res/ua")]
    signals = [
        DemandSignal(
            "депрессия симптомы у мужчин", "UA", "search_term", impressions=400, clicks=20
        ),
        DemandSignal("панические атаки лечение", "UA", "search_term", impressions=300, clicks=15),
    ]

    candidates, _ = plan_expansion(signals=signals, groups=groups)

    assert candidates == []


def test_self_diagnosis_instruments_are_refused():
    brands = competitor_brands()

    for term in (
        "тест на депресію",
        "шкала депрессии бека",
        "тест на тривожність gad 7",
        "mental health test",
        "anxiety screening quiz",
    ):
        assert is_clinical_assessment(term), term
        assert not is_expandable_term(term, brands=brands), term

    # Informational symptom queries stay allowed: they match the article pages.
    assert not is_clinical_assessment("anxiety symptoms")
    assert is_expandable_term("anxiety symptoms", brands=brands)


def test_campaign_locale_reads_the_account_naming_scheme():
    assert campaign_locale("Ser / www.example.com / Articles / EN / Brand_Search") == "EN"
    assert campaign_locale("Ser / www.example.com / Home / UA / City_A") == "UA"
    assert campaign_locale("Bridge MaxClicks / DE / Berlin") == "DE"


def test_theme_overlap_tolerates_inflection():
    # Ukrainian inflects, so the stem has to match, not the whole word.
    assert theme_overlap("ознаки вигорання на роботі", "вигорання на роботі") >= 2
    assert theme_overlap("anxiety self help", "burnout at work") == 0
    # Stopwords must not create phantom overlap.
    assert theme_overlap("how to sleep", "how to eat") == 0


def test_competitor_brands_are_never_expandable():
    brands = competitor_brands()

    assert "betterhelp" in brands
    assert not is_expandable_term("betterhelp alternative", brands=brands)
    assert is_expandable_term("anxiety self help", brands=brands)


def test_clinical_and_malformed_terms_are_rejected():
    brands = competitor_brands()

    assert not is_expandable_term("psychiatrist near me", brands=brands)
    assert not is_expandable_term("buy medication online", brands=brands)
    assert not is_expandable_term("anxiety", brands=brands)  # single word
    assert not is_expandable_term(" ".join(["word"] * (MAX_KEYWORD_WORDS + 1)), brands=brands)


def test_search_terms_qualify_on_clicks_or_on_grant_ctr():
    # Click volume alone is enough, however thin the CTR.
    assert qualifies(DemandSignal("a b", "EN", "search_term", impressions=500, clicks=2))
    # A single click still qualifies when CTR clears the bar on real volume.
    assert qualifies(DemandSignal("a b", "EN", "search_term", impressions=20, clicks=1))
    # One click buried in 500 impressions is noise on both counts.
    assert not qualifies(DemandSignal("a b", "EN", "search_term", impressions=500, clicks=1))
    assert not qualifies(DemandSignal("a b", "EN", "search_term", impressions=5, clicks=0))


def test_keyword_ideas_need_volume_and_are_penalised_by_competition():
    low = DemandSignal("a b", "EN", "keyword_ideas", monthly_searches=10)
    high = DemandSignal("a b", "EN", "keyword_ideas", monthly_searches=2000, competition="LOW")
    contested = DemandSignal("a b", "EN", "keyword_ideas", monthly_searches=2000, competition="HIGH")

    assert not qualifies(low)
    assert qualifies(high)
    assert score_signal(high) > score_signal(contested)


def test_proven_clicks_outrank_transferred_hypotheses():
    proven = DemandSignal("burnout at work", "EN", "search_term", impressions=100, clicks=9)
    transferred = DemandSignal("burnout at work", "EN", "cross_locale", origin_locale="UA")

    assert score_signal(proven) > score_signal(transferred)


def test_terms_land_only_in_a_group_that_shares_the_theme():
    groups = [
        _group("вигорання на роботі", "UA", resource_name="res/burnout"),
        _group("тривога", "UA", resource_name="res/anxiety"),
    ]

    assert match_group("ознаки вигорання на роботі", "UA", groups)["resource_name"] == "res/burnout"
    assert match_group("рецепт борщу", "UA", groups) is None
    # Right theme, wrong locale: no placement.
    assert match_group("ознаки вигорання на роботі", "DE", groups) is None


def test_plan_places_proven_queries_and_skips_existing_terms():
    groups = [_group("вигорання на роботі", "UA", terms={"вигорання на роботі"})]
    signals = [
        DemandSignal("ознаки вигорання на роботі", "UA", "search_term", impressions=200, clicks=12),
        DemandSignal("вигорання на роботі", "UA", "search_term", impressions=900, clicks=40),
    ]

    candidates, _ = plan_expansion(signals=signals, groups=groups)

    assert [item.text for item in candidates] == ["ознаки вигорання на роботі"]
    assert candidates[0].match_type == "PHRASE"
    assert candidates[0].locale == "UA"
    assert "Proven query" in candidates[0].reason


def test_plan_refuses_terms_the_campaign_negatives_already_block():
    groups = [
        _group(
            "вигорання на роботі",
            "UA",
            negatives=[{"text": "безкоштовно", "match_type": "BROAD"}],
        )
    ]
    signals = [
        DemandSignal(
            "вигорання на роботі безкоштовно", "UA", "search_term", impressions=200, clicks=12
        )
    ]

    candidates, warnings = plan_expansion(signals=signals, groups=groups)

    assert candidates == []
    assert warnings[0]["op"] == "expansion_blocked_by_negative"


def test_plan_rejects_language_mismatched_terms():
    groups = [_group("angst selbsthilfe", "DE")]
    signals = [
        # A Ukrainian query attributed to the DE campaign must not be created
        # there: it would wreck relevance and Quality Score.
        DemandSignal("тривога допомога", "DE", "search_term", impressions=300, clicks=15)
    ]

    candidates, _ = plan_expansion(signals=signals, groups=groups)

    assert candidates == []


def test_plan_caps_growth_per_group_and_per_run():
    groups = [_group("вигорання на роботі", "UA", resource_name="res/burnout")]
    signals = [
        DemandSignal(
            f"вигорання на роботі варіант {index}",
            "UA",
            "search_term",
            impressions=200,
            clicks=10 + index,
        )
        for index in range(12)
    ]

    candidates, _ = plan_expansion(signals=signals, groups=groups, max_per_group=3)

    assert len(candidates) == 3
    # Highest-evidence terms win the limited slots.
    assert candidates[0].evidence["clicks"] == 21


def test_plan_respects_the_total_cap_across_groups():
    groups = [
        _group("вигорання на роботі", "UA", resource_name="res/a"),
        _group("тривога допомога", "UA", resource_name="res/b"),
    ]
    signals = [
        DemandSignal(f"вигорання на роботі x{i}", "UA", "search_term", impressions=200, clicks=9)
        for i in range(5)
    ] + [
        DemandSignal(f"тривога допомога y{i}", "UA", "search_term", impressions=200, clicks=9)
        for i in range(5)
    ]

    candidates, _ = plan_expansion(signals=signals, groups=groups, max_total=4, max_per_group=5)

    assert len(candidates) == 4


def test_cross_locale_transfer_fills_locales_without_traffic():
    proven = {"anxiety_stress": "UA"}

    signals = cross_locale_signals(proven_families=proven, locales=("UA", "EN", "DE"))
    locales = {signal.locale for signal in signals}

    # The locale that already proved the intent is not re-seeded.
    assert "UA" not in locales
    assert {"EN", "DE"} <= locales
    assert all(signal.source == "cross_locale" for signal in signals)
    assert all(signal.origin_locale == "UA" for signal in signals)


def test_proven_families_are_derived_from_real_clicks_only():
    signals = [
        DemandSignal("тривога допомога онлайн", "UA", "search_term", impressions=300, clicks=20),
        DemandSignal("angst selbsthilfe app", "DE", "search_term", impressions=300, clicks=0),
    ]

    families = proven_families(signals)

    assert families.get("anxiety_stress") == "UA"
    assert "DE" not in families.values()


def test_transferred_intents_reach_the_matching_foreign_group():
    groups = [_group("angst selbsthilfe", "DE", resource_name="res/de")]
    signals = cross_locale_signals(proven_families={"anxiety_stress": "UA"}, locales=("DE",))

    candidates, _ = plan_expansion(signals=signals, groups=groups)

    assert candidates
    assert all(item.locale == "DE" for item in candidates)
    assert all(item.ad_group == "res/de" for item in candidates)
    assert "Intent proven in UA" in candidates[0].reason


def test_coverage_report_exposes_starved_locales():
    groups = [
        _group("вигорання на роботі", "UA", terms={"вигорання на роботі"}),
        _group("angst selbsthilfe", "DE", terms=set()),
    ]
    candidates, _ = plan_expansion(
        signals=[
            DemandSignal("ознаки вигорання на роботі", "UA", "search_term", impressions=200, clicks=9)
        ],
        groups=groups,
    )

    report = coverage_report(groups=groups, candidates=candidates, locales=("UA", "EN", "DE"))

    assert report["UA"]["live_keywords"] == 1
    assert report["UA"]["planned_additions"] == 1
    assert report["DE"]["groups_without_live_keywords"] == 1
    assert report["EN"]["ad_groups"] == 0

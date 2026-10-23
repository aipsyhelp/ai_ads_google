"""Continuously expand and correct Search semantics across every locale.

The keyword normalizer keeps the existing core policy-safe; this agent grows it.
It answers "what is the market asking for that we do not yet cover, in every
locale we run" and turns the answer into policy-safe PHRASE keywords placed in
the ad group that already owns that theme.

Three demand sources, in descending order of evidence strength:

1. `search_term_view` — queries that really reached the account. Strongest
   evidence, but it only ever covers ground we already rank for.
2. Cross-locale intent transfer — an intent proven in one locale is proposed in
   the others from the curated locale seeds. This is what keeps DE and EN moving
   when only UA has traffic, and it needs no external data.
3. Keyword Planner demand — real search volumes for terms nobody has clicked
   yet. Optional: `KeywordPlanIdeaService` requires a Basic-access developer
   token, so the agent degrades to sources 1 and 2 and says so when the token is
   still on explorer access.

Competitor brands are used as an exclusion list, never as bid targets: bidding
on a competitor's trademark is both a legal and an Ad Grants risk.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
from ads_ai_agents.adapters.settings import GoogleAdsSettings
from ads_ai_agents.adapters.write_guard import assert_write_service
from ads_ai_agents.intelligence import keyword_ops
from ads_ai_agents.intelligence.grant_keyword_normalizer import GrantKeywordNormalizer
from ads_ai_agents.intelligence.locale_strategy import LOCALE_SEEDS
from ads_ai_agents.intelligence.negative_match import blocked_by

LOCALES: tuple[str, ...] = ("UA", "EN", "DE")

# Matches the normalizer's window: evidence has to outlive a delivery outage.
EVIDENCE_WINDOW_DAYS = 120

# A search term earns a keyword either by converting attention into clicks or by
# clearing the Ad Grants CTR bar on real impression volume.
MIN_IMPRESSIONS_FOR_DEMAND = 10
MIN_CTR_FOR_DEMAND = 0.03
MIN_CLICKS_FOR_DEMAND = 2

# Keyword Planner volume floor: below this a term cannot move account CTR.
MIN_MONTHLY_SEARCHES = 50

# Growth is deliberately capped per run so one cycle can never restructure the
# account faster than 7 days of CTR data can validate it.
MAX_NEW_PER_GROUP = 5
MAX_NEW_TOTAL = 40

# Google rejects keywords longer than 10 words or 80 characters.
MAX_KEYWORD_WORDS = 10
MAX_KEYWORD_CHARS = 80

# A term is only placed in a group whose theme it shares, never dumped into
# whichever group happens to be first.
MIN_THEME_OVERLAP = 1
_STEM_LENGTH = 5

GEO_BY_LOCALE = {
    "UA": ("geoTargetConstants/2804",),
    "DE": ("geoTargetConstants/2276",),
    "EN": (
        "geoTargetConstants/2840",  # US
        "geoTargetConstants/2826",  # UK
        "geoTargetConstants/2124",  # CA
    ),
}

# Verified against language_constant: 1036 is Ukrainian (uk), not Russian.
LANGUAGE_BY_LOCALE = {
    "UA": "languageConstants/1036",
    "EN": "languageConstants/1000",
    "DE": "languageConstants/1001",
}

SITE_BY_LOCALE = {
    "UA": "https://www.example.com/uk",
    "EN": "https://www.example.com/en",
    "DE": "https://www.example.com/de",
}

# Ukrainian and Russian share most of the Cyrillic alphabet, and the account
# receives both. A Russian keyword inside a Ukrainian campaign is an editorial
# decision, not an optimisation, so a Cyrillic term is only treated as Ukrainian
# when it proves it: a Ukrainian-only letter or function word. Anything else is
# left alone rather than guessed at.
_UKRAINIAN_ONLY_LETTERS = frozenset("іїєґ")
_RUSSIAN_ONLY_LETTERS = frozenset("ыэъё")
_UKRAINIAN_WORDS = frozenset(
    {
        "як",
        "що",
        "чому",
        "це",
        "або",
        "щоб",
        "де",
        "хто",
        "дуже",
        "треба",
        "робити",
    }
)
_RUSSIAN_WORDS = frozenset(
    {
        "как",
        "что",
        "почему",
        "это",
        "или",
        "чтобы",
        "где",
        "кто",
        "очень",
        "нужно",
        "делать",
        "лечение",
        "лечения",
        "симптомы",
        "женщин",
        "мужчин",
        "детей",
        "избавиться",
    }
)

# Self-assessment instruments imply clinical screening. For a wellbeing NGO on
# Ad Grants — already restricted under HEALTH_IN_PERSONALIZED_ADS — these are the
# riskiest terms in the space, so the expander never proposes them.
_ASSESSMENT_WORDS = frozenset(
    {
        "test",
        "tests",
        "quiz",
        "screening",
        "diagnosis",
        "diagnostic",
        "тест",
        "тести",
        "тесты",
        "діагностика",
        "диагностика",
    }
)
_ASSESSMENT_MARKERS = (
    "тест на",
    "шкала",
    "gad 7",
    "gad-7",
    "gad7",
    "phq",
    "опросник",
    "опитувальник",
    "self diagnosis",
    "self-diagnosis",
)

_GERMAN_LETTERS = frozenset("äöüß")
_GERMAN_WORDS = frozenset(
    {
        "und",
        "für",
        "der",
        "die",
        "das",
        "mit",
        "ohne",
        "bei",
        "wie",
        "ich",
        "gegen",
        "selbsthilfe",
        "hilfe",
        "unterstützung",
        "bewältigen",
        "angst",
        "stress",
        "burnout",
    }
)

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "at",
        "for",
        "from",
        "how",
        "in",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
        "der",
        "die",
        "das",
        "und",
        "für",
        "mit",
        "bei",
        "wie",
        "як",
        "при",
        "для",
        "що",
        "чи",
        "та",
        "і",
        "в",
        "на",
        "з",
    }
)


@dataclass(frozen=True)
class DemandSignal:
    text: str
    locale: str
    source: str  # search_term | cross_locale | keyword_ideas
    impressions: int = 0
    clicks: int = 0
    monthly_searches: int = 0
    competition: str = ""
    origin_locale: str | None = None

    @property
    def ctr(self) -> float:
        return (self.clicks / self.impressions) if self.impressions else 0.0


@dataclass(frozen=True)
class ExpansionCandidate:
    text: str
    locale: str
    ad_group: str
    ad_group_name: str
    campaign_name: str
    source: str
    score: float
    reason: str
    match_type: str = "PHRASE"
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SemanticExpansionResult:
    dry_run: bool
    locales: list[str] = field(default_factory=list)
    demand_signals: int = 0
    candidates_planned: list[dict[str, Any]] = field(default_factory=list)
    keywords_created: int = 0
    coverage: dict[str, Any] = field(default_factory=dict)
    warnings: list[dict[str, str]] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def competitor_brands(locales: tuple[str, ...] = LOCALES) -> frozenset[str]:
    """Brands we must never bid on, gathered from every locale watchlist."""
    brands: set[str] = set()
    for locale in locales:
        watchlist = (LOCALE_SEEDS.get(locale) or {}).get("competitors")
        if watchlist is None:
            continue
        brands.update(brand.lower() for brand in watchlist.brands)
    return frozenset(brands)


def detect_locale(text: str) -> str | None:
    """Locale of a term, or None when it cannot be established.

    Returning None is a feature: an unidentified term is skipped instead of
    being dropped into whichever campaign looked closest.
    """
    lowered = (text or "").strip().lower()
    if not lowered:
        return None
    letters = set(lowered)
    words = set(lowered.split())

    if any("\u0400" <= ch <= "\u04ff" for ch in lowered):
        if _RUSSIAN_ONLY_LETTERS & letters or _RUSSIAN_WORDS & words:
            return "RU"
        if _UKRAINIAN_ONLY_LETTERS & letters or _UKRAINIAN_WORDS & words:
            return "UA"
        return None

    if _GERMAN_LETTERS & letters or _GERMAN_WORDS & words:
        return "DE"
    if any("a" <= ch <= "z" for ch in lowered):
        return "EN"
    return None


def is_clinical_assessment(text: str) -> bool:
    """Self-diagnosis instruments: depression scales, GAD-7, screening quizzes."""
    lowered = (text or "").strip().lower()
    if any(marker in lowered for marker in _ASSESSMENT_MARKERS):
        return True
    return bool(_ASSESSMENT_WORDS & set(_tokens(lowered)))


def campaign_locale(campaign_name: str) -> str | None:
    """Read the locale out of the account's `A / B / LOCALE / D` naming scheme."""
    segments = {segment.strip().upper() for segment in (campaign_name or "").split("/")}
    for locale in ("UA", "EN", "DE", "LT"):
        if locale in segments:
            return locale
    return detect_locale(campaign_name)


def _tokens(text: str) -> list[str]:
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in (text or "").lower())
    return [word for word in cleaned.split() if word not in _STOPWORDS]


def _same_stem(left: str, right: str) -> bool:
    """Cheap morphology: Ukrainian and German inflect, so compare word stems."""
    if left == right:
        return True
    if len(left) < _STEM_LENGTH or len(right) < _STEM_LENGTH:
        return False
    return left[:_STEM_LENGTH] == right[:_STEM_LENGTH]


def theme_overlap(text: str, theme: str) -> int:
    text_tokens = _tokens(text)
    theme_tokens = _tokens(theme)
    return sum(
        1
        for token in theme_tokens
        if any(_same_stem(token, other) for other in text_tokens)
    )


def is_expandable_term(text: str, *, brands: frozenset[str]) -> bool:
    lowered = (text or "").strip().lower()
    words = lowered.split()
    if not (2 <= len(words) <= MAX_KEYWORD_WORDS):
        return False
    if len(lowered) > MAX_KEYWORD_CHARS:
        return False
    if is_clinical_assessment(lowered):
        return False
    # quality_score=0 means "unknown", which skips the normalizer's QS filter
    # while keeping its policy and clinical-language gates.
    if not GrantKeywordNormalizer._is_safe_keyword(lowered, quality_score=0):
        return False
    return not any(brand in lowered for brand in brands)


def score_signal(signal: DemandSignal) -> float:
    if signal.source == "search_term":
        return round(signal.ctr * 100 + signal.clicks * 3, 2)
    if signal.source == "keyword_ideas":
        volume = min(signal.monthly_searches / 100.0, 30.0)
        penalty = {"HIGH": 4.0, "MEDIUM": 2.0}.get(signal.competition.upper(), 0.0)
        return round(max(volume - penalty, 0.1), 2)
    # Cross-locale transfer is a hypothesis, not evidence: it ranks below any
    # term with real clicks but above nothing at all.
    return 4.0


def qualifies(signal: DemandSignal) -> bool:
    if signal.source == "search_term":
        if signal.clicks >= MIN_CLICKS_FOR_DEMAND:
            return True
        return signal.impressions >= MIN_IMPRESSIONS_FOR_DEMAND and signal.ctr >= MIN_CTR_FOR_DEMAND
    if signal.source == "keyword_ideas":
        return signal.monthly_searches >= MIN_MONTHLY_SEARCHES
    return True


def cross_locale_signals(
    *,
    proven_families: dict[str, str],
    locales: tuple[str, ...] = LOCALES,
) -> list[DemandSignal]:
    """Propose seeds of an intent family into locales that lack coverage.

    `proven_families` maps an intent family (for example `anxiety_stress`) to the
    locale that proved it. Every other locale gets that family's curated seeds.
    """
    signals: list[DemandSignal] = []
    for locale in locales:
        config = LOCALE_SEEDS.get(locale)
        if not config:
            continue
        for cluster in config.get("intent_clusters") or []:
            family = cluster.name.rsplit("_", 1)[0]
            origin = proven_families.get(family)
            if origin is None or origin == locale:
                continue
            for seed in cluster.seed_keywords:
                signals.append(
                    DemandSignal(
                        text=seed,
                        locale=locale,
                        source="cross_locale",
                        origin_locale=origin,
                    )
                )
    return signals


def proven_families(signals: list[DemandSignal]) -> dict[str, str]:
    """Which intent families have real click evidence, and where."""
    proven: dict[str, str] = {}
    for signal in signals:
        if signal.source != "search_term" or not qualifies(signal):
            continue
        config = LOCALE_SEEDS.get(signal.locale)
        if not config:
            continue
        for cluster in config.get("intent_clusters") or []:
            family = cluster.name.rsplit("_", 1)[0]
            if family in proven:
                continue
            if any(theme_overlap(signal.text, seed) for seed in cluster.seed_keywords):
                proven[family] = signal.locale
    return proven


def match_group(
    text: str,
    locale: str,
    groups: list[dict[str, Any]],
) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_overlap = 0
    for group in groups:
        if group.get("locale") != locale:
            continue
        overlap = theme_overlap(text, group.get("name") or "")
        if overlap > best_overlap:
            best, best_overlap = group, overlap
    return best if best_overlap >= MIN_THEME_OVERLAP else None


def plan_expansion(
    *,
    signals: list[DemandSignal],
    groups: list[dict[str, Any]],
    max_per_group: int = MAX_NEW_PER_GROUP,
    max_total: int = MAX_NEW_TOTAL,
    brands: frozenset[str] | None = None,
) -> tuple[list[ExpansionCandidate], list[dict[str, str]]]:
    """Turn demand signals into placed, policy-safe PHRASE keyword candidates."""
    brands = brands if brands is not None else competitor_brands()
    warnings: list[dict[str, str]] = []

    # Keep the strongest signal per locale+term.
    strongest: dict[tuple[str, str], DemandSignal] = {}
    for signal in signals:
        text = " ".join((signal.text or "").strip().lower().split())
        if not text:
            continue
        key = (signal.locale, text)
        incumbent = strongest.get(key)
        if incumbent is None or score_signal(signal) > score_signal(incumbent):
            strongest[key] = signal

    candidates: list[ExpansionCandidate] = []
    for (locale, text), signal in strongest.items():
        if not qualifies(signal):
            continue
        if detect_locale(text) != locale:
            # A German term in a Ukrainian group would tank both relevance and
            # Quality Score, so language must agree with the campaign.
            continue
        if not is_expandable_term(text, brands=brands):
            continue

        group = match_group(text, locale, groups)
        if group is None:
            continue
        if text in (group.get("terms") or set()):
            continue

        blockers = blocked_by(text, group.get("negatives") or [])
        if blockers:
            warnings.append(
                {
                    "op": "expansion_blocked_by_negative",
                    "keyword": text,
                    "warning": f"Suppressed by campaign negative(s): {', '.join(blockers[:3])}",
                }
            )
            continue

        candidates.append(
            ExpansionCandidate(
                text=text,
                locale=locale,
                ad_group=group["resource_name"],
                ad_group_name=group.get("name") or "",
                campaign_name=group.get("campaign_name") or "",
                source=signal.source,
                score=score_signal(signal),
                reason=_reason(signal),
                evidence={
                    "impressions": signal.impressions,
                    "clicks": signal.clicks,
                    "ctr": round(signal.ctr, 4),
                    "monthly_searches": signal.monthly_searches,
                    "competition": signal.competition,
                    "origin_locale": signal.origin_locale,
                },
            )
        )

    candidates.sort(key=lambda item: (-item.score, item.text))

    selected: list[ExpansionCandidate] = []
    per_group: dict[str, int] = {}
    for candidate in candidates:
        if len(selected) >= max_total:
            break
        used = per_group.get(candidate.ad_group, 0)
        if used >= max_per_group:
            continue
        per_group[candidate.ad_group] = used + 1
        selected.append(candidate)

    return selected, warnings


def _reason(signal: DemandSignal) -> str:
    if signal.source == "search_term":
        return (
            f"Proven query: {signal.clicks} click(s) on {signal.impressions} impression(s) "
            f"({signal.ctr:.1%} CTR) in the last {EVIDENCE_WINDOW_DAYS} days."
        )
    if signal.source == "keyword_ideas":
        return (
            f"Unserved demand: ~{signal.monthly_searches} monthly searches, "
            f"{signal.competition or 'unknown'} competition."
        )
    return (
        f"Intent proven in {signal.origin_locale}; transferring the curated "
        f"{signal.locale} phrasing to close the locale gap."
    )


def coverage_report(
    *,
    groups: list[dict[str, Any]],
    candidates: list[ExpansionCandidate],
    locales: tuple[str, ...] = LOCALES,
) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for locale in locales:
        locale_groups = [group for group in groups if group.get("locale") == locale]
        live = sum(len(group.get("live_terms") or ()) for group in locale_groups)
        report[locale] = {
            "ad_groups": len(locale_groups),
            "live_keywords": live,
            "planned_additions": sum(1 for item in candidates if item.locale == locale),
            "groups_without_live_keywords": sum(
                1 for group in locale_groups if not group.get("live_terms")
            ),
        }
    return report


class _ScopedWriteClientProxy:
    def __init__(self, client: Any) -> None:
        self._client = client

    def get_service(self, name: str):
        assert_write_service(name, scope="grant_semantic_expand")
        return self._client.get_service(name)

    def get_type(self, name: str):
        return self._client.get_type(name)

    @property
    def enums(self):
        return self._client.enums

    def __getattr__(self, item: str):
        return getattr(self._client, item)


class GrantSemanticExpander:
    """Grow the live keyword core from real demand, locale by locale."""

    def __init__(self, settings: GoogleAdsSettings | None = None) -> None:
        self.settings = settings or GoogleAdsSettings.from_env()
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from google.ads.googleads.client import GoogleAdsClient

            raw = GoogleAdsClient.load_from_dict(self.settings.client_config())
            self._client = _ScopedWriteClientProxy(raw)
        return self._client

    def inventory(self) -> dict[str, Any]:
        """Enabled Search ad groups with their keywords and campaign negatives."""
        ga = self.client.get_service("GoogleAdsService")
        cid = self.settings.customer_id

        groups: dict[str, dict[str, Any]] = {}
        for row in ga.search(
            customer_id=cid,
            query="""
            SELECT campaign.id, campaign.name, ad_group.id, ad_group.name,
                   ad_group.resource_name
            FROM ad_group
            WHERE campaign.status = 'ENABLED'
              AND campaign.advertising_channel_type = 'SEARCH'
              AND ad_group.status = 'ENABLED'
            """,
        ):
            groups[str(row.ad_group.id)] = {
                "resource_name": row.ad_group.resource_name,
                "name": row.ad_group.name,
                "campaign_id": str(row.campaign.id),
                "campaign_name": row.campaign.name,
                "locale": campaign_locale(row.campaign.name),
                "terms": set(),
                "live_terms": set(),
                "negatives": [],
            }

        for row in ga.search(
            customer_id=cid,
            query="""
            SELECT ad_group.id, ad_group_criterion.keyword.text,
                   ad_group_criterion.status, ad_group_criterion.negative
            FROM ad_group_criterion
            WHERE campaign.status = 'ENABLED'
              AND campaign.advertising_channel_type = 'SEARCH'
              AND ad_group.status = 'ENABLED'
              AND ad_group_criterion.type = 'KEYWORD'
              AND ad_group_criterion.status != 'REMOVED'
            """,
        ):
            group = groups.get(str(row.ad_group.id))
            if group is None:
                continue
            text = " ".join(row.ad_group_criterion.keyword.text.lower().split())
            if row.ad_group_criterion.negative:
                group["negatives"].append({"text": text, "match_type": "BROAD"})
                continue
            group["terms"].add(text)
            if row.ad_group_criterion.status.name == "ENABLED":
                group["live_terms"].add(text)

        campaign_negatives: dict[str, list[dict[str, str]]] = {}
        for row in ga.search(
            customer_id=cid,
            query="""
            SELECT campaign.id, campaign_criterion.keyword.text,
                   campaign_criterion.keyword.match_type
            FROM campaign_criterion
            WHERE campaign.status = 'ENABLED'
              AND campaign.advertising_channel_type = 'SEARCH'
              AND campaign_criterion.type = 'KEYWORD'
              AND campaign_criterion.negative = TRUE
            """,
        ):
            campaign_negatives.setdefault(str(row.campaign.id), []).append(
                {
                    "text": row.campaign_criterion.keyword.text.lower(),
                    "match_type": self.client.enums.KeywordMatchTypeEnum(
                        row.campaign_criterion.keyword.match_type
                    ).name,
                }
            )

        for group in groups.values():
            group["negatives"].extend(campaign_negatives.get(group["campaign_id"], []))

        return {"groups": list(groups.values())}

    def search_term_signals(self) -> list[DemandSignal]:
        ga = self.client.get_service("GoogleAdsService")
        end = date.today()
        start = end - timedelta(days=EVIDENCE_WINDOW_DAYS)
        query = (
            "SELECT campaign.name, search_term_view.search_term, "
            "metrics.impressions, metrics.clicks "
            "FROM search_term_view "
            "WHERE campaign.status = 'ENABLED' "
            "AND campaign.advertising_channel_type = 'SEARCH' "
            f"AND segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}' "
            "ORDER BY metrics.clicks DESC "
            "LIMIT 2000"
        )

        signals: list[DemandSignal] = []
        for row in ga.search(customer_id=self.settings.customer_id, query=query):
            text = row.search_term_view.search_term
            locale = campaign_locale(row.campaign.name) or detect_locale(text)
            if locale not in LOCALES:
                continue
            signals.append(
                DemandSignal(
                    text=text,
                    locale=locale,
                    source="search_term",
                    impressions=int(row.metrics.impressions),
                    clicks=int(row.metrics.clicks),
                )
            )
        return signals

    def keyword_idea_signals(
        self,
        *,
        locales: tuple[str, ...] = LOCALES,
        warnings: list[dict[str, str]] | None = None,
    ) -> list[DemandSignal]:
        """Search volumes for terms nobody has clicked yet.

        Needs a Basic-access developer token. On explorer access the API answers
        DEVELOPER_TOKEN_NOT_APPROVED, which is recorded as a warning so the run
        still produces a plan from the other two sources.
        """
        warnings = warnings if warnings is not None else []
        signals: list[DemandSignal] = []

        try:
            service = self.client.get_service("KeywordPlanIdeaService")
        except Exception as exc:  # noqa: BLE001
            warnings.append({"op": "keyword_ideas", "warning": str(exc)[:240]})
            return signals

        for locale in locales:
            config = LOCALE_SEEDS.get(locale)
            language = LANGUAGE_BY_LOCALE.get(locale)
            if not config or not language:
                continue
            seeds = [
                seed
                for cluster in config.get("intent_clusters") or []
                for seed in cluster.seed_keywords
            ][:20]
            if not seeds:
                continue

            request = self.client.get_type("GenerateKeywordIdeasRequest")
            request.customer_id = self.settings.customer_id
            request.language = language
            request.geo_target_constants.extend(GEO_BY_LOCALE.get(locale, ()))
            request.keyword_plan_network = (
                self.client.enums.KeywordPlanNetworkEnum.GOOGLE_SEARCH
            )
            request.keyword_and_url_seed.url = SITE_BY_LOCALE.get(locale, "")
            request.keyword_and_url_seed.keywords.extend(seeds)

            try:
                response = service.generate_keyword_ideas(request=request)
                for idea in response:
                    metrics = idea.keyword_idea_metrics
                    signals.append(
                        DemandSignal(
                            text=idea.text,
                            locale=locale,
                            source="keyword_ideas",
                            monthly_searches=int(metrics.avg_monthly_searches or 0),
                            competition=metrics.competition.name,
                        )
                    )
            except Exception as exc:  # noqa: BLE001
                warnings.append(
                    {
                        "op": "keyword_ideas",
                        "locale": locale,
                        "warning": _idea_failure_hint(str(exc)),
                    }
                )

        return signals

    def apply(
        self,
        *,
        dry_run: bool = True,
        locales: tuple[str, ...] = LOCALES,
        max_total: int = MAX_NEW_TOTAL,
        max_per_group: int = MAX_NEW_PER_GROUP,
        use_keyword_ideas: bool = True,
    ) -> SemanticExpansionResult:
        if not dry_run:
            assert_mutations_allowed()

        result = SemanticExpansionResult(dry_run=dry_run, locales=list(locales))

        try:
            inventory = self.inventory()
        except Exception as exc:  # noqa: BLE001
            result.errors.append({"op": "inventory", "error": str(exc)[:300]})
            return result

        groups = [group for group in inventory["groups"] if group.get("locale") in locales]

        signals: list[DemandSignal] = []
        try:
            signals.extend(self.search_term_signals())
        except Exception as exc:  # noqa: BLE001
            result.warnings.append({"op": "search_terms", "warning": str(exc)[:300]})

        signals.extend(
            cross_locale_signals(proven_families=proven_families(signals), locales=locales)
        )

        if use_keyword_ideas:
            signals.extend(
                self.keyword_idea_signals(locales=locales, warnings=result.warnings)
            )

        result.demand_signals = len(signals)

        candidates, warnings = plan_expansion(
            signals=signals,
            groups=groups,
            max_per_group=max_per_group,
            max_total=max_total,
        )
        result.warnings.extend(warnings)
        result.candidates_planned = [candidate.to_dict() for candidate in candidates]
        result.coverage = coverage_report(groups=groups, candidates=candidates, locales=locales)

        if dry_run or not candidates:
            return result

        try:
            self._create_keywords(candidates, result)
        except Exception as exc:  # noqa: BLE001
            result.errors.append({"op": "create_keywords", "error": str(exc)[:300]})

        return result

    def _create_keywords(
        self,
        candidates: list[ExpansionCandidate],
        result: SemanticExpansionResult,
    ) -> None:
        service = self.client.get_service("AdGroupCriterionService")
        operations = []
        for candidate in candidates:
            op = self.client.get_type("AdGroupCriterionOperation")
            op.create.ad_group = candidate.ad_group
            op.create.status = self.client.enums.AdGroupCriterionStatusEnum.ENABLED
            op.create.keyword.text = candidate.text
            op.create.keyword.match_type = self.client.enums.KeywordMatchTypeEnum.PHRASE
            operations.append(op)

        operations = keyword_ops.prune_rejected(
            service=service,
            client=self.client,
            customer_id=self.settings.customer_id,
            operations=operations,
            warnings=result.warnings,
        )
        created, _ = keyword_ops.commit(
            service=service,
            client=self.client,
            customer_id=self.settings.customer_id,
            operations=operations,
            warnings=result.warnings,
        )
        result.keywords_created = created


def _idea_failure_hint(message: str) -> str:
    if "DEVELOPER_TOKEN_NOT_APPROVED" in message or "explorer access" in message:
        return (
            "Keyword Planner demand unavailable: the developer token still has explorer "
            "access. Apply for Basic access in API Center to unlock search volumes."
        )
    return message[:240]

"""Promote proven Search queries into controlled exact-match Grant keywords."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
from ads_ai_agents.adapters.settings import GoogleAdsSettings
from ads_ai_agents.adapters.write_guard import assert_write_service
from ads_ai_agents.intelligence.grant_keyword_normalizer import (
    BLOCKED_KEYWORD_MARKERS,
    DIRECT_OR_TREATMENT_MARKERS,
    DIRECT_SERVICE_GROUPS,
)


@dataclass
class SearchTermPromotionResult:
    dry_run: bool
    candidates_found: int = 0
    keywords_planned: list[dict[str, Any]] = field(default_factory=list)
    keywords_created: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _ScopedWriteClientProxy:
    def __init__(self, client: Any) -> None:
        self._client = client

    def get_service(self, name: str):
        assert_write_service(name, scope="grant_keyword_normalize")
        return self._client.get_service(name)

    def get_type(self, name: str):
        return self._client.get_type(name)

    @property
    def enums(self):
        return self._client.enums

    def __getattr__(self, item: str):
        return getattr(self._client, item)


class GrantSearchTermPromoter:
    """Recover proven demand without reopening broad-match policy risk."""

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

    def inventory(
        self,
        *,
        min_impressions: int = 3,
        min_clicks: int = 2,
        min_ctr: float = 0.05,
        max_candidates: int = 20,
    ) -> list[dict[str, Any]]:
        ga = self.client.get_service("GoogleAdsService")
        cid = self.settings.customer_id
        end = date.today()
        start = end - timedelta(days=59)
        existing: dict[int, set[tuple[str, str]]] = {}
        for row in ga.search(
            customer_id=cid,
            query="""
            SELECT ad_group.id,
                   ad_group_criterion.keyword.text,
                   ad_group_criterion.keyword.match_type
            FROM ad_group_criterion
            WHERE campaign.status = 'ENABLED'
              AND campaign.advertising_channel_type = 'SEARCH'
              AND ad_group.status = 'ENABLED'
              AND ad_group_criterion.type = 'KEYWORD'
            """,
        ):
            existing.setdefault(row.ad_group.id, set()).add(
                (
                    row.ad_group_criterion.keyword.text.strip().lower(),
                    self.client.enums.KeywordMatchTypeEnum(
                        row.ad_group_criterion.keyword.match_type
                    ).name,
                )
            )

        candidates: list[dict[str, Any]] = []
        search_term_query = f"""
            SELECT campaign.name, campaign.status,
                   ad_group.id, ad_group.name, ad_group.status,
                   search_term_view.search_term,
                   metrics.impressions, metrics.clicks, metrics.conversions
            FROM search_term_view
            WHERE campaign.status = 'ENABLED'
              AND campaign.advertising_channel_type = 'SEARCH'
              AND ad_group.status = 'ENABLED'
              AND segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'
            ORDER BY metrics.clicks DESC
            LIMIT 2000
            """
        for row in ga.search(
            customer_id=cid,
            query=search_term_query,
        ):
            term = " ".join(row.search_term_view.search_term.strip().split())
            impressions = int(row.metrics.impressions)
            clicks = int(row.metrics.clicks)
            ctr = clicks / impressions if impressions else 0.0
            if (
                impressions < min_impressions
                or clicks < min_clicks
                or ctr < min_ctr
                or not self._is_safe(term, row.campaign.name, row.ad_group.name)
                or (term.lower(), "EXACT") in existing.get(row.ad_group.id, set())
            ):
                continue
            candidates.append(
                {
                    "campaign": row.campaign.name,
                    "ad_group_id": str(row.ad_group.id),
                    "ad_group": row.ad_group.name,
                    "term": term,
                    "match_type": "EXACT",
                    "impressions_60d": impressions,
                    "clicks_60d": clicks,
                    "ctr_60d": round(ctr, 4),
                    # Kept as context only: historical account goals included
                    # low-value actions, so promotion is gated by CTR, not this value.
                    "reported_conversions_60d": round(float(row.metrics.conversions), 2),
                }
            )

        candidates.sort(
            key=lambda item: (
                item["clicks_60d"],
                item["ctr_60d"],
                item["impressions_60d"],
            ),
            reverse=True,
        )
        return candidates[:max_candidates]

    def apply(
        self,
        *,
        dry_run: bool = True,
        min_impressions: int = 3,
        min_clicks: int = 2,
        min_ctr: float = 0.05,
        max_candidates: int = 20,
    ) -> SearchTermPromotionResult:
        candidates = self.inventory(
            min_impressions=min_impressions,
            min_clicks=min_clicks,
            min_ctr=min_ctr,
            max_candidates=max_candidates,
        )
        result = SearchTermPromotionResult(
            dry_run=dry_run,
            candidates_found=len(candidates),
            keywords_planned=candidates,
        )
        if dry_run or not candidates:
            return result

        assert_mutations_allowed()
        service = self.client.get_service("AdGroupCriterionService")
        operations = []
        for item in candidates:
            operation = self.client.get_type("AdGroupCriterionOperation")
            criterion = operation.create
            criterion.ad_group = (
                f"customers/{self.settings.customer_id}/adGroups/{item['ad_group_id']}"
            )
            criterion.status = self.client.enums.AdGroupCriterionStatusEnum.ENABLED
            criterion.keyword.text = item["term"]
            criterion.keyword.match_type = self.client.enums.KeywordMatchTypeEnum.EXACT
            operations.append(operation)
        try:
            request = self.client.get_type("MutateAdGroupCriteriaRequest")
            request.customer_id = self.settings.customer_id
            request.operations.extend(operations)
            request.partial_failure = True
            response = service.mutate_ad_group_criteria(request=request)
            partial = str(response.partial_failure_error.message or "")
            result.keywords_created = len(response.results)
            if partial:
                result.errors.append({"operation": "create_exact_keywords", "error": partial})
        except Exception as exc:  # noqa: BLE001
            result.errors.append({"operation": "create_exact_keywords", "error": str(exc)})
        return result

    @staticmethod
    def _is_safe(term: str, campaign_name: str, ad_group_name: str) -> bool:
        lower = term.lower()
        words = [word for word in lower.split() if word]
        if len(words) < 2 or len(term) > 80:
            return False
        if ad_group_name.strip().lower() in DIRECT_SERVICE_GROUPS:
            return False
        if any(marker in lower for marker in BLOCKED_KEYWORD_MARKERS):
            return False
        if any(marker in lower for marker in DIRECT_OR_TREATMENT_MARKERS):
            return False
        if any(marker in lower for marker in (" test", "test ", "тест", "quiz")):
            return False

        has_cyrillic = any("\u0400" <= char <= "\u04ff" for char in term)
        if " / EN /" in campaign_name and has_cyrillic:
            return False
        if " / UA /" in campaign_name:
            # Conservative language match: UA ads and landing pages should not
            # auto-promote Russian-language queries.
            has_ukrainian_letter = any(char in "іїєґ" for char in lower)
            if not has_cyrillic or not has_ukrainian_letter:
                return False
        return True

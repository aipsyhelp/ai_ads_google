from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
from ads_ai_agents.adapters.settings import GoogleAdsSettings
from ads_ai_agents.adapters.write_guard import assert_write_service
from ads_ai_agents.logging_utils import configure_logging

logger = configure_logging()


@dataclass
class KeywordApplyResult:
    dry_run: bool
    attempted: list[dict[str, Any]] = field(default_factory=list)
    applied: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GoogleAdsKeywordWriter:
    """CONTROLLED_LIVE writer: campaign-level negative keywords only."""

    def __init__(self, settings: GoogleAdsSettings | None = None) -> None:
        self.settings = settings or GoogleAdsSettings.from_env()
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from google.ads.googleads.client import GoogleAdsClient

            raw = GoogleAdsClient.load_from_dict(self.settings.client_config())
            self._client = _ScopedWriteClientProxy(raw, scope="controlled_live_keywords")
        return self._client

    def list_campaign_negatives(self) -> set[str]:
        service = self.client.get_service("GoogleAdsService")
        query = """
            SELECT campaign_criterion.keyword.text
            FROM campaign_criterion
            WHERE campaign_criterion.negative = TRUE
              AND campaign_criterion.type = 'KEYWORD'
            LIMIT 5000
        """
        terms: set[str] = set()
        for row in service.search(customer_id=self.settings.customer_id, query=query):
            text = (row.campaign_criterion.keyword.text or "").strip().lower()
            if text:
                terms.add(text)
        return terms

    def list_enabled_search_campaign_ids(self) -> list[dict[str, str]]:
        service = self.client.get_service("GoogleAdsService")
        query = """
            SELECT campaign.id, campaign.name
            FROM campaign
            WHERE campaign.status = 'ENABLED'
              AND campaign.advertising_channel_type = 'SEARCH'
        """
        return [
            {"id": str(row.campaign.id), "name": row.campaign.name}
            for row in service.search(customer_id=self.settings.customer_id, query=query)
        ]

    def apply_negatives(
        self,
        proposals: list[dict[str, Any]],
        *,
        dry_run: bool = True,
        max_apply: int = 40,
    ) -> KeywordApplyResult:
        result = KeywordApplyResult(dry_run=dry_run)
        if not dry_run:
            assert_mutations_allowed()

        search_campaigns = self.list_enabled_search_campaign_ids()
        if not search_campaigns:
            result.errors.append({"error": "no_enabled_search_campaigns"})
            return result

        applied_count = 0
        for proposal in proposals:
            if proposal.get("action") != "add_negative":
                result.skipped.append({**proposal, "reason": "not_add_negative"})
                continue
            if not proposal.get("auto_apply"):
                result.skipped.append({**proposal, "reason": "requires_approval"})
                continue
            term = str(proposal.get("term") or "").strip()
            if not term:
                continue
            match = str(proposal.get("match_type") or "PHRASE").upper()
            target_id = str(proposal.get("campaign_id") or "")
            targets = (
                [c for c in search_campaigns if c["id"] == target_id]
                if target_id
                else search_campaigns
            )
            # Cap account-wide bank terms to all search campaigns; limit total ops
            for camp in targets:
                if applied_count >= max_apply:
                    result.skipped.append({**proposal, "reason": "max_apply_reached"})
                    break
                op = {
                    **proposal,
                    "campaign_id": camp["id"],
                    "campaign_name": camp["name"],
                }
                result.attempted.append(op)
                if dry_run:
                    applied_count += 1
                    continue
                try:
                    self._add_campaign_negative(camp["id"], term, match)
                    result.applied.append(op)
                    applied_count += 1
                    logger.info("Added negative '%s' to %s", term, camp["name"][:50])
                except Exception as exc:  # noqa: BLE001
                    # Duplicate criterion is non-fatal
                    msg = str(exc)
                    if "DUPLICATE" in msg.upper() or "ALREADY_EXISTS" in msg.upper():
                        result.skipped.append({**op, "reason": "already_exists"})
                    else:
                        result.errors.append({**op, "error": msg})
            if not target_id:
                # For ALL_SEARCH bank terms, only attach once per cycle across campaigns up to cap
                # (loop above already walked campaigns until max_apply)
                pass
        return result

    def _add_campaign_negative(self, campaign_id: str, term: str, match_type: str) -> None:
        service = self.client.get_service("CampaignCriterionService")
        operation = self.client.get_type("CampaignCriterionOperation")
        criterion = operation.create
        # Build resource name without CampaignService (not in keyword write scope).
        criterion.campaign = f"customers/{self.settings.customer_id}/campaigns/{campaign_id}"
        criterion.negative = True
        criterion.keyword.text = term
        if match_type == "EXACT":
            criterion.keyword.match_type = self.client.enums.KeywordMatchTypeEnum.EXACT
        elif match_type == "BROAD":
            # Explicitly avoid broad negatives auto-expansion surprises — map to PHRASE
            criterion.keyword.match_type = self.client.enums.KeywordMatchTypeEnum.PHRASE
        else:
            criterion.keyword.match_type = self.client.enums.KeywordMatchTypeEnum.PHRASE
        service.mutate_campaign_criteria(
            customer_id=self.settings.customer_id, operations=[operation]
        )


class _ScopedWriteClientProxy:
    def __init__(self, client: Any, *, scope: str) -> None:
        self._client = client
        self._scope = scope

    def get_service(self, name: str):
        assert_write_service(name, scope=self._scope)
        return self._client.get_service(name)

    def get_type(self, name: str):
        return self._client.get_type(name)

    @property
    def enums(self):
        return self._client.enums

    def __getattr__(self, item: str):
        return getattr(self._client, item)

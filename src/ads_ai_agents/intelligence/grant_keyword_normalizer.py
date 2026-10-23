"""Normalize live Search semantics for an NGO Google Ad Grants account."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
from ads_ai_agents.adapters.settings import GoogleAdsSettings
from ads_ai_agents.adapters.write_guard import assert_write_service
from ads_ai_agents.intelligence import keyword_ops


# Evidence must outlive a delivery outage: a 30-day window goes blank once the
# account stops serving, which silently turns recovery into a no-op and keeps
# the account starved. 120 days still covers the last healthy serving period.
EVIDENCE_WINDOW_DAYS = 120

# Ad Grants requires a 5% account CTR, so proven-CTR terms recover first.
MIN_CTR_FOR_RECOVERY = 0.05

# Terms with real click volume but sub-5% broad CTR are recovered as PHRASE:
# tightening the match type is what lifts their CTR back over the Grant bar.
VOLUME_PROVEN_CLICKS = 5
MIN_CTR_FOR_VOLUME_PROVEN = 0.03

# An enabled ad group with fewer live keywords than this cannot win auctions,
# which is the failure mode that took Search delivery to zero.
MIN_ENABLED_KEYWORDS_PER_GROUP = 5

MAX_VALIDATION_ROUNDS = keyword_ops.MAX_VALIDATION_ROUNDS


DIRECT_SERVICE_GROUPS = frozenset(
    {
        "affordable online counseling",
        "find psychologists",
        "how to find a counsellor",
        "therapist ai",
        "дитячий психолог",
        "допомога психолога",
        "психотерапія",
    }
)

BLOCKED_KEYWORD_MARKERS = (
    "suicide",
    "suicidal",
    "kill myself",
    "emergency",
    "hotline",
    "psychiatrist",
    "medication",
    "prescription",
    "diagnosis",
    "clinical trial",
    "buy ",
    "purchase",
    "checkout",
    "app store",
    "google play",
    "суїцид",
    "самогуб",
    "екстрен",
    "психіатр",
    "ліки",
    "діагноз",
    "купити",
    "оплата",
    # Russian forms: the account receives Russian queries, and the Ukrainian
    # spellings above do not match them.
    "суицид",
    "самоубий",
    "психиатр",
    "лекарств",
    "таблетк",
    "диагноз",
    "купить",
)

DIRECT_OR_TREATMENT_MARKERS = (
    "therap",
    "psycholog",
    "psychiatr",
    "counsel",
    "treatment",
    "diagnos",
    "medication",
    "prescription",
    "clinic",
    "consultation",
    "психолог",
    "психіатр",
    "терап",
    "лікув",
    "діагн",
    "лікар",
    "клінік",
    "консультац",
    "лечен",
    "психиатр",
    "диагно",
    "клиник",
    "консультаци",
    "врач",
)


@dataclass
class KeywordNormalizeResult:
    dry_run: bool
    direct_groups: list[dict[str, Any]] = field(default_factory=list)
    groups_paused: int = 0
    broad_keywords_found: int = 0
    recoverable_paused_found: int = 0
    broad_keywords_paused: int = 0
    phrase_keywords_created: int = 0
    phrase_keywords_enabled: int = 0
    unsafe_keywords_paused: int = 0
    warnings: list[dict[str, str]] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    after_broad_keywords: int = 0
    after_direct_groups: int = 0

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


class GrantKeywordNormalizer:
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
        ga = self.client.get_service("GoogleAdsService")
        cid = self.settings.customer_id
        groups: dict[int, dict[str, Any]] = {}
        for row in ga.search(
            customer_id=cid,
            query="""
            SELECT campaign.name, campaign.status,
                   ad_group.id, ad_group.name, ad_group.status
            FROM ad_group
            WHERE campaign.status = 'ENABLED'
              AND campaign.advertising_channel_type = 'SEARCH'
              AND ad_group.status = 'ENABLED'
            """,
        ):
            groups[row.ad_group.id] = {
                "id": row.ad_group.id,
                "resource_name": f"customers/{cid}/adGroups/{row.ad_group.id}",
                "campaign": row.campaign.name,
                "name": row.ad_group.name,
                "criteria": [],
            }

        for row in ga.search(
            customer_id=cid,
            query="""
            SELECT campaign.status, ad_group.status, ad_group.id,
                   ad_group_criterion.resource_name,
                   ad_group_criterion.status,
                   ad_group_criterion.negative,
                   ad_group_criterion.keyword.text,
                   ad_group_criterion.keyword.match_type,
                   ad_group_criterion.quality_info.quality_score
            FROM keyword_view
            WHERE campaign.status = 'ENABLED'
              AND ad_group.status = 'ENABLED'
              AND ad_group_criterion.negative = FALSE
              AND ad_group_criterion.type = 'KEYWORD'
            """,
        ):
            if row.ad_group.id not in groups:
                continue
            criterion = row.ad_group_criterion
            groups[row.ad_group.id]["criteria"].append(
                {
                    "resource_name": criterion.resource_name,
                    "text": criterion.keyword.text,
                    "match_type": self.client.enums.KeywordMatchTypeEnum(
                        criterion.keyword.match_type
                    ).name,
                    "status": self.client.enums.AdGroupCriterionStatusEnum(
                        criterion.status
                    ).name,
                    "quality_score": criterion.quality_info.quality_score,
                    "impressions_window": 0,
                    "clicks_window": 0,
                }
            )

        window_end = date.today()
        window_start = window_end - timedelta(days=EVIDENCE_WINDOW_DAYS - 1)
        metrics_by_resource: dict[str, tuple[int, int]] = {}
        for row in ga.search(
            customer_id=cid,
            query=f"""
            SELECT campaign.status, ad_group.status,
                   ad_group_criterion.resource_name,
                   metrics.impressions, metrics.clicks
            FROM keyword_view
            WHERE campaign.status = 'ENABLED'
              AND ad_group.status = 'ENABLED'
              AND ad_group_criterion.type = 'KEYWORD'
              AND segments.date BETWEEN '{window_start.isoformat()}'
                                    AND '{window_end.isoformat()}'
            """,
        ):
            resource_name = row.ad_group_criterion.resource_name
            impressions, clicks = metrics_by_resource.get(resource_name, (0, 0))
            metrics_by_resource[resource_name] = (
                impressions + int(row.metrics.impressions),
                clicks + int(row.metrics.clicks),
            )
        for group in groups.values():
            for criterion in group["criteria"]:
                impressions, clicks = metrics_by_resource.get(
                    criterion["resource_name"],
                    (0, 0),
                )
                criterion["impressions_window"] = impressions
                criterion["clicks_window"] = clicks

        direct = [
            {
                "id": group["id"],
                "resource_name": group["resource_name"],
                "campaign": group["campaign"],
                "name": group["name"],
            }
            for group in groups.values()
            if group["name"].strip().lower() in DIRECT_SERVICE_GROUPS
        ]
        broad = [
            {"group": group, "criterion": criterion}
            for group in groups.values()
            if group["name"].strip().lower() not in DIRECT_SERVICE_GROUPS
            for criterion in group["criteria"]
            if criterion["status"] == "ENABLED" and criterion["match_type"] == "BROAD"
        ]
        # Paused BROAD terms are recovered as PHRASE copies; paused PHRASE/EXACT
        # terms only need re-enabling, so both are valid recovery candidates.
        candidates = [
            {"group": group, "criterion": criterion}
            for group in groups.values()
            if group["name"].strip().lower() not in DIRECT_SERVICE_GROUPS
            for criterion in group["criteria"]
            if criterion["status"] == "PAUSED"
            and criterion["match_type"] in {"BROAD", "PHRASE", "EXACT"}
            and self._is_safe_keyword(
                criterion["text"],
                quality_score=criterion["quality_score"],
            )
            and not any(
                existing["text"].strip().lower() == criterion["text"].strip().lower()
                and existing["match_type"] in {"PHRASE", "EXACT"}
                and existing["status"] == "ENABLED"
                for existing in group["criteria"]
            )
        ]
        recoverable = [
            item
            for item in candidates
            if self._has_grant_ctr_evidence(item["criterion"])
        ]

        # Keep every campaign structurally usable even after broad match is
        # removed: at least two evidence-safe topic groups, up to five phrases
        # per fallback group.
        selected_resources = {
            item["criterion"]["resource_name"] for item in recoverable
        }
        campaigns = {group["campaign"] for group in groups.values()}
        for campaign in campaigns:
            selected_group_ids = {
                item["group"]["id"]
                for item in recoverable
                if item["group"]["campaign"] == campaign
            }
            selected_group_ids.update(
                group["id"]
                for group in groups.values()
                if group["campaign"] == campaign
                and group["name"].strip().lower() not in DIRECT_SERVICE_GROUPS
                and any(
                    criterion["status"] == "ENABLED"
                    and criterion["match_type"] in {"PHRASE", "EXACT"}
                    and self._is_safe_keyword(
                        criterion["text"],
                        quality_score=criterion["quality_score"],
                    )
                    for criterion in group["criteria"]
                )
            )
            if len(selected_group_ids) >= 2:
                continue
            by_group: dict[int, list[dict[str, Any]]] = {}
            for item in candidates:
                if item["group"]["campaign"] != campaign:
                    continue
                by_group.setdefault(item["group"]["id"], []).append(item)
            ranked_groups = sorted(
                by_group.values(),
                key=lambda items: (
                    max(x["criterion"]["quality_score"] for x in items),
                    len(items),
                ),
                reverse=True,
            )
            for items in ranked_groups:
                group_id = items[0]["group"]["id"]
                if group_id in selected_group_ids:
                    continue
                ranked_items = sorted(
                    items,
                    key=lambda item: (
                        item["criterion"]["impressions_window"],
                        item["criterion"]["quality_score"],
                    ),
                    reverse=True,
                )
                for item in ranked_items[:5]:
                    if item["criterion"]["resource_name"] not in selected_resources:
                        recoverable.append(item)
                        selected_resources.add(item["criterion"]["resource_name"])
                selected_group_ids.add(group_id)
                if len(selected_group_ids) >= 2:
                    break
        return {
            "groups": groups,
            "direct_groups": direct,
            "broad": broad,
            "recoverable": recoverable,
        }

    @staticmethod
    def _has_grant_ctr_evidence(criterion: dict[str, Any]) -> bool:
        impressions = int(criterion.get("impressions_window") or 0)
        clicks = int(criterion.get("clicks_window") or 0)
        if impressions <= 0 or clicks <= 0:
            return False
        ctr = clicks / impressions
        if ctr >= MIN_CTR_FOR_RECOVERY:
            return True
        return clicks >= VOLUME_PROVEN_CLICKS and ctr >= MIN_CTR_FOR_VOLUME_PROVEN

    def apply(self, *, dry_run: bool = True) -> KeywordNormalizeResult:
        inventory = self.inventory()
        result = KeywordNormalizeResult(
            dry_run=dry_run,
            direct_groups=inventory["direct_groups"],
            groups_paused=len(inventory["direct_groups"]) if dry_run else 0,
            broad_keywords_found=len(inventory["broad"]),
            recoverable_paused_found=len(inventory["recoverable"]),
        )
        plans = self._build_keyword_plans(inventory, result)
        if dry_run:
            result.broad_keywords_paused = len(plans["pause"])
            result.phrase_keywords_created = len(plans["create"])
            result.phrase_keywords_enabled = len(plans["enable"])
            result.after_broad_keywords = 0
            result.after_direct_groups = 0
            return result

        assert_mutations_allowed()
        self._pause_direct_groups(result)
        self._apply_keyword_plans(plans, result)
        after = self.inventory()
        result.after_broad_keywords = len(after["broad"])
        result.after_direct_groups = len(after["direct_groups"])
        return result

    def _build_keyword_plans(
        self,
        inventory: dict[str, Any],
        result: KeywordNormalizeResult,
    ) -> dict[str, list[dict[str, Any]]]:
        plans: dict[str, list[dict[str, Any]]] = {
            "create": [],
            "enable": [],
            "pause": [],
        }
        for item in inventory["broad"]:
            group = item["group"]
            criterion = item["criterion"]
            text = criterion["text"].strip()
            plans["pause"].append(criterion)
            if any(marker in text.lower() for marker in BLOCKED_KEYWORD_MARKERS):
                result.unsafe_keywords_paused += 1
                continue
            phrase = next(
                (
                    existing
                    for existing in group["criteria"]
                    if existing["text"].strip().lower() == text.lower()
                    and existing["match_type"] == "PHRASE"
                    and existing["status"] != "REMOVED"
                ),
                None,
            )
            if phrase:
                if phrase["status"] != "ENABLED":
                    plans["enable"].append(phrase)
            else:
                plans["create"].append(
                    {
                        "ad_group": group["resource_name"],
                        "text": text,
                    }
                )
        for item in inventory["recoverable"]:
            criterion = item["criterion"]
            if criterion["match_type"] in {"PHRASE", "EXACT"}:
                plans["enable"].append(criterion)
            else:
                plans["create"].append(
                    {
                        "ad_group": item["group"]["resource_name"],
                        "text": criterion["text"].strip(),
                    }
                )
        self._enforce_delivery_floor(inventory, plans, result)
        return plans

    def _enforce_delivery_floor(
        self,
        inventory: dict[str, Any],
        plans: dict[str, list[dict[str, Any]]],
        result: KeywordNormalizeResult,
    ) -> None:
        """Guarantee every enabled ad group keeps a servable keyword set.

        Pausing broad match without replacing it is what drove Search delivery
        to zero: groups were left with too few live keywords to enter auctions.
        """
        paused_resources = {criterion["resource_name"] for criterion in plans["pause"]}
        enabled_resources = {criterion["resource_name"] for criterion in plans["enable"]}
        created_by_group: dict[str, int] = {}
        for item in plans["create"]:
            created_by_group[item["ad_group"]] = created_by_group.get(item["ad_group"], 0) + 1

        for group in inventory["groups"].values():
            if group["name"].strip().lower() in DIRECT_SERVICE_GROUPS:
                continue
            live = sum(
                1
                for criterion in group["criteria"]
                if criterion["resource_name"] not in paused_resources
                and criterion["match_type"] in {"PHRASE", "EXACT"}
                and (
                    criterion["status"] == "ENABLED"
                    or criterion["resource_name"] in enabled_resources
                )
            )
            live += created_by_group.get(group["resource_name"], 0)
            if live >= MIN_ENABLED_KEYWORDS_PER_GROUP:
                continue

            # Texts already serving in this group must never be proposed again,
            # otherwise every run re-sends the same duplicate operations.
            live_texts = {
                criterion["text"].strip().lower()
                for criterion in group["criteria"]
                if criterion["match_type"] in {"PHRASE", "EXACT"}
                and criterion["resource_name"] not in paused_resources
                and (
                    criterion["status"] == "ENABLED"
                    or criterion["resource_name"] in enabled_resources
                )
            }
            backfill = sorted(
                (
                    criterion
                    for criterion in group["criteria"]
                    if criterion["status"] == "PAUSED"
                    and criterion["resource_name"] not in enabled_resources
                    and criterion["text"].strip().lower() not in live_texts
                    and self._is_safe_keyword(
                        criterion["text"],
                        quality_score=criterion["quality_score"],
                    )
                ),
                key=lambda criterion: (
                    criterion["clicks_window"],
                    criterion["impressions_window"],
                    criterion["quality_score"],
                ),
                reverse=True,
            )
            already = {
                item["text"].strip().lower()
                for item in plans["create"]
                if item["ad_group"] == group["resource_name"]
            }
            for criterion in backfill:
                if live >= MIN_ENABLED_KEYWORDS_PER_GROUP:
                    break
                text = criterion["text"].strip()
                if text.lower() in already:
                    continue
                if criterion["match_type"] in {"PHRASE", "EXACT"}:
                    plans["enable"].append(criterion)
                    enabled_resources.add(criterion["resource_name"])
                else:
                    plans["create"].append(
                        {"ad_group": group["resource_name"], "text": text}
                    )
                    already.add(text.lower())
                live += 1

            if live == 0:
                # Never leave a group unable to serve: keep its broad terms live
                # until a safe replacement exists.
                revived = [
                    criterion
                    for criterion in plans["pause"]
                    if criterion["resource_name"]
                    in {item["resource_name"] for item in group["criteria"]}
                ]
                for criterion in revived:
                    plans["pause"].remove(criterion)
                    paused_resources.discard(criterion["resource_name"])
                if revived:
                    result.warnings.append(
                        {
                            "op": "delivery_floor",
                            "ad_group": group["name"],
                            "warning": (
                                "No policy-safe replacement available; broad match kept "
                                "live to preserve delivery."
                            ),
                        }
                    )

    @staticmethod
    def _is_safe_keyword(text: str, *, quality_score: int) -> bool:
        lowered = (text or "").strip().lower()
        if len(lowered.split()) < 2:
            return False
        if 0 < quality_score <= 2:
            return False
        if any(marker in lowered for marker in BLOCKED_KEYWORD_MARKERS):
            return False
        return not any(marker in lowered for marker in DIRECT_OR_TREATMENT_MARKERS)

    def _pause_direct_groups(self, result: KeywordNormalizeResult) -> None:
        from google.protobuf.field_mask_pb2 import FieldMask

        operations = []
        for item in result.direct_groups:
            op = self.client.get_type("AdGroupOperation")
            op.update.resource_name = item["resource_name"]
            op.update.status = self.client.enums.AdGroupStatusEnum.PAUSED
            op.update_mask.CopyFrom(FieldMask(paths=["status"]))
            operations.append(op)
        if not operations:
            return
        try:
            self.client.get_service("AdGroupService").mutate_ad_groups(
                customer_id=self.settings.customer_id,
                operations=operations,
            )
            result.groups_paused = len(operations)
        except Exception as exc:  # noqa: BLE001
            result.errors.append({"op": "pause_direct_groups", "error": str(exc)[:300]})

    def _rejected_indices(self, failure_error: Any) -> dict[int, str]:
        return keyword_ops.rejected_indices(self.client, failure_error)

    @staticmethod
    def _operation_label(operation: Any) -> str:
        return keyword_ops.operation_label(operation)

    @staticmethod
    def _is_create(operation: Any) -> bool:
        return keyword_ops.is_create(operation)

    def _drop_rejected_operations(
        self,
        service: Any,
        operations: list[Any],
        result: KeywordNormalizeResult,
    ) -> list[Any]:
        return keyword_ops.prune_rejected(
            service=service,
            client=self.client,
            customer_id=self.settings.customer_id,
            operations=operations,
            warnings=result.warnings,
            rejected_indices_fn=self._rejected_indices,
        )

    def _apply_keyword_plans(
        self,
        plans: dict[str, list[dict[str, Any]]],
        result: KeywordNormalizeResult,
    ) -> None:
        from google.protobuf.field_mask_pb2 import FieldMask

        create_operations = []
        for item in plans["create"]:
            op = self.client.get_type("AdGroupCriterionOperation")
            op.create.ad_group = item["ad_group"]
            op.create.status = self.client.enums.AdGroupCriterionStatusEnum.ENABLED
            op.create.keyword.text = item["text"]
            op.create.keyword.match_type = self.client.enums.KeywordMatchTypeEnum.PHRASE
            create_operations.append(op)
        for item in plans["enable"]:
            op = self.client.get_type("AdGroupCriterionOperation")
            op.update.resource_name = item["resource_name"]
            op.update.status = self.client.enums.AdGroupCriterionStatusEnum.ENABLED
            op.update_mask.CopyFrom(FieldMask(paths=["status"]))
            create_operations.append(op)

        service = self.client.get_service("AdGroupCriterionService")
        try:
            if create_operations:
                create_operations = self._drop_rejected_operations(
                    service, create_operations, result
                )
            if create_operations:
                created, enabled = keyword_ops.commit(
                    service=service,
                    client=self.client,
                    customer_id=self.settings.customer_id,
                    operations=create_operations,
                    warnings=result.warnings,
                    rejected_indices_fn=self._rejected_indices,
                )
                result.phrase_keywords_created = created
                result.phrase_keywords_enabled = enabled
        except Exception as exc:  # noqa: BLE001
            result.errors.append({"op": "create_phrase_keywords", "error": str(exc)[:300]})

        # Broad match is outside the Grant-safe operating envelope. Pause it
        # after phrase creation was attempted; policy-rejected terms must not
        # remain serving merely because Google grandfathered the broad version.
        pause_operations = []
        for item in plans["pause"]:
            op = self.client.get_type("AdGroupCriterionOperation")
            op.update.resource_name = item["resource_name"]
            op.update.status = self.client.enums.AdGroupCriterionStatusEnum.PAUSED
            op.update_mask.CopyFrom(FieldMask(paths=["status"]))
            pause_operations.append(op)
        try:
            if pause_operations:
                service.mutate_ad_group_criteria(
                    customer_id=self.settings.customer_id,
                    operations=pause_operations,
                )
                result.broad_keywords_paused = len(pause_operations)
        except Exception as exc:  # noqa: BLE001
            result.errors.append({"op": "pause_broad_keywords", "error": str(exc)[:300]})

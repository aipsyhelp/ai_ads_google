from types import SimpleNamespace

from ads_ai_agents.intelligence.grant_keyword_normalizer import (
    MAX_VALIDATION_ROUNDS,
    MIN_ENABLED_KEYWORDS_PER_GROUP,
    GrantKeywordNormalizer,
    KeywordNormalizeResult,
)


def _criterion(
    resource_name: str,
    text: str,
    *,
    match_type: str = "BROAD",
    status: str = "PAUSED",
    quality_score: int = 5,
    impressions: int = 0,
    clicks: int = 0,
) -> dict:
    return {
        "resource_name": resource_name,
        "text": text,
        "match_type": match_type,
        "status": status,
        "quality_score": quality_score,
        "impressions_window": impressions,
        "clicks_window": clicks,
    }


def test_clinical_language_is_blocked_in_both_cyrillic_spellings():
    is_safe = GrantKeywordNormalizer._is_safe_keyword

    for term in ("психіатр поруч", "ліки від тривоги", "лікування депресії"):
        assert not is_safe(term, quality_score=8), term
    # The account receives Russian queries, so the Russian forms must be
    # blocked too, not only the Ukrainian ones.
    for term in ("психиатр рядом", "лекарства от тревоги", "лечение депрессии", "врач онлайн"):
        assert not is_safe(term, quality_score=8), term

    assert is_safe("спокійні практики ввечері", quality_score=8)


def test_recovery_requires_grant_safe_ctr_evidence():
    has_evidence = GrantKeywordNormalizer._has_grant_ctr_evidence

    assert has_evidence({"impressions_window": 100, "clicks_window": 5})
    assert has_evidence({"impressions_window": 40, "clicks_window": 3})
    assert not has_evidence({"impressions_window": 10, "clicks_window": 0})
    assert not has_evidence({"impressions_window": 0, "clicks_window": 0})


def test_volume_proven_terms_recover_below_five_percent_ctr():
    has_evidence = GrantKeywordNormalizer._has_grant_ctr_evidence

    # 2150 impressions / 69 clicks = 3.2%: real demand, recovered as PHRASE.
    assert has_evidence({"impressions_window": 2150, "clicks_window": 69})
    # Same CTR without click volume stays out of the recovery set.
    assert not has_evidence({"impressions_window": 62, "clicks_window": 2})
    # Volume alone is not enough when CTR is far below the Grant bar.
    assert not has_evidence({"impressions_window": 10_000, "clicks_window": 60})


def test_delivery_floor_backfills_groups_left_without_live_keywords():
    normalizer = GrantKeywordNormalizer.__new__(GrantKeywordNormalizer)
    broad = _criterion("res/broad", "ознаки вигорання на роботі", status="ENABLED")
    spare = [
        _criterion(f"res/spare{index}", f"спокійні практики ввечері {index}")
        for index in range(6)
    ]
    group = {
        "id": 1,
        "resource_name": "customers/1/adGroups/1",
        "campaign": "Ser / UA",
        "name": "вигорання на роботі",
        "criteria": [broad, *spare],
    }
    inventory = {
        "groups": {1: group},
        "direct_groups": [],
        "broad": [{"group": group, "criterion": broad}],
        "recoverable": [],
    }
    result = KeywordNormalizeResult(dry_run=True)

    plans = normalizer._build_keyword_plans(inventory, result)

    assert plans["pause"] == [broad]
    created_for_group = [
        item for item in plans["create"] if item["ad_group"] == group["resource_name"]
    ]
    assert len(created_for_group) >= MIN_ENABLED_KEYWORDS_PER_GROUP


def test_delivery_floor_never_reproposes_already_serving_keywords():
    normalizer = GrantKeywordNormalizer.__new__(GrantKeywordNormalizer)
    # Same text exists twice: paused BROAD original and the live PHRASE copy a
    # previous run already created.
    paused_broad = _criterion("res/broad", "спокійні практики ввечері")
    live_phrase = _criterion(
        "res/phrase",
        "спокійні практики ввечері",
        match_type="PHRASE",
        status="ENABLED",
    )
    group = {
        "id": 4,
        "resource_name": "customers/1/adGroups/4",
        "campaign": "Ser / UA",
        "name": "вечірні практики",
        "criteria": [paused_broad, live_phrase],
    }
    inventory = {
        "groups": {4: group},
        "direct_groups": [],
        "broad": [],
        "recoverable": [],
    }
    result = KeywordNormalizeResult(dry_run=True)

    plans = normalizer._build_keyword_plans(inventory, result)

    assert plans["create"] == []
    assert plans["enable"] == []


def test_delivery_floor_keeps_broad_live_when_no_replacement_exists():
    normalizer = GrantKeywordNormalizer.__new__(GrantKeywordNormalizer)
    broad = _criterion("res/broad", "burnout at work signs", status="ENABLED")
    group = {
        "id": 2,
        "resource_name": "customers/1/adGroups/2",
        "campaign": "Ser / EN",
        "name": "burnout at work",
        "criteria": [broad],
    }
    inventory = {
        "groups": {2: group},
        "direct_groups": [],
        "broad": [{"group": group, "criterion": broad}],
        "recoverable": [],
    }
    result = KeywordNormalizeResult(dry_run=True)

    plans = normalizer._build_keyword_plans(inventory, result)

    # A phrase copy is created, so the group can serve and broad can retire.
    assert plans["create"] == [
        {"ad_group": group["resource_name"], "text": "burnout at work signs"}
    ]
    assert plans["pause"] == [broad]


def test_delivery_floor_revives_broad_when_every_term_is_policy_blocked():
    normalizer = GrantKeywordNormalizer.__new__(GrantKeywordNormalizer)
    broad = _criterion("res/broad", "psychiatrist near me", status="ENABLED")
    group = {
        "id": 3,
        "resource_name": "customers/1/adGroups/3",
        "campaign": "Ser / EN",
        "name": "mental assistance",
        "criteria": [broad],
    }
    inventory = {
        "groups": {3: group},
        "direct_groups": [],
        "broad": [{"group": group, "criterion": broad}],
        "recoverable": [],
    }
    result = KeywordNormalizeResult(dry_run=True)

    plans = normalizer._build_keyword_plans(inventory, result)

    assert plans["create"] == []
    assert plans["pause"] == []
    assert result.warnings and result.warnings[0]["op"] == "delivery_floor"


def _operation(text: str = "", resource_name: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        create=SimpleNamespace(keyword=SimpleNamespace(text=text)),
        update=SimpleNamespace(resource_name=resource_name),
    )


class _FakeRequest:
    def __init__(self) -> None:
        self.customer_id = ""
        self.operations: list = []
        self.partial_failure = False
        self.validate_only = False


class _FakeClient:
    def get_type(self, name: str):
        if name == "MutateAdGroupCriteriaRequest":
            return _FakeRequest()
        raise AssertionError(f"unexpected get_type({name})")


class _PhasedValidationService:
    """Mimics Google reporting only part of a batch's rejections per pass."""

    def __init__(self, blocked: set[str], *, per_round: int = 1) -> None:
        self.blocked = blocked
        self.per_round = per_round
        self.rounds: list[list[str]] = []

    def mutate_ad_group_criteria(self, *, request):
        texts = [op.create.keyword.text for op in request.operations]
        self.rounds.append(texts)
        offenders = {
            index: "HEALTH_IN_PERSONALIZED_ADS"
            for index, text in enumerate(texts)
            if text in self.blocked
        }
        reported = dict(sorted(offenders.items())[: self.per_round])
        code = 3 if reported else 0
        return SimpleNamespace(
            partial_failure_error=SimpleNamespace(
                code=code, message="policy findings", details=(), rejected_indices=reported
            ),
            results=[],
        )


class _StubNormalizer(GrantKeywordNormalizer):
    """Bypasses protobuf decoding so the convergence loop can be tested."""

    def _rejected_indices(self, failure_error):
        return dict(failure_error.rejected_indices)


def _stub_normalizer() -> _StubNormalizer:
    normalizer = _StubNormalizer.__new__(_StubNormalizer)
    normalizer._client = _FakeClient()
    normalizer.settings = SimpleNamespace(customer_id="1")
    return normalizer


def test_validation_revalidates_until_no_rejection_remains():
    normalizer = _stub_normalizer()
    service = _PhasedValidationService({"anxiety treatment", "depression medication"})
    operations = [
        _operation("спокійні практики ввечері"),
        _operation("anxiety treatment"),
        _operation("ознаки вигорання"),
        _operation("depression medication"),
    ]
    result = KeywordNormalizeResult(dry_run=False)

    surviving = normalizer._drop_rejected_operations(service, operations, result)

    texts = [op.create.keyword.text for op in surviving]
    assert texts == ["спокійні практики ввечері", "ознаки вигорання"]
    # One pass per rejection plus a final clean pass — a single pass would have
    # left the second blocked term in the committed batch.
    assert len(service.rounds) == 3
    rejected = [w for w in result.warnings if w["op"] == "keyword_rejected"]
    assert {w["keyword"] for w in rejected} == {"anxiety treatment", "depression medication"}


def test_validation_gives_up_after_the_round_budget():
    normalizer = _stub_normalizer()
    operations = [_operation(f"blocked term {index}") for index in range(MAX_VALIDATION_ROUNDS + 3)]
    service = _PhasedValidationService({op.create.keyword.text for op in operations})
    result = KeywordNormalizeResult(dry_run=False)

    surviving = normalizer._drop_rejected_operations(service, operations, result)

    assert len(service.rounds) == MAX_VALIDATION_ROUNDS
    assert len(surviving) == len(operations) - MAX_VALIDATION_ROUNDS
    assert any("still reported rejections" in w.get("warning", "") for w in result.warnings)


def test_unattributable_validation_failure_keeps_batch_and_warns():
    normalizer = _stub_normalizer()

    class _BatchLevelFailure:
        def __init__(self) -> None:
            self.calls = 0

        def mutate_ad_group_criteria(self, *, request):
            self.calls += 1
            return SimpleNamespace(
                partial_failure_error=SimpleNamespace(
                    code=3, message="batch rejected", details=(), rejected_indices={}
                ),
                results=[],
            )

    service = _BatchLevelFailure()
    operations = [_operation("спокійні практики")]
    result = KeywordNormalizeResult(dry_run=False)

    surviving = normalizer._drop_rejected_operations(service, operations, result)

    assert surviving == operations
    assert service.calls == 1
    assert any("Unattributable" in w.get("warning", "") for w in result.warnings)


def test_operation_kind_detection_separates_creates_from_enables():
    assert GrantKeywordNormalizer._is_create(_operation(text="спокійні практики"))
    assert not GrantKeywordNormalizer._is_create(_operation(resource_name="res/1"))
    assert GrantKeywordNormalizer._operation_label(_operation(resource_name="res/1")) == "res/1"

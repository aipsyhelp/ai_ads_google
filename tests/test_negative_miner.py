from ads_ai_agents.intelligence.negative_miner import mine_negative_proposals


def test_mines_policy_bank_and_harmful_terms():
    terms = [
        {
            "search_term": "psychiatric medication online",
            "clicks": 5,
            "conversions": 0,
            "spend": 12.0,
            "campaign_id": "1",
            "campaign": "Search EN",
        },
        {
            "search_term": "anxiety self help",
            "clicks": 10,
            "conversions": 2,
            "spend": 20.0,
            "campaign_id": "1",
            "campaign": "Search EN",
        },
    ]
    proposals = mine_negative_proposals(terms, existing_negatives=set())
    texts = {p.term.lower() for p in proposals}
    assert "psychiatric medication online" in texts
    assert any("suicide" in t or "free diagnosis" in t for t in texts)
    harmful = next(p for p in proposals if p.term.lower() == "psychiatric medication online")
    assert harmful.auto_apply is True
    assert harmful.confidence >= 0.85


def test_skips_existing_negatives():
    proposals = mine_negative_proposals(
        [{"search_term": "jobs", "clicks": 3, "conversions": 0, "campaign_id": "1", "campaign": "X"}],
        existing_negatives={"jobs", "pdf"},
    )
    assert all(p.term.lower() != "jobs" for p in proposals)

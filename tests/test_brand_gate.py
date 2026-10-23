from ads_ai_agents.intelligence.brand_gate import brand_gate_pack, brand_gate_text


def test_allows_no_diagnosis_disclaimer():
    assert brand_gate_text("Burnout check-ins — no diagnosis").get("pass") is True


def test_blocks_clinical_claim():
    assert brand_gate_text("Get a psychiatric diagnosis online").get("pass") is False


def test_pack_pass():
    pack = brand_gate_pack(
        [
            "Anonymous daily support",
            "Щоденні check-in — без діагнозів",
        ]
    )
    assert pack["pass"] is True

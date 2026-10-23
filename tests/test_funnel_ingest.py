from pathlib import Path

from ads_ai_agents.platform.funnel_ingest import aggregate_funnel, ingest_funnel_file, load_funnel_csv


def test_ingest_template_csv():
    path = Path(__file__).resolve().parents[1] / "samples" / "funnel_90d_template.csv"
    rows = load_funnel_csv(path)
    assert len(rows) >= 2
    funnel = aggregate_funnel(rows)
    assert funnel.ad_clicks > 0
    assert funnel.paying_users >= 1  # template includes one organic paying example
    result = ingest_funnel_file(path, source="google_ads")
    assert result["funnel"]["paying_users"] == 0.0  # ads-only filter
    assert "UA" in result["by_locale"] or "EN" in result["by_locale"]

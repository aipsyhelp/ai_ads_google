"""External system adapters.

Production implementations: Google Ads API, BigQuery, CRM/offline conversion imports,
and asset generators. The control plane should depend on these contracts, not vendor SDKs
directly outside this package.
"""

from ads_ai_agents.adapters.google_ads_readonly import GoogleAdsReadOnlyAdapter
from ads_ai_agents.adapters.settings import GoogleAdsSettings

__all__ = ["GoogleAdsReadOnlyAdapter", "GoogleAdsSettings"]

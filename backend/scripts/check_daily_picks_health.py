"""Read-only scheduled publication monitor; never triggers generation or retries."""
from __future__ import annotations

import json
import logging
import sys
from urllib.request import urlopen

log = logging.getLogger(__name__)
BASE_URL = "https://stocksense-production-7e0d.up.railway.app"


def publication_healthy(status: dict) -> bool:
    """A running job or last-known-good payload is not a completed daily run."""
    return (
        status.get("has_today") is True
        and status.get("stale") is False
        and status.get("serving_stale_payload") is False
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    failed = False
    for market in ("IN", "US"):
        try:
            with urlopen(f"{BASE_URL}/api/picks/status?market={market}", timeout=45) as response:
                status = json.load(response)
            if not publication_healthy(status):
                failed = True
                log.error("%s publication missing/stale: last_success=%s job=%s status=%s",
                          market, status.get("last_successful_generated_at"),
                          status.get("job_id"), status.get("last_attempt_status"))
            else:
                log.info("%s publication healthy: %s", market, status.get("last_successful_generated_at"))
        except Exception:
            failed = True
            log.exception("%s publication status unavailable", market)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

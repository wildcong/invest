import os
import time
from datetime import datetime

from scanner import (
    CACHE_FILE,
    KST,
    attach_previous_market_snapshots,
    build_scan_cache,
    cache_has_target_date,
    get_target_date,
    load_scan_cache,
    save_scan_cache,
)

PREFETCH_MAX_ATTEMPTS = int(os.environ.get("PREFETCH_MAX_ATTEMPTS", "4"))
PREFETCH_RETRY_DELAY_SECONDS = int(os.environ.get("PREFETCH_RETRY_DELAY_SECONDS", "300"))
MARKET_DATA_READY_HOUR = 15
MARKET_DATA_READY_MINUTE = 40


def main():
    now_kst = datetime.now(KST)
    if now_kst.weekday() > 4:
        print(f"weekend in KST ({now_kst:%Y-%m-%d %H:%M}); skipping KIS token/API calls")
        return

    ready_at = now_kst.replace(
        hour=MARKET_DATA_READY_HOUR,
        minute=MARKET_DATA_READY_MINUTE,
        second=0,
        microsecond=0,
    )
    if now_kst < ready_at:
        wait_seconds = int((ready_at - now_kst).total_seconds())
        print(f"waiting {wait_seconds} seconds until KST {ready_at:%H:%M} market data window")
        time.sleep(wait_seconds)

    app_key = os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("KIS_APP_SECRET")

    if not app_key or not app_secret:
        raise SystemExit("환경변수 KIS_APP_KEY / KIS_APP_SECRET 이 필요합니다.")

    target_date = get_target_date()
    existing_cache = load_scan_cache()
    if cache_has_target_date(existing_cache, target_date):
        print(f"scan cache already up to date for {target_date}; skipping rebuild")
        return

    cache = None
    for attempt in range(1, PREFETCH_MAX_ATTEMPTS + 1):
        print(f"building scan cache attempt {attempt}/{PREFETCH_MAX_ATTEMPTS} for {target_date}")
        cache = build_scan_cache(app_key, app_secret)
        if cache_has_target_date(cache, target_date):
            break

        for market_key, market in cache.get("markets", {}).items():
            summary = market.get("summary", {})
            print(
                f"incomplete {market_key}: scanned={summary.get('scanned', 0)} "
                f"market_size={market.get('market_size')}"
            )

        if attempt < PREFETCH_MAX_ATTEMPTS:
            print(f"cache incomplete; retrying in {PREFETCH_RETRY_DELAY_SECONDS} seconds")
            time.sleep(PREFETCH_RETRY_DELAY_SECONDS)
    else:
        print("scan cache was not complete enough to save; leaving previous cache unchanged")
        return

    cache = attach_previous_market_snapshots(existing_cache, cache)
    save_scan_cache(cache)

    print(f"scan cache updated: {CACHE_FILE}")
    print(f"generated_at_kst: {cache['generated_at_kst']}")
    for market_key, market in cache["markets"].items():
        summary = market["summary"]
        print(
            f"{market_key}: buy={summary['buy']} mixed={summary['mixed']} "
            f"sell={summary['sell']} scanned={summary['scanned']}"
        )


if __name__ == "__main__":
    main()

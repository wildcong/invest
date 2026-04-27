import os

from scanner import (
    CACHE_FILE,
    attach_previous_market_snapshots,
    build_scan_cache,
    cache_has_target_date,
    get_target_date,
    load_scan_cache,
    save_scan_cache,
)


def main():
    app_key = os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("KIS_APP_SECRET")

    if not app_key or not app_secret:
        raise SystemExit("환경변수 KIS_APP_KEY / KIS_APP_SECRET 이 필요합니다.")

    target_date = get_target_date()
    existing_cache = load_scan_cache()
    if cache_has_target_date(existing_cache, target_date):
        print(f"scan cache already up to date for {target_date}; skipping rebuild")
        return

    cache = build_scan_cache(app_key, app_secret)
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

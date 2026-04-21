import os

from scanner import CACHE_FILE, build_scan_cache, save_scan_cache


def main():
    app_key = os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("KIS_APP_SECRET")

    if not app_key or not app_secret:
        raise SystemExit("환경변수 KIS_APP_KEY / KIS_APP_SECRET 이 필요합니다.")

    cache = build_scan_cache(app_key, app_secret)
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

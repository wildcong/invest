import json
import os
import time
from datetime import datetime
from pathlib import Path

from market_data import (
    KST,
    build_program_trade_cache,
    build_us_liquidity_cache,
    load_program_trade_cache,
    program_cache_has_target_date,
    save_program_trade_cache,
    save_us_liquidity_cache,
)
from scanner import (
    CACHE_FILE,
    attach_previous_market_snapshots,
    build_scan_cache,
    cache_has_target_date,
    get_access_token,
    get_target_date,
    load_scan_cache,
    save_scan_cache,
)

PREFETCH_MAX_ATTEMPTS = int(os.environ.get("PREFETCH_MAX_ATTEMPTS", "3"))
PREFETCH_RETRY_DELAY_SECONDS = int(os.environ.get("PREFETCH_RETRY_DELAY_SECONDS", "300"))
MARKET_DATA_READY_HOUR = 15
MARKET_DATA_READY_MINUTE = 45
BATCH_STATE_FILE = Path(__file__).parent / "data" / "kis_batch_state.json"


def load_batch_state() -> dict:
    try:
        return json.loads(BATCH_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_batch_state(payload: dict) -> None:
    BATCH_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = BATCH_STATE_FILE.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(BATCH_STATE_FILE)


def refresh_us_liquidity_cache() -> None:
    try:
        cache = build_us_liquidity_cache()
        save_us_liquidity_cache(cache)
        print("US liquidity cache updated")
    except Exception as exc:
        print(f"US liquidity cache update failed; preserving previous cache: {exc}")


def main():
    now_kst = datetime.now(KST)
    if now_kst.weekday() > 4:
        print(f"weekend in KST ({now_kst:%Y-%m-%d %H:%M}); skipping batch")
        return

    ready_at = now_kst.replace(
        hour=MARKET_DATA_READY_HOUR,
        minute=MARKET_DATA_READY_MINUTE,
        second=0,
        microsecond=0,
    )
    if now_kst < ready_at:
        print(f"before KST {ready_at:%H:%M}; skipping without requesting a KIS token")
        return

    # FRED is public and independent of KIS authentication.
    refresh_us_liquidity_cache()

    target_date = get_target_date(now_kst)
    existing_scan = load_scan_cache()
    existing_program = load_program_trade_cache()
    scan_ready = cache_has_target_date(existing_scan, target_date)
    program_ready = program_cache_has_target_date(existing_program, target_date)
    if scan_ready and program_ready:
        print(f"all KIS caches already current for {target_date}; no token request")
        return

    batch_state = load_batch_state()
    if batch_state.get("token_request_date_kst") == now_kst.strftime("%Y-%m-%d"):
        print("today's KIS token request was already attempted; refusing a second request")
        return

    app_key = os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("KIS_APP_SECRET")
    if not app_key or not app_secret:
        raise SystemExit("환경변수 KIS_APP_KEY / KIS_APP_SECRET 이 필요합니다.")

    # Persist the guard before the network request. The workflow commits this
    # file even when later data collection fails, preventing same-day reruns
    # from issuing another token.
    save_batch_state(
        {
            "token_request_date_kst": now_kst.strftime("%Y-%m-%d"),
            "requested_at_kst": now_kst.isoformat(),
        }
    )
    access_token = get_access_token(app_key, app_secret)
    if not access_token:
        raise RuntimeError("KIS 토큰 1회 요청이 실패했습니다. 오늘은 자동 재발급하지 않습니다.")

    if not scan_ready:
        completed_scan = None
        for attempt in range(1, PREFETCH_MAX_ATTEMPTS + 1):
            print(f"building scan cache attempt {attempt}/{PREFETCH_MAX_ATTEMPTS}")
            candidate = build_scan_cache(app_key, app_secret, access_token)
            if cache_has_target_date(candidate, target_date):
                completed_scan = candidate
                break
            if attempt < PREFETCH_MAX_ATTEMPTS:
                print(
                    "scan cache incomplete; retrying data calls with the same token "
                    f"in {PREFETCH_RETRY_DELAY_SECONDS} seconds"
                )
                time.sleep(PREFETCH_RETRY_DELAY_SECONDS)

        if completed_scan:
            completed_scan = attach_previous_market_snapshots(existing_scan, completed_scan)
            save_scan_cache(completed_scan)
            print(f"scan cache updated: {CACHE_FILE}")
        else:
            print("scan cache incomplete; preserving previous cache")

    if not program_ready:
        try:
            program_cache = build_program_trade_cache(
                access_token,
                app_key,
                app_secret,
                now=now_kst,
            )
            if program_cache_has_target_date(program_cache, target_date):
                save_program_trade_cache(program_cache)
                print("program-trade cache updated")
            else:
                print("program-trade response is not current; preserving previous cache")
        except Exception as exc:
            print(f"program-trade cache update failed; preserving previous cache: {exc}")


if __name__ == "__main__":
    main()

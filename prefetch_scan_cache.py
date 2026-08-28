from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import requests

from kis_token_store import decrypt_access_token, encrypt_access_token
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
    get_target_date,
    issue_access_token,
    load_scan_cache,
    save_scan_cache,
)

PREFETCH_MAX_ATTEMPTS = max(1, int(os.environ.get("PREFETCH_MAX_ATTEMPTS", "3")))
PREFETCH_RETRY_DELAY_SECONDS = max(
    0,
    int(os.environ.get("PREFETCH_RETRY_DELAY_SECONDS", "60")),
)
TOKEN_REQUEST_COOLDOWN_SECONDS = max(
    0,
    int(os.environ.get("TOKEN_REQUEST_COOLDOWN_SECONDS", "1800")),
)
TOKEN_EXPIRY_SAFETY_SECONDS = 30
TOKEN_CONNECT_MAX_ATTEMPTS = 3
TOKEN_CONNECT_RETRY_DELAY_SECONDS = 5
MARKET_DATA_READY_HOUR = 15
MARKET_DATA_READY_MINUTE = 45
BATCH_STATE_FILE = Path(__file__).parent / "data" / "kis_batch_state.json"


def load_batch_state() -> dict:
    try:
        payload = json.loads(BATCH_STATE_FILE.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_batch_state(payload: dict) -> None:
    BATCH_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        dir=BATCH_STATE_FILE.parent,
        suffix=".tmp",
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary_file:
            json.dump(
                payload,
                temporary_file,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        os.replace(temporary_path, BATCH_STATE_FILE)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def _as_kst(value: datetime | None = None) -> datetime:
    current = value or datetime.now(KST)
    if current.tzinfo is None:
        return current.replace(tzinfo=KST)
    return current.astimezone(KST)


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def _safe_message(value: object, limit: int = 500) -> str:
    return " ".join(str(value).split())[:limit]


def _secret_fingerprint(app_secret: str) -> str:
    return hashlib.sha256(app_secret.encode("utf-8")).hexdigest()[:16]


def prepare_batch_state(state: dict, target_date: str, now_kst: datetime) -> dict:
    state["version"] = 2
    batch = state.get("batch")
    if not isinstance(batch, dict) or batch.get("target_date") != target_date:
        batch = {
            "target_date": target_date,
            "started_at_kst": now_kst.isoformat(),
            "updated_at_kst": now_kst.isoformat(),
            "status": "pending",
            "stages": {},
        }
        state["batch"] = batch
    elif not isinstance(batch.get("stages"), dict):
        batch["stages"] = {}
    return state


def _refresh_batch_status(state: dict, now_kst: datetime) -> None:
    batch = state.setdefault("batch", {})
    stages = batch.setdefault("stages", {})
    statuses = [
        value.get("status") for value in stages.values() if isinstance(value, dict)
    ]
    domestic = [
        stages.get(name, {}).get("status") for name in ("program_trade", "scanner")
    ]
    if domestic == ["success", "success"] and "failed" not in statuses:
        status = "success"
        batch["completed_at_kst"] = now_kst.isoformat()
    else:
        batch.pop("completed_at_kst", None)
        if "failed" in statuses and "success" in statuses:
            status = "partial"
        elif "failed" in statuses:
            status = "failed"
        elif "running" in statuses:
            status = "running"
        else:
            status = "pending"
    batch["status"] = status
    batch["updated_at_kst"] = now_kst.isoformat()


def record_stage(
    state: dict,
    name: str,
    status: str,
    now_kst: datetime,
    *,
    message: str | None = None,
    attempts: int | None = None,
) -> None:
    stage = {
        "status": status,
        "updated_at_kst": now_kst.isoformat(),
    }
    if message:
        stage["message"] = _safe_message(message)
    if attempts is not None:
        stage["attempts"] = attempts
    state.setdefault("batch", {}).setdefault("stages", {})[name] = stage
    _refresh_batch_status(state, now_kst)
    save_batch_state(state)


def _resolve_token_expiry(payload: dict, now_kst: datetime) -> datetime:
    official_expiry = _parse_datetime(payload.get("access_token_token_expired"))
    if official_expiry:
        return official_expiry
    try:
        expires_in = int(payload.get("expires_in", 0))
    except (TypeError, ValueError):
        expires_in = 0
    if expires_in > 0:
        return now_kst + timedelta(seconds=expires_in)
    # The official response normally contains both fields. If a compatible
    # proxy omits them, retain the issued token conservatively instead of
    # immediately making a duplicate issuance request.
    return now_kst + timedelta(hours=23)


def _reusable_token(
    state: dict,
    app_secret: str,
    now_kst: datetime,
) -> str | None:
    token_state = state.get("token")
    if not isinstance(token_state, dict):
        return None
    expires_at = _parse_datetime(token_state.get("expires_at_kst"))
    if not expires_at or expires_at <= now_kst + timedelta(
        seconds=TOKEN_EXPIRY_SAFETY_SECONDS
    ):
        return None
    stored_fingerprint = token_state.get("key_fingerprint")
    current_fingerprint = _secret_fingerprint(app_secret)
    if stored_fingerprint and stored_fingerprint != current_fingerprint:
        return None
    token = decrypt_access_token(
        str(token_state.get("ciphertext", "")),
        app_secret,
    )
    if not token:
        raise RuntimeError(
            "저장된 KIS 토큰을 복호화하지 못했습니다. 같은 시크릿으로 만료될 "
            "때까지 재발급을 중단합니다."
        )
    return token


def _request_is_cooling_down(state: dict, now_kst: datetime) -> bool:
    request_state = state.get("token_request")
    if not isinstance(request_state, dict):
        return False
    if request_state.get("status") not in {"running", "failed"}:
        return False
    if request_state.get("safe_to_retry") is True:
        return False
    attempted_at = _parse_datetime(request_state.get("attempted_at_kst"))
    if not attempted_at:
        return False
    return (now_kst - attempted_at).total_seconds() < TOKEN_REQUEST_COOLDOWN_SECONDS


def get_or_issue_access_token(
    state: dict,
    app_key: str,
    app_secret: str,
    now_kst: datetime,
) -> tuple[str, str]:
    token = _reusable_token(state, app_secret, now_kst)
    if token:
        return token, "reused"

    if _request_is_cooling_down(state, now_kst):
        raise RuntimeError(
            "직전 KIS 토큰 요청이 완료되지 않았거나 실패하여 재시도 대기 중입니다."
        )

    state["token_request"] = {
        "status": "running",
        "attempted_at_kst": now_kst.isoformat(),
    }
    save_batch_state(state)
    try:
        response = None
        for attempt in range(1, TOKEN_CONNECT_MAX_ATTEMPTS + 1):
            try:
                response = issue_access_token(app_key, app_secret)
                break
            except requests.ConnectTimeout:
                if attempt == TOKEN_CONNECT_MAX_ATTEMPTS:
                    raise
                delay = TOKEN_CONNECT_RETRY_DELAY_SECONDS * (2 ** (attempt - 1))
                print(
                    "KIS token endpoint connect timeout; no request was sent. "
                    f"Retrying in {delay} seconds ({attempt}/"
                    f"{TOKEN_CONNECT_MAX_ATTEMPTS})."
                )
                time.sleep(delay)
        if response is None:
            raise RuntimeError("KIS 토큰 응답을 받지 못했습니다.")
        token = str(response["access_token"])
        expires_at = _resolve_token_expiry(response, now_kst)
        state["token"] = {
            "cipher": "AES-256-GCM",
            "ciphertext": encrypt_access_token(token, app_secret),
            "issued_at_kst": now_kst.isoformat(),
            "expires_at_kst": expires_at.isoformat(),
            "key_fingerprint": _secret_fingerprint(app_secret),
        }
        state["token_request"] = {
            "status": "success",
            "attempted_at_kst": now_kst.isoformat(),
            "expires_at_kst": expires_at.isoformat(),
        }
        state.pop("token_request_date_kst", None)
        state.pop("requested_at_kst", None)
        save_batch_state(state)
        return token, "issued"
    except Exception as exc:
        safe_to_retry = isinstance(exc, requests.ConnectTimeout)
        state["token_request"] = {
            "status": "failed",
            "attempted_at_kst": now_kst.isoformat(),
            "message": _safe_message(exc),
            "safe_to_retry": safe_to_retry,
        }
        save_batch_state(state)
        raise RuntimeError(f"KIS 토큰 요청 실패: {_safe_message(exc)}") from exc


def run_with_retries(
    label: str,
    operation: Callable[[], dict],
    validator: Callable[[dict], bool],
    *,
    attempts: int = PREFETCH_MAX_ATTEMPTS,
    base_delay_seconds: int = PREFETCH_RETRY_DELAY_SECONDS,
) -> tuple[dict, int]:
    last_error = "응답이 완료 조건을 충족하지 않았습니다."
    for attempt in range(1, attempts + 1):
        print(f"{label}: attempt {attempt}/{attempts}")
        try:
            result = operation()
            if validator(result):
                return result, attempt
            last_error = "응답 기준일 또는 필수 데이터가 불완전합니다."
        except Exception as exc:
            last_error = _safe_message(exc)
        if attempt < attempts:
            delay = base_delay_seconds * (2 ** (attempt - 1))
            print(f"{label}: retrying with the same token in {delay} seconds")
            time.sleep(delay)
    raise RuntimeError(f"{label} 실패 ({attempts}회): {last_error}")


def _validate_run_time(now_kst: datetime) -> bool:
    allow_off_hours = os.environ.get("ALLOW_OFF_HOURS", "").lower() in {
        "1",
        "true",
        "yes",
    }
    if now_kst.weekday() > 4 and not allow_off_hours:
        print(f"weekend in KST ({now_kst:%Y-%m-%d %H:%M}); skipping batch")
        return False
    ready_at = now_kst.replace(
        hour=MARKET_DATA_READY_HOUR,
        minute=MARKET_DATA_READY_MINUTE,
        second=0,
        microsecond=0,
    )
    if now_kst < ready_at and not allow_off_hours:
        print(f"before KST {ready_at:%H:%M}; skipping without requesting a KIS token")
        return False
    if allow_off_hours and (now_kst.weekday() > 4 or now_kst < ready_at):
        print("manual recovery run: collecting the latest completed trading day")
    return True


def _credentials() -> tuple[str, str]:
    app_key = os.environ.get("KIS_APP_KEY", "").strip()
    app_secret = os.environ.get("KIS_APP_SECRET", "").strip()
    if not app_key or not app_secret:
        raise RuntimeError("환경변수 KIS_APP_KEY / KIS_APP_SECRET 이 필요합니다.")
    return app_key, app_secret


def run_priority_phase(now: datetime | None = None) -> None:
    now_kst = _as_kst(now)
    if not _validate_run_time(now_kst):
        return
    target_date = get_target_date(now_kst)
    state = prepare_batch_state(load_batch_state(), target_date, now_kst)
    save_batch_state(state)
    failures: list[str] = []

    us_stage = state["batch"]["stages"].get("us_liquidity", {})
    if us_stage.get("status") != "success":
        record_stage(state, "us_liquidity", "running", now_kst)
        try:
            us_cache, attempts = run_with_retries(
                "US liquidity cache",
                build_us_liquidity_cache,
                lambda payload: bool(payload),
            )
            save_us_liquidity_cache(us_cache)
            record_stage(
                state,
                "us_liquidity",
                "success",
                now_kst,
                message="미국 유동성 캐시 갱신 완료",
                attempts=attempts,
            )
        except Exception as exc:
            message = _safe_message(exc)
            record_stage(
                state,
                "us_liquidity",
                "failed",
                now_kst,
                message=message,
                attempts=PREFETCH_MAX_ATTEMPTS,
            )
            failures.append(message)

    existing_scan = load_scan_cache()
    existing_program = load_program_trade_cache()
    scan_ready = cache_has_target_date(existing_scan, target_date)
    program_ready = program_cache_has_target_date(existing_program, target_date)
    stages = state["batch"]["stages"]
    if (
        scan_ready
        and program_ready
        and state["batch"].get("status") == "success"
        and stages.get("us_liquidity", {}).get("status") == "success"
    ):
        print(f"all caches already current for {target_date}; no state change")
        return
    record_stage(
        state,
        "scanner",
        "success" if scan_ready else "pending",
        now_kst,
        message=(
            f"수급 캐시 기준일 {target_date} 확인"
            if scan_ready
            else f"수급 캐시 기준일 {target_date} 수집 대기"
        ),
    )

    if scan_ready and program_ready:
        record_stage(
            state,
            "program_trade",
            "success",
            now_kst,
            message=f"비차익 캐시 기준일 {target_date} 확인",
        )
    else:
        try:
            app_key, app_secret = _credentials()
            access_token, token_source = get_or_issue_access_token(
                state,
                app_key,
                app_secret,
                now_kst,
            )
            record_stage(
                state,
                "authentication",
                "success",
                now_kst,
                message=(
                    "기존 암호화 토큰 재사용"
                    if token_source == "reused"
                    else "새 토큰 발급 후 암호화 저장"
                ),
            )
        except Exception as exc:
            message = _safe_message(exc)
            record_stage(
                state,
                "authentication",
                "failed",
                now_kst,
                message=message,
            )
            if not program_ready:
                record_stage(
                    state,
                    "program_trade",
                    "failed",
                    now_kst,
                    message=f"인증 실패로 수집하지 못함: {message}",
                )
            failures.append(message)
            access_token = ""
            app_key = ""
            app_secret = ""

        if program_ready:
            record_stage(
                state,
                "program_trade",
                "success",
                now_kst,
                message=f"비차익 캐시 기준일 {target_date} 확인",
            )
        elif access_token:
            record_stage(state, "program_trade", "running", now_kst)
            try:
                program_cache, attempts = run_with_retries(
                    "program-trade cache",
                    lambda: build_program_trade_cache(
                        access_token,
                        app_key,
                        app_secret,
                        now=now_kst,
                    ),
                    lambda payload: program_cache_has_target_date(
                        payload,
                        target_date,
                    ),
                )
                save_program_trade_cache(program_cache)
                record_stage(
                    state,
                    "program_trade",
                    "success",
                    now_kst,
                    message=f"비차익 캐시 기준일 {target_date} 갱신 완료",
                    attempts=attempts,
                )
            except Exception as exc:
                message = _safe_message(exc)
                record_stage(
                    state,
                    "program_trade",
                    "failed",
                    now_kst,
                    message=message,
                    attempts=PREFETCH_MAX_ATTEMPTS,
                )
                failures.append(message)

    if failures:
        raise RuntimeError("; ".join(failures))


def run_scanner_phase(now: datetime | None = None) -> None:
    now_kst = _as_kst(now)
    if not _validate_run_time(now_kst):
        return
    target_date = get_target_date(now_kst)
    state = prepare_batch_state(load_batch_state(), target_date, now_kst)
    save_batch_state(state)
    existing_scan = load_scan_cache()
    if cache_has_target_date(existing_scan, target_date):
        if (
            state["batch"].get("status") == "success"
            and state["batch"]["stages"].get("scanner", {}).get("status") == "success"
        ):
            print(f"scan cache already current for {target_date}; no state change")
            return
        record_stage(
            state,
            "scanner",
            "success",
            now_kst,
            message=f"수급 캐시 기준일 {target_date} 확인",
        )
        return

    record_stage(state, "scanner", "running", now_kst)
    try:
        app_key, app_secret = _credentials()
        access_token, token_source = get_or_issue_access_token(
            state,
            app_key,
            app_secret,
            now_kst,
        )
        record_stage(
            state,
            "authentication",
            "success",
            now_kst,
            message=(
                "기존 암호화 토큰 재사용"
                if token_source == "reused"
                else "새 토큰 발급 후 암호화 저장"
            ),
        )
        completed_scan, attempts = run_with_retries(
            "scan cache",
            lambda: build_scan_cache(app_key, app_secret, access_token),
            lambda payload: cache_has_target_date(payload, target_date),
        )
        completed_scan = attach_previous_market_snapshots(
            existing_scan,
            completed_scan,
        )
        save_scan_cache(completed_scan)
        record_stage(
            state,
            "scanner",
            "success",
            now_kst,
            message=f"수급 캐시 기준일 {target_date} 갱신 완료: {CACHE_FILE.name}",
            attempts=attempts,
        )
    except Exception as exc:
        message = _safe_message(exc)
        record_stage(
            state,
            "scanner",
            "failed",
            now_kst,
            message=message,
        )
        raise


def mark_workflow_failure(phase: str, now: datetime | None = None) -> None:
    now_kst = _as_kst(now)
    state = load_batch_state()
    batch = state.get("batch")
    if not isinstance(batch, dict):
        return
    stages = batch.get("stages", {})
    names = (
        ("us_liquidity", "authentication", "program_trade")
        if phase == "priority"
        else ("scanner",)
    )
    changed = False
    for name in names:
        if stages.get(name, {}).get("status") == "running":
            stages[name] = {
                "status": "failed",
                "updated_at_kst": now_kst.isoformat(),
                "message": "GitHub Actions 단계가 실패했거나 제한시간을 초과했습니다.",
            }
            changed = True
    if changed:
        _refresh_batch_status(state, now_kst)
        save_batch_state(state)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase",
        choices=("priority", "scanner", "all"),
        default="all",
    )
    parser.add_argument(
        "--mark-workflow-failure",
        choices=("priority", "scanner"),
    )
    arguments = parser.parse_args(argv)
    if arguments.mark_workflow_failure:
        mark_workflow_failure(arguments.mark_workflow_failure)
        return

    if arguments.phase == "priority":
        run_priority_phase()
        return
    if arguments.phase == "scanner":
        run_scanner_phase()
        return

    failures = []
    for phase in (run_priority_phase, run_scanner_phase):
        try:
            phase()
        except Exception as exc:
            failures.append(_safe_message(exc))
    if failures:
        raise RuntimeError("; ".join(failures))


if __name__ == "__main__":
    main()

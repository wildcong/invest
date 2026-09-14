from __future__ import annotations

import fcntl
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterator

from market_data import KST
from prefetch_scan_cache import run_scanner_phase
from scanner import cache_has_target_date, get_target_date, load_scan_cache


STATE_FILE = Path(__file__).parent / "data" / "manual_refresh_state.json"
LOCK_FILE = Path(__file__).parent / "data" / "manual_refresh.lock"
RETRY_COOLDOWN = timedelta(minutes=30)
RUNNING_TIMEOUT = timedelta(minutes=70)
_PROCESS_LOCK = threading.Lock()


class ManualRefreshError(RuntimeError):
    """A safe, user-facing manual refresh failure."""


class ManualRefreshBusy(ManualRefreshError):
    pass


class ManualRefreshCooldown(ManualRefreshError):
    def __init__(self, retry_at: datetime):
        self.retry_at = retry_at
        super().__init__(f"재시도 가능 시각은 {retry_at:%H:%M KST}입니다.")


@dataclass(frozen=True)
class ManualRefreshResult:
    target_date: str
    status: str
    message: str


@dataclass(frozen=True)
class ManualRefreshAvailability:
    can_run: bool
    status: str
    retry_at: datetime | None = None


def _as_kst(value: datetime | None = None) -> datetime:
    current = value or datetime.now(KST)
    if current.tzinfo is None:
        return current.replace(tzinfo=KST)
    return current.astimezone(KST)


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def _safe_message(value: object, limit: int = 400) -> str:
    return " ".join(str(value).split())[:limit]


def load_manual_refresh_state(path: Path = STATE_FILE) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_manual_refresh_state(payload: dict, path: Path = STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary_file:
            json.dump(payload, temporary_file, ensure_ascii=False, separators=(",", ":"))
        os.replace(temporary_path, path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def manual_refresh_availability(
    target_date: str,
    *,
    now: datetime | None = None,
    state_path: Path = STATE_FILE,
) -> ManualRefreshAvailability:
    now_kst = _as_kst(now)
    state = load_manual_refresh_state(state_path)
    if state.get("target_date") != target_date:
        return ManualRefreshAvailability(True, "ready")

    started_at = _parse_datetime(state.get("started_at_kst"))
    if (
        state.get("status") == "running"
        and started_at
        and now_kst - started_at < RUNNING_TIMEOUT
    ):
        return ManualRefreshAvailability(False, "running")

    retry_at = _parse_datetime(state.get("retry_at_kst"))
    if retry_at and now_kst < retry_at:
        return ManualRefreshAvailability(False, "cooldown", retry_at)
    return ManualRefreshAvailability(True, "ready")


@contextmanager
def _kis_environment(app_key: str, app_secret: str) -> Iterator[None]:
    updates = {
        "KIS_APP_KEY": app_key,
        "KIS_APP_SECRET": app_secret,
        "ALLOW_OFF_HOURS": "true",
    }
    previous = {name: os.environ.get(name) for name in updates}
    os.environ.update(updates)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def run_direct_scan_refresh(
    app_key: str,
    app_secret: str,
    *,
    now: datetime | None = None,
    state_path: Path = STATE_FILE,
    lock_path: Path = LOCK_FILE,
    scanner_runner: Callable[[datetime | None], None] = run_scanner_phase,
    cache_loader: Callable[[], dict] = load_scan_cache,
) -> ManualRefreshResult:
    if not app_key.strip() or not app_secret.strip():
        raise ManualRefreshError("Streamlit에 KIS API 키가 설정되지 않았습니다.")

    now_kst = _as_kst(now)
    target_date = get_target_date(now_kst)
    if cache_has_target_date(cache_loader(), target_date):
        return ManualRefreshResult(target_date, "already_current", "이미 최신 데이터입니다.")

    if not _PROCESS_LOCK.acquire(blocking=False):
        raise ManualRefreshBusy("다른 수동 갱신이 실행 중입니다.")

    lock_handle = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_handle = lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ManualRefreshBusy("다른 수동 갱신이 실행 중입니다.") from exc

        # Check again inside both locks so simultaneous button presses cannot
        # pass the freshness test together.
        if cache_has_target_date(cache_loader(), target_date):
            return ManualRefreshResult(
                target_date, "already_current", "이미 최신 데이터입니다."
            )

        availability = manual_refresh_availability(
            target_date,
            now=now_kst,
            state_path=state_path,
        )
        if availability.status == "running":
            raise ManualRefreshBusy("다른 수동 갱신이 실행 중입니다.")
        if availability.status == "cooldown" and availability.retry_at:
            raise ManualRefreshCooldown(availability.retry_at)

        save_manual_refresh_state(
            {
                "target_date": target_date,
                "status": "running",
                "started_at_kst": now_kst.isoformat(),
            },
            state_path,
        )
        try:
            with _kis_environment(app_key.strip(), app_secret.strip()):
                scanner_runner(now_kst)
            refreshed_cache = cache_loader()
            complete = cache_has_target_date(refreshed_cache, target_date)
            partial = cache_has_target_date(
                refreshed_cache,
                target_date,
                allow_partial=True,
            )
            if not complete and not partial:
                raise RuntimeError("수집 결과가 게시 가능한 최소 기준에 미달했습니다.")

            finished_at = datetime.now(KST)
            status = "success" if complete else "degraded"
            message = (
                "수동 갱신이 완료됐습니다."
                if complete
                else "일부 종목만 갱신됐습니다. 30분 뒤 미갱신 종목을 다시 조회할 수 있습니다."
            )
            state = {
                "target_date": target_date,
                "status": status,
                "started_at_kst": now_kst.isoformat(),
                "finished_at_kst": finished_at.isoformat(),
                "message": message,
            }
            if not complete:
                state["retry_at_kst"] = (finished_at + RETRY_COOLDOWN).isoformat()
            save_manual_refresh_state(state, state_path)
            return ManualRefreshResult(target_date, status, message)
        except Exception as exc:
            if isinstance(exc, ManualRefreshError):
                raise
            failed_at = datetime.now(KST)
            message = _safe_message(exc)
            save_manual_refresh_state(
                {
                    "target_date": target_date,
                    "status": "failed",
                    "started_at_kst": now_kst.isoformat(),
                    "finished_at_kst": failed_at.isoformat(),
                    "retry_at_kst": (failed_at + RETRY_COOLDOWN).isoformat(),
                    "message": message,
                },
                state_path,
            )
            raise ManualRefreshError(f"수동 갱신 실패: {message}") from exc
    finally:
        if lock_handle is not None:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            finally:
                lock_handle.close()
        _PROCESS_LOCK.release()

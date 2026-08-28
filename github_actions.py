from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import requests


REPOSITORY = "wildcong/invest"
WORKFLOW_FILE = "prefetch-scan-cache.yml"
WORKFLOW_REF = "main"
WORKFLOW_URL = (
    f"https://github.com/{REPOSITORY}/actions/workflows/{WORKFLOW_FILE}"
)
DISPATCH_URL = (
    f"https://api.github.com/repos/{REPOSITORY}/actions/workflows/"
    f"{WORKFLOW_FILE}/dispatches"
)


class WorkflowDispatchError(RuntimeError):
    """A safe, user-facing workflow dispatch failure."""


@dataclass(frozen=True)
class WorkflowDispatchResult:
    run_url: str
    run_id: int | None = None


def dispatch_market_cache_workflow(
    token: str,
    *,
    request_post: Callable = requests.post,
) -> WorkflowDispatchResult:
    clean_token = token.strip()
    if not clean_token:
        raise WorkflowDispatchError("GitHub Actions 실행 토큰이 설정되지 않았습니다.")

    try:
        response = request_post(
            DISPATCH_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {clean_token}",
                "X-GitHub-Api-Version": "2026-03-10",
                "User-Agent": "wildcong-invest-streamlit",
            },
            json={"ref": WORKFLOW_REF},
            timeout=15,
        )
    except requests.RequestException as exc:
        raise WorkflowDispatchError(
            "GitHub에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요."
        ) from exc

    if response.status_code not in {200, 204}:
        if response.status_code in {401, 403}:
            message = (
                "GitHub 토큰 인증 또는 Actions 쓰기 권한을 확인해 주세요."
            )
        elif response.status_code == 404:
            message = "GitHub 저장소 또는 수동 갱신 워크플로를 찾지 못했습니다."
        elif response.status_code == 422:
            message = "GitHub가 main 브랜치의 수동 실행 요청을 처리하지 못했습니다."
        else:
            message = f"GitHub 수동 실행 요청이 실패했습니다. (HTTP {response.status_code})"
        raise WorkflowDispatchError(message)

    if response.status_code == 204:
        return WorkflowDispatchResult(run_url=WORKFLOW_URL)

    try:
        payload = response.json()
    except (requests.JSONDecodeError, ValueError):
        payload = {}
    run_url = str(payload.get("html_url") or WORKFLOW_URL)
    try:
        run_id = int(payload["workflow_run_id"])
    except (KeyError, TypeError, ValueError):
        run_id = None
    return WorkflowDispatchResult(run_url=run_url, run_id=run_id)

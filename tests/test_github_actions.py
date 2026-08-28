import unittest
from unittest.mock import Mock

import requests

from github_actions import (
    DISPATCH_URL,
    WORKFLOW_URL,
    WorkflowDispatchError,
    dispatch_market_cache_workflow,
)


class WorkflowDispatchTests(unittest.TestCase):
    def test_dispatch_returns_exact_run_url(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "workflow_run_id": 123,
            "html_url": "https://github.com/wildcong/invest/actions/runs/123",
        }
        request_post = Mock(return_value=response)

        result = dispatch_market_cache_workflow(
            "test-token",
            request_post=request_post,
        )

        self.assertEqual(result.run_id, 123)
        self.assertEqual(
            result.run_url,
            "https://github.com/wildcong/invest/actions/runs/123",
        )
        request_post.assert_called_once()
        args, kwargs = request_post.call_args
        self.assertEqual(args[0], DISPATCH_URL)
        self.assertEqual(kwargs["json"], {"ref": "main"})
        self.assertEqual(kwargs["timeout"], 15)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-token")

    def test_legacy_empty_success_uses_workflow_page(self):
        response = Mock(status_code=204)
        request_post = Mock(return_value=response)

        result = dispatch_market_cache_workflow(
            "test-token",
            request_post=request_post,
        )

        self.assertEqual(result.run_url, WORKFLOW_URL)
        self.assertIsNone(result.run_id)

    def test_permission_failure_has_safe_message(self):
        response = Mock(status_code=403)

        with self.assertRaisesRegex(WorkflowDispatchError, "Actions 쓰기 권한"):
            dispatch_market_cache_workflow(
                "secret-token-value",
                request_post=Mock(return_value=response),
            )

    def test_network_failure_has_safe_message(self):
        with self.assertRaisesRegex(WorkflowDispatchError, "GitHub에 연결"):
            dispatch_market_cache_workflow(
                "secret-token-value",
                request_post=Mock(side_effect=requests.ConnectionError("offline")),
            )


if __name__ == "__main__":
    unittest.main()

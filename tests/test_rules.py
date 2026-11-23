import io
import unittest
from contextlib import redirect_stdout
from subprocess import CompletedProcess
from unittest.mock import patch

from check import check


class RuleTests(unittest.TestCase):
    def test_metric_names_in_nested_selectors(self):
        parsed = {
            "type": "binaryExpr",
            "lhs": {
                "type": "call",
                "func": {"name": "rate"},
                "args": [{
                    "type": "matrixSelector",
                    "name": "http_requests_total",
                    "matchers": [{"name": "__name__", "type": "=", "value": "http_requests_total"}],
                    "offset": 3600000,
                }],
            },
            "rhs": {
                "type": "binaryExpr",
                "lhs": {
                    "type": "vectorSelector",
                    "name": "",
                    "matchers": [{"name": "__name__", "type": "=", "value": "up"}],
                },
                "rhs": {
                    "type": "vectorSelector",
                    "name": "",
                    "matchers": [
                        {"name": "__name__", "type": "=~", "value": "http_.*"},
                        {"name": "job", "type": "=", "value": "app"},
                    ],
                },
            },
        }

        self.assertEqual(set(check.metric_names(parsed)), {"http_requests_total", "up"})

    def test_recording_only_setup_fails_before_reloading(self):
        with (
            patch.object(check, "rule_files", return_value=["recording.yaml"]),
            patch.object(check, "alert_rules", return_value=[]),
            patch.object(check.subprocess, "run", return_value=CompletedProcess([], 0)),
            patch.object(check, "reload_prometheus") as reload,
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(check.check_rules(), 1)

        self.assertIn("알람 룰이 없음", output.getvalue())
        reload.assert_not_called()

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from check import check


def series(name, state, timestamps):
    return {
        "metric": {"alertname": name, "alertstate": state},
        "values": [[ts, "1"] for ts in timestamps],
    }


class ResultTests(unittest.TestCase):
    def test_episode_ends_at_first_missing_step(self):
        self.assertEqual(check.episodes([995.123, 1005.123]), [
            (995.123, 1000.123),
            (1005.123, 1010.123),
        ])

    def run_result(self, events, now, responses):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "fault.log")
            path.write_text("\n".join(map(json.dumps, events)), encoding="utf-8")
            output = io.StringIO()
            with (
                redirect_stdout(output),
                patch.object(check, "LOG", path),
                patch.object(check.time, "time", return_value=now),
                patch.object(check, "api", side_effect=responses) as api,
            ):
                code = check.result()
        return code, output.getvalue(), api.call_args_list

    def test_stopped_run_counts_continuing_and_new_fires_separately(self):
        events = [
            {"event": "armed", "start": 800, "end": 850, "ts": 795},
            {"event": "on", "ts": 800},
            {"event": "off", "ts": 851},
            {"event": "armed", "start": 990, "end": 1200, "ts": 988},
            {"event": "on", "ts": 1000, "late_by_s": 10},
            {"event": "stopped", "ts": 1040},
            {"event": "off", "ts": 1045},
        ]
        code, output, calls = self.run_result(events, 1210, [
            {"result": [{"values": [[ts, "1"] for ts in range(1000, 1040, 5)]}]},
            {"result": [
                series("Restarted", "firing", [990, 995, 1000, 1005, 1025, 1030, 1035]),
                series("Continuing", "firing", [995, 1000, 1005]),
                series("AtStop", "firing", [1040]),
            ]},
            {"groups": []},
        ])

        self.assertEqual(code, 0)
        self.assertIn("총 0분 40초", output)
        self.assertIn("첫 새 울림: 장애 시작 후 0분 25초", output)
        self.assertIn("새 울림 1회 · 장애 중 울린 시간 0분 25초 / 0분 40초", output)
        self.assertIn("장애 시작 전부터 이어진 울림 있음", output)
        self.assertIn("새 울림 0회 · 장애 중 울린 시간 0분 10초 / 0분 40초", output)
        self.assertNotIn("AtStop", output)
        self.assertEqual(calls[0].args[1]["start"], 1000)
        self.assertEqual(calls[0].args[1]["end"], 1040)
        self.assertEqual(calls[1].args[1]["start"], 400)
        self.assertEqual(calls[1].args[1]["end"], 1040)

    def test_in_progress_metrics_use_elapsed_time(self):
        events = [
            {"event": "armed", "start": 1000, "end": 1200, "ts": 999},
            {"event": "on", "ts": 1000},
        ]
        code, output, calls = self.run_result(events, 1017, [
            {"result": [{"values": [[ts, "1"] for ts in range(1000, 1017, 5)]}]},
            {"result": [
                series("Current", "firing", [1005, 1010, 1015]),
                series("Earlier", "firing", [900, 905]),
                series("PendingOnly", "pending", [1010, 1015]),
            ]},
            {"groups": []},
        ])

        self.assertEqual(code, 0)
        self.assertIn("중간 결과", output)
        self.assertIn("울린 시간 0분 12초 / 0분 17초 (71%)", output)
        self.assertIn("장애 중 울림 없음 · Pending 기록만 있음", output)
        self.assertIn("가장 길게 0분 7초", output)
        self.assertIn("Earlier\n      장애 전에도 울림 관측", output)
        self.assertEqual(output.count("장애 중 울림 없음"), 2)
        self.assertEqual(calls[0].args[1]["end"], 1017)

    def test_missing_prometheus_samples_late_in_run_prevent_verdict(self):
        events = [
            {"event": "armed", "start": 1000, "end": 1200, "ts": 999},
            {"event": "on", "ts": 1000},
        ]
        for has_failed_sample in (False, True):
            with self.subTest(has_failed_sample=has_failed_sample):
                values = [[ts, "1"] for ts in range(1000, 1200, 5) if ts != 1120]
                if has_failed_sample:
                    values.append([1120, "0"])
                code, output, calls = self.run_result(events, 1210, [
                    {"result": [{"values": values}]},
                ])

                self.assertEqual(code, 1)
                self.assertIn("판정할 수 없음", output)
                self.assertEqual(len(calls), 1)
                self.assertIn('timestamp(up{job="prometheus"})', calls[0].args[1]["query"])

    def test_cancelled_before_activation_does_not_query_prometheus(self):
        events = [
            {"event": "armed", "start": 1000, "end": 1200, "ts": 990},
            {"event": "stopped", "ts": 995},
        ]
        code, output, calls = self.run_result(events, 1017, [])

        self.assertEqual(code, 1)
        self.assertIn("켜지기 전에 취소됨", output)
        self.assertEqual(calls, [])

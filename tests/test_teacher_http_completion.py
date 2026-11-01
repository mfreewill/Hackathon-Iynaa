"""Socket-free completion helpers for publication behavior.

Pure reserve-lifetime and no-op-publication discrimination over
real domain snapshots from the test fixture. No HTTP, sockets,
or provider. The HTTP behaviour built on these helpers is proved
by the ``test_http_teacher_*`` suites.
"""
import copy
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from teacher_demo.http_teacher_publication import (  # noqa: E402
    TERMINAL_SLACK_BYTES,
    is_noop_publication,
    retain_terminal_reserves,
)
from test_question_request_prompt import ready_workspace  # noqa: E402


class RetainTerminalReservesTests(unittest.TestCase):
    def test_working_request_keeps_full_reserve(self):
        ws, _ = ready_workspace()
        qid = ws.begin_question_request("q-complete-working")
        reserves = {qid: TERMINAL_SLACK_BYTES + 321}
        self.assertEqual(
            retain_terminal_reserves(reserves, ws.snapshot(), {}),
            {qid: TERMINAL_SLACK_BYTES + 321})

    def test_closed_without_raw_status_keeps_only_closure_slack(self):
        ws, _ = ready_workspace()
        qid = ws.begin_question_request("q-complete-closed")
        ws.cancel_question_request(qid)
        reserves = {qid: TERMINAL_SLACK_BYTES + 321}
        self.assertEqual(
            retain_terminal_reserves(reserves, ws.snapshot(), {}),
            {qid: TERMINAL_SLACK_BYTES})

    def test_closed_with_raw_status_releases_and_unknown_drops(self):
        ws, _ = ready_workspace()
        qid = ws.begin_question_request("q-complete-published")
        ws.finish_question_request(qid, "error")
        reserves = {qid: TERMINAL_SLACK_BYTES + 321, "ghost": 99}
        before = dict(reserves)
        self.assertEqual(
            retain_terminal_reserves(
                reserves, ws.snapshot(), {qid: "omitted_capacity"}),
            {})
        self.assertEqual(reserves, before)  # input never mutated

    def test_no_snapshot_releases_everything(self):
        self.assertEqual(
            retain_terminal_reserves({"x": 1}, None, {}), {})


class NoopPublicationTests(unittest.TestCase):
    def _args(self, ws):
        snapshot = ws.snapshot()
        return {
            "before_snapshot": snapshot, "after_snapshot": snapshot,
            "before_pending": ("g", "o", "r"),
            "after_pending": ("g", "o", "r"),
            "before_graph_cost": 1234, "after_graph_cost": 1234,
            "before_reserves": {"r1": 10}, "after_reserves": {"r1": 10},
        }

    def test_all_dimensions_equal_is_noop(self):
        ws, _ = ready_workspace()
        self.assertTrue(is_noop_publication(**self._args(ws)))

    def test_snapshot_change_is_not_noop(self):
        ws, _ = ready_workspace()
        args = self._args(ws)
        changed = copy.deepcopy(ws)
        changed.set_grade_objective("  another grade  ", ws.objective)
        args["after_snapshot"] = changed.snapshot()
        self.assertFalse(is_noop_publication(**args))

    def test_pending_change_is_not_noop(self):
        ws, _ = ready_workspace()
        args = self._args(ws)
        args["after_pending"] = ("g", "o", "other-rights")
        self.assertFalse(is_noop_publication(**args))

    def test_graph_or_reserve_change_is_not_noop(self):
        ws, _ = ready_workspace()
        args = self._args(ws)
        args["after_graph_cost"] = 1235
        self.assertFalse(is_noop_publication(**args))
        args = self._args(ws)
        args["after_reserves"] = {}
        self.assertFalse(is_noop_publication(**args))


if __name__ == "__main__":
    unittest.main()

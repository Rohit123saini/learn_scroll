"""python -m unittest loadtest.tests_sizing   (pure; no Django)"""
import unittest

from loadtest.sizing import Inputs, size


class SizingTests(unittest.TestCase):
    def test_littles_law(self):
        o = size(Inputs(tasks_per_sec=20, avg_task_seconds=0.5, target_utilization=0.5))
        self.assertEqual(o["celery"]["busy_workers_at_peak"], 10.0)
        self.assertEqual(o["celery"]["worker_slots_needed"], 20)

    def test_monotonic_in_users(self):
        a, b = size(Inputs(users=500)), size(Inputs(users=5000))
        self.assertGreaterEqual(b["asgi"]["processes"], a["asgi"]["processes"])
        self.assertGreater(b["asgi"]["websocket_connections"], a["asgi"]["websocket_connections"])

    def test_ignore_results_removes_result_memory_and_warning(self):
        o = size(Inputs(ignore_results=True))
        self.assertEqual(o["celery"]["result_backend_mb_steady_state"], 0.0)
        self.assertFalse(any("Result backend" in w for w in o["warnings"]))

    def test_fanout_math(self):
        o = size(Inputs(chat_users=60, chat_msgs_per_user_per_min=1, room_size=10))
        self.assertEqual(o["chat_fanout"]["messages_per_sec"], 1.0)
        self.assertEqual(o["chat_fanout"]["deliveries_per_sec"], 10.0)

    def test_always_at_least_two_processes(self):
        o = size(Inputs(users=10, chat_users=0, live_users=0, tasks_per_sec=0.1))
        self.assertGreaterEqual(o["asgi"]["processes"], 2)
        self.assertGreaterEqual(o["celery"]["worker_processes_at_current_concurrency"], 2)


if __name__ == "__main__":
    unittest.main()

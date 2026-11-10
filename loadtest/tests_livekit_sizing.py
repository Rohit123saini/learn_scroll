"""python -m unittest loadtest.tests_livekit_sizing   (pure; no Django, no network)"""
import unittest

from loadtest.livekit_sizing import Inputs, size


class LiveKitSizingTests(unittest.TestCase):
    def test_one_to_one_audio_math(self):
        # 100 audio-only calls: 200 mics x 32 kbps = 6.4 Mbps in, and the same forwarded out
        o = size(Inputs(calls=100, call_video_share=0.0, group_calls=0, rooms=0))
        self.assertEqual(o["workload"]["calls_1to1"]["participants"], 200)
        self.assertAlmostEqual(o["workload"]["calls_1to1"]["ingress_mbps"], 6.4, places=2)
        self.assertAlmostEqual(o["workload"]["calls_1to1"]["egress_mbps"], 6.4, places=2)

    def test_study_room_fanout_is_per_subscriber(self):
        # 1 room, 50 people, 1 open mic, no camera, no screen: 49-ish subscribers x 32 kbps ~= 50 x 32 kbps = 1.6 Mbps out
        o = size(Inputs(calls=0, group_calls=0, rooms=1, room_size=50, room_mic_publishers=1,
                        room_video_publishers=0, room_screenshare=0.0))
        self.assertAlmostEqual(o["workload"]["study_rooms"]["egress_mbps"], 1.6, places=2)
        self.assertAlmostEqual(o["workload"]["study_rooms"]["ingress_mbps"], 0.03, places=2)   # workload numbers are rounded to 2 decimals

    def test_group_call_forwards_every_mic(self):
        o = size(Inputs(calls=0, rooms=0, group_calls=1, group_size=10, group_video_tiles=0, group_video_publishers=0))
        # each of 10 listeners gets 9 mics x 32 kbps -> 10 x 9 x 32 = 2880 kbps
        self.assertAlmostEqual(o["workload"]["group_calls"]["egress_mbps"], 2.88, places=2)

    def test_monotonic_in_rooms(self):
        a, b = size(Inputs(rooms=10)), size(Inputs(rooms=200))
        self.assertGreaterEqual(b["nodes"]["total"], a["nodes"]["total"])
        self.assertGreater(b["load"]["egress_mbps"], a["load"]["egress_mbps"])

    def test_udp_port_cap_can_bind(self):
        # 6,000 participants x 2 PCs = 12,000 ports > 10,001 on one node -> at least 2 nodes by ports
        o = size(Inputs(calls=0, group_calls=0, rooms=120, room_size=50, room_mic_publishers=0,
                        room_video_publishers=0, room_screenshare=0.0))
        self.assertGreaterEqual(o["nodes"]["by_udp_ports"], 2)

    def test_spare_node_is_added(self):
        o = size(Inputs(spare_nodes=1))
        self.assertEqual(o["nodes"]["total"], o["nodes"]["needed"] + 1)
        o0 = size(Inputs(calls=0, group_calls=0, rooms=0))
        self.assertEqual(o0["nodes"]["total"], 0)

    def test_default_assumption_warning_goes_away_after_measurement(self):
        self.assertTrue(any("DEFAULT ASSUMPTION" in w for w in size(Inputs())["warnings"]))
        self.assertFalse(any("DEFAULT ASSUMPTION" in w for w in size(Inputs(mbps_per_core=200.0))["warnings"]))

    def test_join_storm_rate(self):
        o = size(Inputs(calls=0, group_calls=0, rooms=20, room_size=50, join_window_seconds=10))
        self.assertEqual(o["django"]["study_join_requests_per_sec_during_storm"], 100.0)

    def test_biggest_room_check(self):
        o = size(Inputs(calls=0, group_calls=0, rooms=1, room_size=500, cores_per_node=1))
        self.assertFalse(o["biggest_room"]["fits_one_node"])
        self.assertTrue(any("does not fit ONE node" in w for w in o["warnings"]))


if __name__ == "__main__":
    unittest.main()

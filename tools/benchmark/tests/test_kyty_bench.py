import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parents[1] / "kyty_bench.py"
SPEC = importlib.util.spec_from_file_location("kyty_bench", MODULE_PATH)
kyty_bench = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(kyty_bench)


def mock_csv_files(contents):
    """Patch Path.open with independent in-memory CSV streams (no disk access)."""

    def opener(path, *args, **kwargs):
        return io.StringIO(contents[path.name])

    return mock.patch("pathlib.Path.open", new=opener)


class FrameMetricsTests(unittest.TestCase):
    def test_nearest_rank_percentiles(self):
        values = list(range(1, 101))
        self.assertEqual(kyty_bench.percentile_nearest_rank(values, 95), 95)
        self.assertEqual(kyty_bench.percentile_nearest_rank(values, 99), 99)
        self.assertEqual(kyty_bench.percentile_nearest_rank([7, 1, 3], 100), 7)

    def test_frame_metrics_and_low_fps(self):
        # 10 + 10 + 20 + 40 = 80 ms: four frames at 50 average FPS.
        report = kyty_bench.analyze_frame_intervals([10_000, 10_000, 20_000, 40_000])
        self.assertAlmostEqual(report["average_fps"], 50.0)
        self.assertAlmostEqual(report["frame_time_ms"]["mean"], 20.0)
        self.assertAlmostEqual(report["frame_time_ms"]["p95"], 40.0)
        self.assertAlmostEqual(report["low_fps"]["1_percent"], 25.0)
        self.assertAlmostEqual(report["low_fps"]["0_1_percent"], 25.0)
        self.assertEqual(report["stutters"]["over_33_333_ms"], 1)
        self.assertEqual(report["stutters"]["over_2x_median"], 1)

    def test_low_fps_uses_the_slowest_one_percent_not_one_frame_of_a_short_run(self):
        intervals = [10_000] * 99 + [100_000]
        # 100 frames over 1.09 s; the slowest 1% is exactly the 100 ms frame.
        report = kyty_bench.analyze_frame_intervals(intervals)
        self.assertAlmostEqual(report["average_fps"], 100 / 1.09)
        self.assertAlmostEqual(report["low_fps"]["1_percent"], 10.0)

    def test_empty_and_nonpositive_frames_are_rejected(self):
        with self.assertRaises(ValueError):
            kyty_bench.analyze_frame_intervals([])
        with self.assertRaises(ValueError):
            kyty_bench.analyze_frame_intervals([10_000, 0])


class CsvTests(unittest.TestCase):
    def test_frame_log_with_optional_header_and_comments(self):
        with mock_csv_files({"frames.csv": "t_us,interval_us\n# comment\n1000,10000\n11000,20000\n"}):
            report = kyty_bench.read_frame_log(Path("frames.csv"))
        self.assertEqual(report["frames"], 2)
        self.assertAlmostEqual(report["average_fps"], 2 / 0.03)

    def test_compile_stalls_ignore_unrelated_waits(self):
        waits = (
            "begin_us,end_us,tid,filter,ident,events,name\n"
            "0,10000,1,-100,0xabc,0,compile wait\n"
            "10000,30000,2,-103,0x0,0,pipeline mutex\n"
            "0,90000,3,5,0x0,1,unrelated guest wait\n"
        )
        with mock_csv_files({"waits.csv": waits}):
            report = kyty_bench.analyze_waits(Path("waits.csv"))
        self.assertEqual(report["count"], 2)
        self.assertAlmostEqual(report["total_thread_ms"], 30.0)
        self.assertAlmostEqual(report["max_ms"], 20.0)
        self.assertEqual(report["by_kind"]["compile_wait"]["count"], 1)
        self.assertEqual(report["by_kind"]["pipeline_mutex"]["count"], 1)

    def test_pace_metrics(self):
        pace = (
            "present_us,vblank,reserve_us,ready_us,flipsub_us,framesub_us,cpstart_us,done_us,"
            "donewait_us,present_cost_us,gpu_busy_us,gpu_idle_us,gpu_starved_us,gpu_cmdbufs,gpu_last_end_us\n"
            "0,1,0,0,0,0,0,0,1000,2000,8000,2000,100,12,0\n"
            "10000,2,0,0,0,0,0,0,3000,1000,4000,6000,200,8,0\n"
        )
        with mock_csv_files({"pace.csv": pace}):
            report = kyty_bench.analyze_pace(Path("pace.csv"))
        self.assertEqual(report["rows"], 2)
        self.assertEqual(report["gpu_cmdbufs_total"], 20)
        self.assertAlmostEqual(report["gpu_cmdbufs_per_row"], 10.0)
        self.assertAlmostEqual(report["gpu_busy_share_of_busy_plus_idle"], 0.6)
        self.assertAlmostEqual(report["guest_done_wait_ms"], 4.0)
        self.assertAlmostEqual(report["present_cost_ms"], 3.0)

    def test_full_analysis_and_comparison(self):
        pace = (
            "present_us,vblank,reserve_us,ready_us,flipsub_us,framesub_us,cpstart_us,done_us,"
            "donewait_us,present_cost_us,gpu_busy_us,gpu_idle_us,gpu_starved_us,gpu_cmdbufs,gpu_last_end_us\n"
            "0,1,0,0,0,0,0,0,0,0,1000,1000,0,10,0\n"
        )
        waits = "begin_us,end_us,tid,filter,ident,events,name\n0,5000,1,-100,0x0,0,wait\n"
        files = {"frames.csv": "0,10000\n10000,10000\n", "pace.csv": pace, "waits.csv": waits}
        with mock_csv_files(files):
            baseline = kyty_bench.analyze(Path("frames.csv"), Path("pace.csv"), Path("waits.csv"), "baseline")
        with mock_csv_files(files):
            candidate = kyty_bench.analyze(Path("frames.csv"), Path("pace.csv"), Path("waits.csv"), "candidate")
        candidate["frames"]["average_fps"] = 120.0
        candidate["frames"]["low_fps"]["1_percent"] = 80.0
        candidate["frames"]["frame_time_ms"]["mean"] = 8.0
        candidate["frames"]["frame_time_ms"]["p95"] = 12.0
        candidate["frames"]["frame_time_ms"]["p99"] = 16.0
        candidate["compile_stalls"]["count"] = 0
        candidate["pace"]["gpu_cmdbufs_per_row"] = 5.0
        comparison = kyty_bench.compare_reports(baseline, candidate)
        self.assertAlmostEqual(comparison["average_fps_change_percent"], 20.0)
        self.assertAlmostEqual(comparison["low_1_percent_fps_change_percent"], -20.0)
        self.assertAlmostEqual(comparison["mean_frame_time_change_percent"], -20.0)
        self.assertEqual(comparison["compile_stall_count_change"], -1)
        self.assertAlmostEqual(comparison["gpu_cmdbufs_per_row_change_percent"], -50.0)
        self.assertIn("Average FPS", kyty_bench.format_comparison(baseline, candidate, comparison))

    def test_cli_produces_machine_readable_report_without_disk_writes(self):
        with mock_csv_files({"frames.csv": "0,10000\n10000,20000\n"}):
            with mock.patch.object(kyty_bench, "write_json") as write_json:
                with contextlib.redirect_stdout(io.StringIO()):
                    result = kyty_bench.main(
                        ["analyze", "--frames", "frames.csv", "--label", "smoke", "--output", "report.json"]
                    )
        self.assertEqual(result, 0)
        write_json.assert_called_once()
        report = write_json.call_args.args[1]
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["label"], "smoke")
        self.assertEqual(report["frames"]["frames"], 2)
        # Verify that the report itself is serializable for the real --output path.
        json.dumps(report)


if __name__ == "__main__":
    unittest.main()

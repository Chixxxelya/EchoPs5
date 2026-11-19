#!/usr/bin/env python3
"""Analyze Kyty frame-time, pace and stall CSV files without third-party packages.

The frame log is written by KYTY_FRAME_TIMES_LOG as "t_us,interval_us" without a
header. The pace log and its optional ".waits.csv" are written by KYTY_PACE_LOG.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Iterable, Sequence

SCHEMA_VERSION = 1
COMPILE_STALL_NAMES = {
    -100: "compile_wait",
    -101: "build",
    -102: "optimize",
    -103: "pipeline_mutex",
    -104: "driver_cache_probe",
    -105: "fast_build",
}


def percentile_nearest_rank(values: Sequence[float], percent: float) -> float:
    """Return the nearest-rank percentile; p95 of 1..100 is 95."""
    if not values:
        raise ValueError("a percentile needs at least one value")
    if not 0.0 <= percent <= 100.0:
        raise ValueError("percentile must be between 0 and 100")
    ordered = sorted(values)
    rank = max(1, math.ceil(percent / 100.0 * len(ordered)))
    return float(ordered[rank - 1])


def low_fps(intervals_us: Sequence[int], fraction: float) -> float:
    """FPS of the slowest fraction of frames, using their total elapsed time."""
    if not intervals_us:
        raise ValueError("low FPS needs at least one frame interval")
    count = max(1, math.ceil(len(intervals_us) * fraction))
    slowest_us = sum(sorted(intervals_us, reverse=True)[:count])
    return 1_000_000.0 * count / slowest_us


def analyze_frame_intervals(intervals_us: Sequence[int]) -> dict:
    if not intervals_us:
        raise ValueError("the frame log has no positive frame intervals")
    if any(value <= 0 for value in intervals_us):
        raise ValueError("frame intervals must be positive")
    total_us = sum(intervals_us)
    median_us = statistics.median(intervals_us)
    return {
        "frames": len(intervals_us),
        "duration_ms": total_us / 1000.0,
        "average_fps": len(intervals_us) * 1_000_000.0 / total_us,
        "low_fps": {
            "1_percent": low_fps(intervals_us, 0.01),
            "0_1_percent": low_fps(intervals_us, 0.001),
        },
        "frame_time_ms": {
            "mean": statistics.fmean(intervals_us) / 1000.0,
            "median": median_us / 1000.0,
            "p95": percentile_nearest_rank(intervals_us, 95) / 1000.0,
            "p99": percentile_nearest_rank(intervals_us, 99) / 1000.0,
            "max": max(intervals_us) / 1000.0,
        },
        "stutters": {
            "over_33_333_ms": sum(value > 33_333 for value in intervals_us),
            "over_50_ms": sum(value > 50_000 for value in intervals_us),
            "over_100_ms": sum(value > 100_000 for value in intervals_us),
            "over_2x_median": sum(value > 2 * median_us for value in intervals_us),
        },
    }


def read_frame_log(path: Path) -> dict:
    intervals: list[int] = []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        for line_number, raw in enumerate(stream, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line_number == 1 and line.lower() == "t_us,interval_us":
                continue
            fields = line.split(",")
            if len(fields) != 2:
                raise ValueError(f"{path}:{line_number}: expected t_us,interval_us")
            try:
                intervals.append(int(fields[1]))
            except ValueError as error:
                raise ValueError(f"{path}:{line_number}: invalid frame interval") from error
    return analyze_frame_intervals(intervals)


def _nonnegative_int(row: dict[str, str], field: str) -> int:
    value = int(row.get(field, "0") or "0")
    if value < 0:
        raise ValueError(f"{field} must not be negative")
    return value


def analyze_waits(path: Path) -> dict:
    by_kind: dict[str, dict[str, float | int]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"begin_us", "end_us", "filter"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError(f"{path}: expected begin_us,end_us,tid,filter,ident,events,name")
        for row in reader:
            code = int(row["filter"])
            if code not in COMPILE_STALL_NAMES:
                continue
            begin = _nonnegative_int(row, "begin_us")
            end = _nonnegative_int(row, "end_us")
            if end < begin:
                raise ValueError(f"{path}: a stall ends before it begins")
            duration_us = end - begin
            name = COMPILE_STALL_NAMES[code]
            bucket = by_kind.setdefault(name, {"count": 0, "total_thread_ms": 0.0, "max_ms": 0.0})
            bucket["count"] += 1
            bucket["total_thread_ms"] += duration_us / 1000.0
            bucket["max_ms"] = max(bucket["max_ms"], duration_us / 1000.0)
    all_buckets = list(by_kind.values())
    return {
        "count": sum(int(bucket["count"]) for bucket in all_buckets),
        # Durations on different threads can overlap: this is thread-time, not wall time.
        "total_thread_ms": sum(float(bucket["total_thread_ms"]) for bucket in all_buckets),
        "max_ms": max((float(bucket["max_ms"]) for bucket in all_buckets), default=0.0),
        "by_kind": by_kind,
    }


def analyze_pace(path: Path) -> dict:
    totals = {
        "gpu_busy_us": 0,
        "gpu_idle_us": 0,
        "gpu_starved_us": 0,
        "gpu_cmdbufs": 0,
        "donewait_us": 0,
        "present_cost_us": 0,
    }
    rows = 0
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or "gpu_cmdbufs" not in reader.fieldnames:
            raise ValueError(f"{path}: not a Kyty pace CSV")
        for row in reader:
            rows += 1
            for field in totals:
                totals[field] += _nonnegative_int(row, field)
    busy_idle_us = totals["gpu_busy_us"] + totals["gpu_idle_us"]
    return {
        "rows": rows,
        "gpu_cmdbufs_total": totals["gpu_cmdbufs"],
        "gpu_cmdbufs_per_row": totals["gpu_cmdbufs"] / rows if rows else 0.0,
        "gpu_busy_ms": totals["gpu_busy_us"] / 1000.0,
        "gpu_idle_ms": totals["gpu_idle_us"] / 1000.0,
        "gpu_starved_ms": totals["gpu_starved_us"] / 1000.0,
        "gpu_busy_share_of_busy_plus_idle": (
            totals["gpu_busy_us"] / busy_idle_us if busy_idle_us else 0.0
        ),
        "guest_done_wait_ms": totals["donewait_us"] / 1000.0,
        "present_cost_ms": totals["present_cost_us"] / 1000.0,
    }


def analyze(frames: Path, pace: Path | None = None, waits: Path | None = None, label: str = "") -> dict:
    report = {
        "schema_version": SCHEMA_VERSION,
        "label": label,
        "frames": read_frame_log(frames),
    }
    if pace is not None:
        report["pace"] = analyze_pace(pace)
    if waits is not None:
        report["compile_stalls"] = analyze_waits(waits)
    return report


def percent_change(candidate: float, baseline: float) -> float | None:
    if baseline == 0:
        return None
    return (candidate - baseline) / baseline * 100.0


def compare_reports(baseline: dict, candidate: dict) -> dict:
    for report, name in ((baseline, "baseline"), (candidate, "candidate")):
        if report.get("schema_version") != SCHEMA_VERSION or "frames" not in report:
            raise ValueError(f"{name}: unsupported or incomplete benchmark report")
    old = baseline["frames"]
    new = candidate["frames"]
    result = {
        "baseline_label": baseline.get("label", ""),
        "candidate_label": candidate.get("label", ""),
        "average_fps_change_percent": percent_change(new["average_fps"], old["average_fps"]),
        "low_1_percent_fps_change_percent": percent_change(
            new["low_fps"]["1_percent"], old["low_fps"]["1_percent"]
        ),
        "mean_frame_time_change_percent": percent_change(
            new["frame_time_ms"]["mean"], old["frame_time_ms"]["mean"]
        ),
        "p95_frame_time_change_percent": percent_change(
            new["frame_time_ms"]["p95"], old["frame_time_ms"]["p95"]
        ),
        "p99_frame_time_change_percent": percent_change(
            new["frame_time_ms"]["p99"], old["frame_time_ms"]["p99"]
        ),
    }
    if "compile_stalls" in baseline and "compile_stalls" in candidate:
        result["compile_stall_count_change"] = (
            candidate["compile_stalls"]["count"] - baseline["compile_stalls"]["count"]
        )
        result["compile_stall_max_ms_change"] = (
            candidate["compile_stalls"]["max_ms"] - baseline["compile_stalls"]["max_ms"]
        )
    if "pace" in baseline and "pace" in candidate:
        result["gpu_cmdbufs_per_row_change_percent"] = percent_change(
            candidate["pace"]["gpu_cmdbufs_per_row"], baseline["pace"]["gpu_cmdbufs_per_row"]
        )
    return result


def format_summary(report: dict) -> str:
    frames = report["frames"]
    lines = [
        f"Label: {report.get('label') or '(unnamed)'}",
        f"Frames: {frames['frames']} over {frames['duration_ms'] / 1000.0:.3f} s",
        f"Average FPS: {frames['average_fps']:.2f}",
        f"1% low FPS: {frames['low_fps']['1_percent']:.2f}",
        f"0.1% low FPS: {frames['low_fps']['0_1_percent']:.2f}",
        (
            "Frame time ms (mean/median/p95/p99/max): "
            f"{frames['frame_time_ms']['mean']:.2f} / {frames['frame_time_ms']['median']:.2f} / "
            f"{frames['frame_time_ms']['p95']:.2f} / {frames['frame_time_ms']['p99']:.2f} / "
            f"{frames['frame_time_ms']['max']:.2f}"
        ),
    ]
    if "pace" in report:
        pace = report["pace"]
        lines.append(
            f"GPU command buffers/flip: {pace['gpu_cmdbufs_per_row']:.2f}; "
            f"busy/(busy+idle): {pace['gpu_busy_share_of_busy_plus_idle'] * 100.0:.1f}%"
        )
    if "compile_stalls" in report:
        stalls = report["compile_stalls"]
        lines.append(
            f"Compile stalls: {stalls['count']}, thread-time {stalls['total_thread_ms']:.1f} ms, "
            f"max {stalls['max_ms']:.1f} ms"
        )
    return "\n".join(lines)


def format_comparison(baseline: dict, candidate: dict, comparison: dict) -> str:
    old = baseline["frames"]
    new = candidate["frames"]

    def pct(value: float | None) -> str:
        return "n/a" if value is None else f"{value:+.2f}%"

    rows = [
        ("Average FPS", old["average_fps"], new["average_fps"], comparison["average_fps_change_percent"]),
        (
            "1% low FPS",
            old["low_fps"]["1_percent"],
            new["low_fps"]["1_percent"],
            comparison["low_1_percent_fps_change_percent"],
        ),
        (
            "Mean frame ms",
            old["frame_time_ms"]["mean"],
            new["frame_time_ms"]["mean"],
            comparison["mean_frame_time_change_percent"],
        ),
        (
            "p95 frame ms",
            old["frame_time_ms"]["p95"],
            new["frame_time_ms"]["p95"],
            comparison["p95_frame_time_change_percent"],
        ),
        (
            "p99 frame ms",
            old["frame_time_ms"]["p99"],
            new["frame_time_ms"]["p99"],
            comparison["p99_frame_time_change_percent"],
        ),
    ]
    lines = [f"{'Metric':<20} {'Baseline':>12} {'Candidate':>12} {'Change':>12}"]
    lines.extend(f"{name:<20} {before:>12.2f} {after:>12.2f} {pct(change):>12}" for name, before, after, change in rows)
    if "gpu_cmdbufs_per_row_change_percent" in comparison:
        lines.append(
            f"GPU command buffers/flip: {baseline['pace']['gpu_cmdbufs_per_row']:.2f} -> "
            f"{candidate['pace']['gpu_cmdbufs_per_row']:.2f} "
            f"({pct(comparison['gpu_cmdbufs_per_row_change_percent'])})"
        )
    if "compile_stall_count_change" in comparison:
        lines.append(
            f"Compile stalls: {baseline['compile_stalls']['count']} -> "
            f"{candidate['compile_stalls']['count']}; max ms change "
            f"{comparison['compile_stall_max_ms_change']:+.2f}"
        )
    return "\n".join(lines)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subcommands.add_parser("analyze", help="analyze one run")
    analyze_parser.add_argument("--frames", type=Path, required=True, help="KYTY_FRAME_TIMES_LOG CSV")
    analyze_parser.add_argument("--pace", type=Path, help="KYTY_PACE_LOG CSV")
    analyze_parser.add_argument("--waits", type=Path, help="pace CSV .waits.csv")
    analyze_parser.add_argument("--label", default="", help="run name in the report")
    analyze_parser.add_argument("--output", type=Path, help="write the JSON report here")
    analyze_parser.add_argument("--json", action="store_true", help="print JSON instead of the summary")

    compare_parser = subcommands.add_parser("compare", help="compare two JSON reports")
    compare_parser.add_argument("baseline", type=Path)
    compare_parser.add_argument("candidate", type=Path)
    compare_parser.add_argument("--output", type=Path, help="write the comparison JSON here")
    compare_parser.add_argument("--json", action="store_true", help="print JSON instead of the table")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "analyze":
            report = analyze(args.frames, args.pace, args.waits, args.label)
            if args.output:
                write_json(args.output, report)
            print(json.dumps(report, indent=2, ensure_ascii=False) if args.json else format_summary(report))
        else:
            baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
            candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
            comparison = compare_reports(baseline, candidate)
            if args.output:
                write_json(args.output, comparison)
            print(
                json.dumps(comparison, indent=2, ensure_ascii=False)
                if args.json
                else format_comparison(baseline, candidate, comparison)
            )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"kyty_bench: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

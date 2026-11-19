# Kyty/EchoPS repeatable frame-time benchmarks

`kyty_bench.py` analyzes the emulator's existing logs. It uses only the Python standard library and
does not need the emulator, a game, Qt, Vulkan or a compiler.

## 1. Record a route once

For Ghost of Yotei the pad anchor defaults to the second `KYTY_EVENT loading-end`: title menu first,
gameplay second. Create `_Benchmark` beside `kyty_emulator.exe`, then temporarily add this to
`u59-preset.PPSA26344.json` (the launcher applies bundled presets itself; inherited `KYTY_*` variables
are not the reliable route):

```json
"KYTY_TEST_PAD_RECORD": "_Benchmark\\yotei-torii.pad"
```

Play the chosen route for 60–90 seconds and exit. Remove the `RECORD` entry. The resulting text file
contains no game data, only timestamps, buttons and stick positions.

Check the replay before measuring:

```json
"KYTY_TEST_PAD_REPLAY": "_Benchmark\\yotei-torii.pad"
```

`KYTY_TEST_PAD_ANCHOR=2` is the default. Set it explicitly only if a different route needs another
anchor. Timing is in milliseconds, so a different frame rate does not shift the input route.

## 2. Capture a run

The helper arms and finishes a run for you: it backs up the title preset, adds the logging keys,
and restores the preset when you finish.

```powershell
# arm: writes _Benchmark\<tag>.frames.csv / .pace.csv, backs up u59-preset.PPSA26344.json
.\tools\benchmark\echops_bench_run.ps1 -Action Start -EmulatorDir 'D:\workPs' -Tag baseline

# start Ghost of Yotei from the launcher, play the recorded route, exit the game

# restore the preset, analyze, write <tag>.json and print the summary
.\tools\benchmark\echops_bench_run.ps1 -Action Finish -EmulatorDir 'D:\workPs' -Tag baseline
```

`-Replay <pad file>` also arms the recorded route, `-StallTrace` adds the compile-span rows, and
`-Python <path>` picks the interpreter when it is not on `PATH`. The helper refuses to start twice
and refuses to finish without a backup, so an interrupted run cannot leave the preset modified: if
something goes wrong, restore `u59-preset.PPSA26344.json.bench-backup` by hand.

By hand, the same thing means: use the same game version, scene, settings and shader/cache state for
every compared run. Close OBS and other graphics hooks. Precompile once, then use a warm cache for
steady-state measurements; cold shader/pipeline compilation is a separate test.

Add these entries temporarily to the title preset, changing the `baseline` prefix per run:

```json
"KYTY_FRAME_TIMES_LOG": "_Benchmark\\baseline.frames.csv",
"KYTY_PACE_LOG": "_Benchmark\\baseline.pace.csv",
"KYTY_GPU_TIMING": "1",
"KYTY_TEST_PAD_REPLAY": "_Benchmark\\yotei-torii.pad"
```

Run three times, alternating baseline and candidate builds where possible. Do not compare a warm run
with a cold run or two different locations. `KYTY_STALL_TRACE=1` is optional for a separate compile
investigation; leave it off for ordinary FPS comparisons because it adds logging work.

## 3. Analyze and compare

From the repository root:

```powershell
py tools\benchmark\kyty_bench.py analyze `
  --frames ..\_Benchmark\baseline.frames.csv `
  --pace ..\_Benchmark\baseline.pace.csv `
  --waits ..\_Benchmark\baseline.pace.csv.waits.csv `
  --emulator-log ..\_kyty.txt `
  --label baseline --output ..\_Benchmark\baseline.json

py tools\benchmark\kyty_bench.py analyze `
  --frames ..\_Benchmark\candidate.frames.csv `
  --pace ..\_Benchmark\candidate.pace.csv `
  --waits ..\_Benchmark\candidate.pace.csv.waits.csv `
  --label candidate --output ..\_Benchmark\candidate.json

py tools\benchmark\kyty_bench.py compare `
  ..\_Benchmark\baseline.json ..\_Benchmark\candidate.json `
  --output ..\_Benchmark\comparison.json
```

`--pace`, `--waits` and `--emulator-log` are optional. `--emulator-log` reads the existing console
text/`_kyty.txt` and extracts the title/build/GPU, first-flip time, frame stalls, completed compile
waits, shader precompile/replay totals, RT pipeline builds, BDA live-walk samples and a crash line.
Those durations can overlap and are deliberately reported separately, never summed as wall time.
`--json` prints the machine-readable report instead of the human summary. A report stores no game or
save data.

## Metric definitions

- **Average FPS**: frame count divided by the sum of frame intervals.
- **1% low / 0.1% low FPS**: the slowest `ceil(N * fraction)` frames divided by their total elapsed
  time. A one-frame stall therefore has the correct large effect in a short run.
- **p95/p99 frame time**: nearest-rank percentile of the intervals. Lower is better.
- **Stutters**: frames above 33.333/50/100 ms and above twice the run's median frame time.
- **GPU command buffers/flip**: `gpu_cmdbufs` folded at each presented flip. Fewer is useful only if
  frame time and picture correctness also hold.
- **GPU busy share**: `gpu_busy_us / (gpu_busy_us + gpu_idle_us)` over the logged rows. It is not a
  Windows Task Manager GPU-utilization reading.
- **Compile stalls**: the negative `KYTY_STALL_TRACE` filters -100 through -105. Their total is
  **thread-time**, because stalls on different threads can overlap; it is not wall-clock time. `max_ms`
  is the longest individual recorded span.

A candidate is accepted only when the same-route measurements improve beyond run-to-run noise and the
picture remains correct. Average FPS alone is not a "minimum 60 FPS" claim.

## Synthetic tests

```powershell
py -m unittest discover -s tools\benchmark\tests -v
```

The tests use in-memory CSV data, so they also run in restricted CI/sandbox filesystems. They validate
percentiles, low-FPS calculations, pace/stall parsing, comparisons and the CLI report path. They do not
prove emulator performance or correctness on a GPU.

## Finding the commit-thread bottleneck (diagnostics build)

The release compiles the command processor's per-draw statistics out (`-DKYTY_CP_DIAGNOSTICS=OFF`), so
no preset switch can report them. Build a diagnostics package instead: **Actions → U59 Windows
release → Run workflow**, tick **diagnostics**, and take the artifact named `...-diag`. It is slower —
use it only to measure.

Then, with that build, add to the title preset:

```json
"KYTY_CP_COMMIT_STATS": "steps",
"KYTY_CP_GAP_STATS": "1"
```

Play the route for ~60 s and collect the console lines (they repeat every 10 s):

- `CommitStats 10s:` — phases of a committed draw, µs per draw, and how much of the work a run-level
  commit could skip;
- `StartPhases 10s:` — the same split by why a draw started instead of continuing a run;
- `Steps 10s:` — per-step costs inside the commit;
- `CpGaps 10s:` — the gap before a draw split by command-processor category.

These lines answer the only question that decides whether the commit thread can be parallelised:
what share of its time is per-draw work that a delta or another thread could remove. Send the lines
together with the benchmark JSON.

## Submit-batching experiment

Do not ship a guessed value. After a baseline, test one additional title-preset entry at a time:

```json
"KYTY_SUBMIT_EVERY": "8"
```

Compare 4, 8 and 16 in separate runs. Keep the value only if frame time improves without worse pacing
or rendering. Restore the preset afterwards if it regresses.

## Picture-forbidden profile (`fast`)

`tools\presets\u59-preset.PPSA26344.fast.json` is the shipped Yotei preset plus every switch this fork
has that trades correctness for frame time:

| Switch | What it gives up |
|---|---|
| `KYTY_GPU_HAZARD=3`, `KYTY_INDIRECT_ARGS_RANGES=1`, `KYTY_GUEST_BARRIER_SCOPE=rel=drop`, `KYTY_DMA_BARRIER_BATCH=1` | the launcher's "Faster GPU synchronisation": fewer redundant barriers, can glitch |
| `KYTY_SKIP_OCCLUSION_VISIBLE=1` | occlusion results of skipped draws are biased: occasional wrong culling |
| `KYTY_SUBMIT_EVERY=8` | more commands per submit: possible added latency |
| `KYTY_PIPELINE_SKIP_WAIT_US=500` | a draw waits 0.5 ms instead of 2 ms for a new pipeline, then is skipped |

Switch profiles with the helper (it keeps `u59-preset.<title>.json.profile-backup`):

```powershell
.\tools\benchmark\echops_profile.ps1 -EmulatorDir 'D:\workPs' -Profile fast
# play / measure
.\tools\benchmark\echops_profile.ps1 -EmulatorDir 'D:\workPs' -Restore
```

Measure it exactly like the shipped profile, on the same route, and compare the JSON reports. If the
picture or frame pacing gets worse, keep the shipped profile; a faster average FPS with visible
artifacts is not a win.

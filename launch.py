#!/usr/bin/env python3
"""Launch CSV experiments as independent, concurrently requested Slurm steps.

There is deliberately no local CPU/memory scheduler: srun queues a step until
the requested resources are free in the enclosing one-node allocation.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
from pathlib import Path
import re
import random
import shlex
import shutil
import signal
import subprocess
import sys
import time


FIELDS = ("name", "cpus", "memory", "time", "measure", "parameter_file", "command")
LEGACY_FIELDS = FIELDS[:4] + ("repetitions",) + FIELDS[4:]
RESULT_FIELDS = (
    "name", "repetition", "cpus", "memory", "time", "measure", "parameter_file", "command", "result_dir", "srun_launched_at",
    "step_started_at", "srun_finished_at", "hostname", "step_id",
    "exit_code", "status", "stdout", "stderr",
)
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
MEM_RE = re.compile(r"[1-9][0-9]*[KMGT]?\Z", re.IGNORECASE)
TIME_RE = re.compile(r"(?:(?:[0-9]+-)?[0-9]{1,2}:[0-9]{1,2}:[0-9]{1,2}|[0-9]+:[0-9]{1,2}|[0-9]+)\Z")
MEASURE_VALUES = {"true", "false"}


def timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def parse_manifest(path: Path) -> tuple[list[dict[str, str]], bool]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise ValueError("CSV has no header")
        has_row_repetitions = set(reader.fieldnames) == set(LEGACY_FIELDS)
        if not has_row_repetitions and set(reader.fieldnames) != set(FIELDS):
            raise ValueError(
                "CSV columns must be exactly one of: "
                f"{', '.join(FIELDS)}; or {', '.join(LEGACY_FIELDS)}"
            )
        rows = []
        names = set()
        for number, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"CSV line {number}: wrong number of columns; quote commands containing commas")
            row = {key: value.strip() for key, value in row.items()}
            name = row["name"]
            if not NAME_RE.fullmatch(name) or name in (".", ".."):
                raise ValueError(f"CSV line {number}: invalid name {name!r}")
            if name in names:
                raise ValueError(f"CSV line {number}: duplicate name {name!r}")
            names.add(name)
            if not row["cpus"].isdigit() or int(row["cpus"]) < 1:
                raise ValueError(f"CSV line {number}: cpus must be a positive integer")
            if not MEM_RE.fullmatch(row["memory"]):
                raise ValueError(f"CSV line {number}: memory must be positive, e.g. 500M or 4G")
            if not TIME_RE.fullmatch(row["time"]) or not any(c in "123456789" for c in row["time"]):
                raise ValueError(f"CSV line {number}: time must be positive Slurm time, e.g. 01:00:00")
            if has_row_repetitions:
                if not row["repetitions"].isdigit() or int(row["repetitions"]) < 1:
                    raise ValueError(f"CSV line {number}: repetitions must be a positive integer")
            row["measure"] = row["measure"].lower()
            if row["measure"] not in MEASURE_VALUES:
                raise ValueError(f"CSV line {number}: measure must be true or false")
            if "<result-dir>" not in row["command"]:
                raise ValueError(f"CSV line {number}: command must contain <result-dir>")
            if "<parameter-file>" in row["command"] and not row["parameter_file"]:
                raise ValueError(f"CSV line {number}: <parameter-file> requires parameter_file")
            if row["parameter_file"]:
                parameter_file = Path(row["parameter_file"]).expanduser().resolve()
                if not parameter_file.is_file():
                    raise ValueError(f"CSV line {number}: parameter_file does not exist: {parameter_file}")
                row["parameter_file"] = str(parameter_file)
            if not row["command"]:
                raise ValueError(f"CSV line {number}: empty command")
            rows.append(row)
    if not rows:
        raise ValueError("CSV contains no experiments")
    return rows, has_row_repetitions


def step_details(marker: Path) -> tuple[str, str, str]:
    if not marker.exists():
        return "", "", ""
    fields = marker.read_text(encoding="utf-8").strip().split("\t")
    if len(fields) != 3:
        return "", "", ""
    return fields[0], fields[1], fields[2]


def create_unique_result_dir(run_dir: Path, name: str, repetition: int) -> Path:
    parent = run_dir / "runs"
    parent.mkdir(parents=True, exist_ok=True)
    while True:
        timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
        directory = parent / f"{name}_{timestamp}"
        try:
            directory.mkdir()
            return directory
        except FileExistsError:
            time.sleep(random.randint(1, 60))


def expand_command(template: str, result_dir: Path, parameter_file: Path | None,
                   name: str, repetition: int) -> str:
    replacements = {
        "<result-dir>": shlex.quote(str(result_dir)),
        "<run-name>": shlex.quote(name),
        "<repetition>": str(repetition),
    }
    if "<parameter-file>" in template:
        if parameter_file is None:
            raise ValueError("<parameter-file> requires parameter_file")
        replacements["<parameter-file>"] = shlex.quote(str(parameter_file))
    for placeholder, value in replacements.items():
        template = template.replace(placeholder, value)
    return template


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--results-root", type=Path, default=Path("results"))
    parser.add_argument("--run-dir", type=Path, help="explicit directory for this job's results")
    parser.add_argument("--repetitions", type=int, help="global repetition count for manifests without a repetitions column")
    parser.add_argument("--dry-run", action="store_true", help="Validate and show srun requests without executing")
    args = parser.parse_args()
    rows, has_row_repetitions = parse_manifest(args.manifest)
    if has_row_repetitions and args.repetitions is not None:
        raise ValueError("--repetitions is only valid when the CSV has no repetitions column")
    if not has_row_repetitions:
        if args.repetitions is None:
            raise ValueError("CSV has no repetitions column; provide --repetitions")
        if args.repetitions < 1:
            raise ValueError("--repetitions must be a positive integer")
    job_id = os.environ.get("SLURM_JOB_ID")
    if not args.dry_run:
        if not job_id:
            raise ValueError("Run this launcher inside an sbatch allocation (or use --dry-run)")
        if os.environ.get("SLURM_JOB_NUM_NODES") not in (None, "1"):
            raise ValueError("This launcher requires exactly one allocated node")

    step_script = Path(__file__).resolve().with_name("run_step.sh")
    root = args.results_root.resolve()
    run_dir = args.run_dir.expanduser().resolve() if args.run_dir else root / (job_id or "DRY_RUN")
    if has_row_repetitions:
        # Preserve the legacy behavior: launch every expanded trial immediately.
        requests = []
        for row in rows:
            for repetition in range(1, int(row["repetitions"]) + 1):
                name = f"{row['name']}__rep_{repetition:03d}"
                trial = {**row, "name": name, "repetition": repetition}
                stdout = run_dir / f"{name}.out"
                stderr = run_dir / f"{name}.err"
                marker = run_dir / f"{name}.start"
                requests.append((trial, stdout, stderr, marker))
        request_groups = [requests]
    else:
        request_groups = []
        for repetition in range(1, args.repetitions + 1):
            requests = []
            for row in rows:
                name = f"{row['name']}__rep_{repetition:03d}"
                trial = {**row, "name": name, "repetition": repetition}
                stdout = run_dir / f"{name}.out"
                stderr = run_dir / f"{name}.err"
                marker = run_dir / f"{name}.start"
                requests.append((trial, stdout, stderr, marker))
            random.shuffle(requests)
            request_groups.append(requests)

    if args.dry_run:
        for group_number, requests in enumerate(request_groups, start=1):
            if not has_row_repetitions:
                print(f"ROUND {group_number}: randomized order")
            for row, stdout, stderr, _ in requests:
                result_dir = run_dir / "runs" / f"{row['name']}_DRY_RUN"
                parameter_file = result_dir / Path(row["parameter_file"]).name if row["parameter_file"] else None
                command = expand_command(row["command"], result_dir, parameter_file, row["name"], row["repetition"])
                request = ["srun", "--exclusive", "--exact", "--nodes=1", "--ntasks=1", f"--cpus-per-task={row['cpus']}", f"--mem={row['memory']}", f"--time={row['time']}", "--cpu-bind=verbose,cores", "--export=ALL"]
                if row["measure"] == "true":
                    measurement_dir = result_dir / "measurement"
                    request.extend(["/usr/bin/time", "-v", "-o", str(measurement_dir / "time-v.txt"), "perf", "stat", "-o", str(measurement_dir / "perf-stat.txt"), "-e", "cycles,instructions,task-clock,cpu-clock", "--"])
                request.extend(["bash", str(step_script), command, "<marker>"])
                print(f"{row['name']}: {shlex.join(request)} > {stdout} 2> {stderr}")
        return 0

    # Never reuse an earlier job ID's logs; partial results remain inspectable.
    run_dir.mkdir(parents=True, exist_ok=False)
    interrupted = False

    def on_signal(signum: int, _frame: object) -> None:
        nonlocal interrupted
        interrupted = True
        print(f"Received signal {signum}; stopping active srun requests", file=sys.stderr, flush=True)

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    running: dict[int, tuple[dict[str, str], subprocess.Popen[bytes], str, Path, Path, Path, Path]] = {}
    failures = 0

    with (run_dir / "summary.csv").open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=RESULT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        output.flush()

        def archive_step_logs(result_dir: Path, stdout: Path, stderr: Path, marker: Path) -> tuple[Path, Path]:
            for path in (stdout, stderr, marker):
                if path.is_file():
                    shutil.move(str(path), result_dir / path.name)
            return result_dir / stdout.name, result_dir / stderr.name

        def record(row: dict[str, str], result_dir: Path, launched: str, ended: str, code: int | str,
                   stdout: Path, stderr: Path, marker: Path) -> None:
            nonlocal failures
            started, host, step_id = step_details(marker)
            archived_stdout, archived_stderr = archive_step_logs(result_dir, stdout, stderr, marker)
            status = "success" if code == 0 else ("interrupted" if interrupted else "failed")
            failures += (status != "success")
            writer.writerow({
                **row, "result_dir": str(result_dir), "srun_launched_at": launched, "step_started_at": started,
                "srun_finished_at": ended, "hostname": host, "step_id": step_id,
                "exit_code": code, "status": status, "stdout": str(archived_stdout),
                "stderr": str(archived_stderr),
            })
            output.flush()
            print(f"FINISH {row['name']} status={status} exit={code} step={step_id or '-'}", flush=True)

        try:
            for group_number, requests in enumerate(request_groups, start=1):
                if interrupted:
                    break
                if not has_row_repetitions:
                    print(f"ROUND {group_number}/{args.repetitions}: launching randomized order", flush=True)
                for row, stdout, stderr, marker in requests:
                    if interrupted:
                        break
                    launched = timestamp()
                    result_dir = run_dir / "runs" / f"{row['name']}__not-created"
                    try:
                        result_dir = create_unique_result_dir(run_dir, row["name"], row["repetition"])
                        measurement_dir = result_dir / "measurement"
                        parameter_file = None
                        if row["parameter_file"]:
                            source = Path(row["parameter_file"])
                            parameter_file = result_dir / source.name
                            shutil.copy2(source, parameter_file)
                            row = {**row, "parameter_file": str(parameter_file)}
                        command = expand_command(row["command"], result_dir, parameter_file, row["name"], row["repetition"])
                        request = [
                            "srun", "--exclusive", "--exact", "--nodes=1", "--ntasks=1",
                            f"--cpus-per-task={row['cpus']}", f"--mem={row['memory']}",
                            f"--time={row['time']}", "--cpu-bind=verbose,cores", "--export=ALL",
                        ]
                        if row["measure"] == "true":
                            measurement_dir.mkdir()
                            request.extend([
                                "/usr/bin/time", "-v", "-o", str(measurement_dir / "time-v.txt"),
                                "perf", "stat", "-o", str(measurement_dir / "perf-stat.txt"),
                                "-e", "cycles,instructions,task-clock,cpu-clock", "--",
                            ])
                        request.extend(["bash", str(step_script), command, str(marker)])
                        with stdout.open("xb") as out, stderr.open("xb") as err:
                            proc = subprocess.Popen(request, stdin=subprocess.DEVNULL,
                                                    stdout=out, stderr=err)
                        running[proc.pid] = (row, proc, launched, stdout, stderr, marker, result_dir)
                        print(f"REQUEST {row['name']} cpus={row['cpus']} mem={row['memory']} time={row['time']} measure={row['measure']}", flush=True)
                    except OSError as exc:
                        stderr.write_text(f"Could not launch srun: {exc}\n", encoding="utf-8")
                        record(row, result_dir, launched, timestamp(), "launch_error", stdout, stderr, marker)

                # A global-repetition round must finish before the next randomized round begins.
                while running:
                    if interrupted:
                        for _, proc, _, _, _, _, _ in running.values():
                            if proc.poll() is None:
                                proc.terminate()
                        deadline = time.monotonic() + 10
                        while time.monotonic() < deadline and any(entry[1].poll() is None for entry in running.values()):
                            time.sleep(0.2)
                        for _, proc, _, _, _, _, _ in running.values():
                            if proc.poll() is None:
                                proc.kill()
                    for pid, (row, proc, launched, stdout, stderr, marker, result_dir) in list(running.items()):
                        code = proc.poll()
                        if code is not None:
                            record(row, result_dir, launched, timestamp(), code, stdout, stderr, marker)
                            del running[pid]
                    if running and not interrupted:
                        time.sleep(1)
        finally:
            # If an unexpected Python exception occurs, cancel all still-active
            # step requests rather than leaving them without a launcher.
            for _, proc, _, _, _, _, _ in running.values():
                if proc.poll() is None:
                    proc.terminate()

    print(f"Summary: {run_dir / 'summary.csv'}; failures={failures}", flush=True)
    return 1 if failures or interrupted else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

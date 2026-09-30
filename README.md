# One-node SLURM experiment launcher

This package reserves exactly one node in one `sbatch` job and launches every
CSV row as its own `srun` job step. SLURM assigns separate CPUs and the requested
memory to running steps. When a request does not fit yet, its `srun` waits for
resources; the Python program does **not** track free CPUs or implement a local
dispatcher. The launcher spends most of its time sleeping. No core is reserved
for it.

## Files

- `submit.sh`: the one-node allocation and environment setup point.
- `launch.py`: validates the CSV, starts step requests, and records results.
- `run_step.sh`: records when the step actually began on the compute node.
- `experiments.csv`: example manifest; replace its example commands.
- `experiment_csvs/probe.csv`: legacy-format test using only Bash and `sleep`.
- `experiment_csvs/probe_rounds.csv`: round-based-format test using only Bash and `sleep`.

## Configuration and use

1. Copy these files to a directory visible from both the login and compute
   node. Change `#SBATCH --partition=big` and `#SBATCH --time=12:00:00` in
   `submit.sh` to match your partition and the **whole group's** maximum
   duration. Keep `--nodes=1 --exclusive --mem=0` for a whole node including
   its memory. Keep `--hint=nomultithread` if you want physical cores, as on
   the cluster setup discussed previously.
   -> done
2. Prepare the Python/solver environment in `submit.sh` before the launcher
   call, if needed. The commands in the manifest are run with `bash -c` from
   the directory in which you submitted `sbatch`; set `--chdir` when submitting
   if you need another working directory. Set solver thread counts explicitly
   in your command or environment; requesting eight CPUs does not configure
   a solver to use eight threads.
3. Choose one of two experiment CSV formats.

    The legacy format retains independent per-row repetition counts and launches
    every expanded step without waiting between repetitions:

    ```csv
    name,cpus,memory,time,repetitions,measure,parameter_file,command
    ```

    The round-based format omits `repetitions`:

    ```csv
    name,cpus,memory,time,measure,parameter_file,command
    ```

    `name` must be unique and may contain letters, digits, `_`, `.`, and `-`.
    `memory` is the step's per-node request, such as `500M`, `4G`, or `32G`;
    `time` is the step limit, such as `00:10:00`, `02:00:00`, or `1-00:00:00`.
    In the legacy format, `repetitions` is a positive integer. In the
    round-based format, provide one global positive count with
    `--repetitions N`: the launcher creates N rounds, randomizes the row order
    within each round, launches every row in that round, and waits for every
    step to finish before beginning the next round. Each repetition is an
    independent `srun` step. `measure` must be `true` or `false`: `true` surrounds that
   step with `/usr/bin/time -v perf stat -e cycles,instructions,task-clock,cpu-clock`;
   use `false` for workflows that already measure their own trial workers.
   `parameter_file` is optional. When given, the launcher copies it into that
   repetition's result directory and replaces `<parameter-file>` in `command`
   with the copied path. Every command must contain `<result-dir>`, which the
   launcher replaces with a unique, timestamped result directory. It can also
   replace `<run-name>` and `<repetition>`. CSV-quote commands containing
   commas or quotes. Commands are trusted shell text, so shell pipes, redirects,
   and environment variables work as they ordinarily do in `bash -c`.
4. Validate without submitting a job:

   ```bash
    python3 launch.py experiment_csvs/probe.csv --dry-run
    python3 launch.py experiment_csvs/probe_rounds.csv --repetitions 10 --dry-run
   ```

5. Test the allocation with `probe.csv`, then submit the real manifest:

   ```bash
    sbatch --time=00:10:00 submit.sh "$PWD/experiment_csvs/probe.csv"
    sbatch submit.sh "$PWD/experiment_csvs/probe_rounds.csv" --repetitions 10
   ```

    If submitting from another directory, use an absolute manifest path. You
    can override the result location with `RESULTS_ROOT=/shared/path`.

Each job writes `results/job<jobid>_<timestamp>/summary.csv`, `<name>__rep_<n>.out`,
`<name>__rep_<n>.err`, and `<name>__rep_<n>.start`, plus a unique result
directory under `results/job<jobid>_<timestamp>/runs/`. Completed step logs
are moved into their corresponding run directory. The Slurm batch `.out` and
`.err` logs are moved into the job result directory after the launcher exits.
When enabled, `perf stat` and `/usr/bin/time -v` write to `measurement/` in
the corresponding run directory. Application standard output goes to `.out`
and standard error goes to `.err`. `REQUEST` in the batch log means
Python created an `srun` request; `STEP_STARTED` in a run's `.out`
and its `.start` file means
the task actually got resources and started. `srun_launched_at` in the summary
can therefore precede `step_started_at`. Results are flushed after each step
finishes so a partial summary survives an outer-job time limit. The Python
launcher returns a nonzero status if any step fails. Use `sacct -j <jobid>
--format=JobID,State,ExitCode,Elapsed,MaxRSS` to distinguish time limits and
memory failures from application errors; the summary records the `srun` exit
code but does not infer the Slurm termination reason.

Each step's time limit must fit within the enclosing `sbatch` lifetime; the
outer time limit can terminate unfinished or waiting steps. A step asking for
more memory or CPUs than the node can supply may remain waiting. Check the
target node's resources before submitting with `scontrol show node <node>`.
The legacy format launches every expanded `srun` at once and is intended for
modest groups. The round-based format launches only one complete round at a
time. If you later need a priority policy or thousands of experiments, add a
small dispatcher to `launch.py`, while still letting SLURM bind and enforce
the steps.

All launcher steps use `srun --mpi=none`. This leaves MPI process management
to commands that explicitly invoke an MPI launcher, such as PIPS-IPM++. The
PIPS runner uses the cluster's Open MPI launcher with `--bind-to none` and
`-np <ranks>` inside the step's CPU set, and logs each rank's inherited CPU
affinity through `OMPI_COMM_WORLD_RANK` and `OMPI_COMM_WORLD_SIZE`.

The SLURM options follow the one-exclusive-node, multiple-background-`srun`
pattern discussed from SCIP's
[`run_group.sh`](https://github.com/scipopt/scip/blob/master/check/cluster/run_group.sh)
and [`check_cluster.sh`](https://github.com/scipopt/scip/blob/master/check/cluster/check_cluster.sh).
The specific `--exact`, `--exclusive`, `--mem`, and `--time` behavior is
documented in [SchedMD's `srun` manual](https://slurm.schedmd.com/srun.html);
the allocation's `--exclusive --mem=0` behavior is documented in
[SchedMD's `sbatch` manual](https://slurm.schedmd.com/sbatch.html).

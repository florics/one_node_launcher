#!/usr/bin/env bash
# Runs inside a single srun task. The marker distinguishes step start from
# launcher submission, which may be much earlier if this step waited for CPUs.
set -eu

command=$1
marker=$2
started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
host=$(hostname)
step_id=${SLURM_STEP_ID:-unknown}

printf '%s\t%s\t%s\n' "$started" "$host" "$step_id" > "$marker"
printf 'STEP_STARTED=%s HOST=%s JOB=%s STEP=%s CPUS=%s\n' \
    "$started" "$host" "${SLURM_JOB_ID:-unknown}" "$step_id" \
    "${SLURM_CPUS_PER_TASK:-unknown}"

exec bash -c "$command"

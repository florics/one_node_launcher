#!/usr/bin/env bash

#SBATCH --job-name=mthesis_experiments
#SBATCH --output=/scratch/htc/lmehl/master_thesis/one_node_launcher/results/job_out/sbatch_job_%A.out
#SBATCH --error=/scratch/htc/lmehl/master_thesis/one_node_launcher/results/job_out/sbatch_job_%A.err

#SBATCH --partition=big
#SBATCH --time=01:00:00
#SBATCH --mem=50G
#SBATCH --cpus-per-task=4

#SBATCH --nodes=1
#SBATCH --hint=nomultithread
#SBATCH --cpu-freq=medium-medium:Performance
#SBATCH --constraint=Gold6338
#SBATCH --exclude=htc-gpu[001-115]

set -euo pipefail

if [[ -z ${SLURM_JOB_ID:-} ]]; then
    echo 'Submit this script with sbatch, e.g. sbatch submit_small.sh experiments.csv' >&2
    exit 2
fi

if (($# < 1)); then
    echo 'Usage: sbatch submit_small.sh <manifest.csv> [launch.py options]' >&2
    exit 2
fi

echo "------ SLURM allocation info ------"
echo "Job ID: ${SLURM_JOB_ID:-unknown}"
echo "Allocated node(s): ${SLURM_JOB_NODELIST:-unknown}"
echo "CPUs per task: ${SLURM_CPUS_PER_TASK:-not explicitly set}"
echo "CPUs allocated on node: ${SLURM_CPUS_ON_NODE:-unknown}"

echo "Batch-shell CPU affinity:"
taskset -cp $$ || true

echo "SLURM job information:"
scontrol show job "$SLURM_JOB_ID" | grep -E \
    'JobId=|NodeList=|NumNodes=|NumCPUs=|ReqTRES=|AllocTRES=|ReqS:C:T=|ReqB:S:C:T=' \
    || true

echo "Actual task affinity using srun:"
srun --cpu-bind=quiet bash -c '
    echo "Task PID: $$"
    taskset -cp $$
    echo "CPU/core mapping:"
    ALLOWED_CPUS=$(taskset -pc $$ | awk -F": " "{print \$2}")
    lscpu -p=CPU,CORE,SOCKET,NODE | grep -v "^#" |
    awk -F, -v allowed="$ALLOWED_CPUS" "
    BEGIN {
        n = split(allowed, ranges, \",\")
        for (i = 1; i <= n; i++) {
            if (ranges[i] ~ /-/) {
                split(ranges[i], r, \"-\")
                for (j = r[1]; j <= r[2]; j++)
                    a[j] = 1
            } else {
                a[ranges[i]] = 1
            }
        }
    }
    a[\$1] {
        printf \"CPU %-4s CORE %-4s SOCKET %-2s NUMA %-2s\n\", \$1, \$2, \$3, \$4
    }"
'

echo "CPU frequency SBATCH setting:"
grep -E '^#SBATCH[[:space:]]+--cpu-freq(=|[[:space:]])' "$0" \
    || echo "#SBATCH --cpu-freq: not set"

echo "SLURM CPU frequency request: ${SLURM_CPU_FREQ_REQ:-not reported}"

echo "SBATCH hint setting:"
grep -E '^#SBATCH[[:space:]]+--hint(=|[[:space:]])' "$0" \
    || echo "#SBATCH --hint: not set"

if [[ -n ${SLURM_MEM_PER_NODE:-} ]]; then
    echo "Requested memory per node: \
$((SLURM_MEM_PER_NODE / 1024)) GiB \
($(awk -v m="$SLURM_MEM_PER_NODE" \
'BEGIN { printf "%.2f", m*1024*1024/1e9 }') GB)"
else
    echo "Requested memory per node: all node memory (--mem=0)"
fi

echo "------ (end of SLURM allocation info) ------"

echo "------ Hardware info: ------"
lscpu
echo "------ (end of hardware info) ------"

# Set up modules, virtual environments, and licenses here if needed.

source ~/conda_init

#for usage at z1 cluster set http and https proxy environment variables
export https_proxy=http://squid.zib.de:3128
export http_proxy=http://squid.zib.de:3128 

export COPT_HOME=/software/COPT/copt80
export COPT_LICENSE_DIR=/software/COPT/copt80/ziblic/
export PATH=$COPT_HOME/bin:$PATH
#export DYLD_LIBRARY_PATH=$COPT_HOME/lib:$DYLD_LIBRARY_PATH
export LD_LIBRARY_PATH="${COPT_HOME}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

export GUROBI_HOME="/software/Gurobi/gurobi1300/linux64"
export PATH="${PATH}:${GUROBI_HOME}/bin"
if [[ -z ${LD_LIBRARY_PATH:-} ]]
then
    export LD_LIBRARY_PATH="${GUROBI_HOME}/lib"
else
    export LD_LIBRARY_PATH="$LD_LIBRARY_PATH:${GUROBI_HOME}/lib"
fi
export GRB_LICENSE_FILE=/software/Gurobi/gurobi1300/gurobi.lic

export XPAUTH_PATH=/software/opt-sw/xpress_licence/xpauth_31-mar-2030_9.8.xpr

#script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
script_dir="/scratch/htc/lmehl/master_thesis/one_node_launcher"
results_root=${RESULTS_ROOT:-"$script_dir/results"}
manifest="$1"
launcher_args=("${@:2}")
run_dir="$results_root/job${SLURM_JOB_ID}_$(date -u +%Y-%m-%d_%H-%M-%S)"

# Commands in experiments.csv run relative to the sbatch submission directory.
echo "Allocation: job=$SLURM_JOB_ID nodes=${SLURM_JOB_NODELIST:-unknown} cwd=$PWD"
echo "Manifest: $manifest"
echo "Results: $run_dir"

launcher_status=0
python3 "$script_dir/launch.py" "$manifest" --results-root "$results_root" --run-dir "$run_dir" "${launcher_args[@]}" || launcher_status=$?
if [[ -d $run_dir ]]; then
    for batch_log in "$script_dir/results/job_out/sbatch_job_${SLURM_JOB_ID}.out" "$script_dir/results/job_out/sbatch_job_${SLURM_JOB_ID}.err"; do
        [[ -f $batch_log ]] && mv "$batch_log" "$run_dir/"
    done
fi
exit "$launcher_status"

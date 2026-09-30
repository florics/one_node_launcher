#!/usr/bin/env python3
"""Generate the three-round scenario-catalogue and 2SSP benchmark manifest."""
from __future__ import annotations

import csv
from pathlib import Path


ROOT = Path("/scratch/htc/lmehl/master_thesis")
OPT = Path("/scratch/opt/lmehl/instances")
HERE = Path(__file__).resolve().parent
LOCAL_ROOT = HERE.parents[1]
PATH_CSVS = ROOT / "warm_start_experiments/scenario_catalogue_warmstart/csvs-for-arguments/paths_to_lp_and_bas_files"
EXPERIMENT_CSVS = ROOT / "warm_start_experiments/scenario_catalogue_warmstart/csvs-for-arguments/experiment_description"
XPRESS = ROOT / "base_problems/xpress"
COPT = ROOT / "base_problems/copt"
GUROBI = ROOT / "base_problems/gurobi"


def write(path: Path, text: str) -> None:
    (LOCAL_ROOT / path.relative_to(ROOT)).write_text(text, encoding="utf-8")


def scenario_sequence(path: Path, sequence: str, lp_dir: Path, lp_suffix: str, basis_dir: Path, base_basis_dir: Path) -> None:
    base_lp = OPT / "base/2050-100-el_eff-3costmods-1e9boundsremoved-investvarsmerged.lp.gz"
    lines = ["name,lp_file,basis_file,basis_description", f"3cm_br_ivm,{base_lp},{base_basis_dir / '3cm_br_ivm.bas.gz'},xpress_barrier_co_1thrd"]
    for value in range(1, 6):
        token = f"1p0{value}"
        basis = basis_dir / f"3cm_br_ivm_{sequence}{token}.bas.gz"
        lines.append(f"3cm_br_ivm_{sequence}{token},{lp_dir / (lp_suffix.format(value=value))},{basis},xpress_barrier_co_1thrd")
    write(path, "\n".join(lines) + "\n")


def row(rows, name, cpus, memory, time, parameter, command):
    rows.append({"name": name, "cpus": cpus, "memory": memory, "time": time, "measure": "true", "parameter_file": str(parameter), "command": command})


def write_manifest(path: Path, rows: list[dict[str, object]]) -> None:
    with (HERE / path).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("name", "cpus", "memory", "time", "measure", "parameter_file", "command"))
        writer.writeheader()
        writer.writerows(rows)


def solver_command(solver: str, lp: Path) -> str:
    return f"python3 {ROOT}/base_problems/{solver}/solve_lp_w_{solver}.py {lp} <result-dir> <parameter-file>"


def barrier_rows(rows, prefix, instances, solver, threads, crossover, parameter):
    for instance, lp in instances:
        row(rows, f"scen_cat_{prefix}_{solver}_barrier_{'co' if crossover else 'noco'}_{threads}thrd_{instance}", threads, "12G", "06:00:00", parameter, solver_command(solver, lp))


def warmstart_rows(rows, family: str, selection_family: str, data_csv: Path, names: list[str]) -> None:
    for basis in names:
        experiments_csv = EXPERIMENT_CSVS / f"full_matrix_{selection_family}_column_{basis}.csv"
        experiments = "\n".join(f"{target},{basis}" for target in names if target != basis)
        write(experiments_csv, f"instance_name,basis_name\n{experiments}\n")
        command = f"python3 {ROOT}/warm_start_experiments/scenario_catalogue_warmstart/warmstart_batch.py xpress {data_csv} 1 <parameter-file> <result-dir> --experiments-csv {experiments_csv}"
        row(rows, f"scen_cat_warmstart_{family}_column_{basis}", 1, "12G", "06:00:00", XPRESS / "parameters/dualsmplx_1Thrd_TimeLimit1h.prs", command)


def two_stage_rows(rows):
    calls = ROOT / "warm_start_experiments/two_stage_stochastic/csvs-for-calls"
    instances = {
        "3cm_elec1p03": "3cm-br-ivm_base_elec1p03.lp.gz",
        "3cm_elec1p05": "3cm-br-ivm_base_elec1p05.lp.gz",
        "3cm_elec1p01_heatboth1p01_elecheatboth1p01": "3cm-br-ivm_base_elec1p01_heatboth1p01_elecheatboth1p01.lp.gz",
        "nomods_elec1p01": "nomods_base_elec1p01.lp.gz",
    }
    methods = (
        ("copt_dual", "copt", 1, COPT / "parameters/dualsmplx_1Thrd_TimeLimit10h.par"),
        ("xpress_dual", "xpress", 1, XPRESS / "parameters/dualsmplx_1Thrd_TimeLimit10h.prs"),
        ("xpress_barrier_co", "xpress", 1, XPRESS / "parameters/barrier_wCrssvr_1Thrd_TimeLimit10h.prs"),
        ("gurobi_barrier_co", "gurobi", 16, GUROBI / "parameters/barrier_wCrssvr_16Thrds_TimeLimit10h.prm"),
        ("xpress_barrier_noco", "xpress", 1, XPRESS / "parameters/barrier_noCrssvr_1Thrd_TimeLimit10h.prs"),
        ("xpress_barrier_noco", "xpress", 16, XPRESS / "parameters/barrier_noCrssvr_16Thrds_TimeLimit10h.prs"),
    )
    for instance, filename in instances.items():
        lp = OPT / "2ssp/integrated" / filename
        memory = "24G" if instance == "3cm_elec1p01_heatboth1p01_elecheatboth1p01" else "12G"
        for method, solver, cpus, parameter in methods:
            row(rows, f"2ssp_benchmark_{instance}_{method}_{cpus}thrd", cpus, memory, "11:00:00", parameter, solver_command(solver, lp))
    call_names = {
        "3cm_elec1p03": "3cm_br_ivm-base-elec1p03",
        "3cm_elec1p05": "3cm_br_ivm-base-elec1p05",
        "3cm_elec1p01_heatboth1p01_elecheatboth1p01": "3cm_br_ivm-base-elec1p01-heatboth1p01-elecheatboth1p01",
        "nomods_elec1p01": "nomods-base-elec1p01",
    }
    construct = ROOT / "warm_start_experiments/two_stage_stochastic/construct_two_stage_basis.py"
    for instance, call_name in call_names.items():
        memory = "24G" if instance == "3cm_elec1p01_heatboth1p01_elecheatboth1p01" else "12G"
        for source, suffix in (("dual", "xpressSbasis.csv"), ("barrier1", "xpressBarrier1T_basis.csv")):
            reference = " --reference-scenario elecheatboth1p01" if instance == "3cm_elec1p01_heatboth1p01_elecheatboth1p01" else ""
            command = f"python3 {construct} xpress --input-csv {calls / (call_name + '-' + suffix)} --output-parent <result-dir> --parameter-file <parameter-file>{reference} --solve --repetitions 1 --no-measure"
            row(rows, f"2ssp_warmstart_{instance}_xpress_{source}", 1, memory, "11:00:00", XPRESS / "parameters/dualsmplx_1Thrd_TimeLimit10h.prs", command)
    stepwise = ROOT / "warm_start_experiments/two_stage_stochastic-stepwise_solving/stepwise_two_stage.py"
    for source, suffix in (("barrier1", "xpressBarrier1T_basis.csv"), ("dual", "xpressSbasis.csv")):
        stepwise_input = calls / f"3cm_br_ivm-base-elec1p01-{suffix}"
        for strategy in ("relative_ascending", "relative_descending", "random"):
            command = f"python3 {stepwise} xpress --input-csv {stepwise_input} --output-parent <result-dir> --parameter-file <parameter-file> --strategy {strategy} --repetitions 1 --no-measure"
            row(rows, f"2ssp_stepwise_3cm_elec1p01_xpress_{source}_{strategy}", 1, "12G", "11:00:00", XPRESS / "parameters/dualsmplx_1Thrd_TimeLimit10h.prs", command)


def main() -> None:
    xpress_params = XPRESS / "parameters"
    copt_params = COPT / "parameters"
    write(xpress_params / "barrier_wCrssvr_1Thrd_TimeLimit5h.prs", "OUTPUTLOG = 1\nTHREADS = 1\nLPFLAGS = 4\nTIMELIMIT = 18000\n")
    write(xpress_params / "barrier_wCrssvr_16Thrds_TimeLimit5h.prs", "OUTPUTLOG = 1\nTHREADS = 16\nLPFLAGS = 4\nTIMELIMIT = 18000\n")
    write(copt_params / "barrier_wCrssvr_1Thrd_TimeLimit5h.par", "Threads 1\nLpMethod 2\nCrossover 1\nBarIterLimit 2000\nTimeLimit 18000\nLogLevel 3\n")
    write(copt_params / "barrier_wCrssvr_16Thrds_TimeLimit5h.par", "Threads 16\nLpMethod 2\nCrossover 1\nBarIterLimit 2000\nTimeLimit 18000\nLogLevel 3\n")
    write(copt_params / "barrier_noCrssvr_16Thrds_TimeLimit5h.par", "Threads 16\nLpMethod 2\nCrossover 0\nBarIterLimit 2000\nTimeLimit 18000\nLogLevel 3\n")
    electricity_bases = ROOT / "warm_start_experiments/generate_solver_bases/results-electricity/run_2026-09-22_12-19-38/bases/xpress/barrier_1"
    heat_bases = ROOT / "warm_start_experiments/generate_solver_bases/results-heatboth/run_2026-09-22_12-42-32/bases/xpress/barrier_1"
    electricity_csv = PATH_CSVS / "3cm_br_ivm-elec1p00to1p05-barrier1_xpress.csv"
    heat_csv = PATH_CSVS / "3cm_br_ivm-heatboth1p00to1p05-barrier1_xpress.csv"
    # These files are generated solely for this manifest. Regenerate the set so
    # obsolete selection granularities cannot accumulate.
    local_experiments = LOCAL_ROOT / EXPERIMENT_CSVS.relative_to(ROOT)
    for stale in local_experiments.glob("full_matrix_*.csv"):
        stale.unlink()
    scenario_sequence(electricity_csv, "elec", OPT / "scenario_catalogue/electricity", "2050-100-el_eff-3costmods-1e9boundsremoved-investvarsmerged_electricity_d1p0{value}.lp.gz", electricity_bases, electricity_bases)
    scenario_sequence(heat_csv, "heatboth", OPT / "scenario_catalogue/heat_both", "2050-100-el_eff-3costmods-1e9boundsremoved-investvarsmerged_heat_central_heat_decentral_d1p0{value}.lp.gz", heat_bases, electricity_bases)

    base_3cm = OPT / "base/2050-100-el_eff-3costmods-1e9boundsremoved-investvarsmerged.lp.gz"
    base_nomods = OPT / "base/2050-100-el_eff-nothingmodified.lp.gz"
    plain_3cm = [("d1p00", base_3cm)] + [(f"d1p0{i}", OPT / f"scenario_catalogue/2050-100-el_eff-3costmods-1e9boundsremoved-investvarsmerged_d1p0{i}.lp.gz") for i in range(1, 6)]
    plain_nomods = [("d1p00", base_nomods)] + [(f"d1p0{i}", OPT / f"scenario_catalogue/2050-100-el_eff-nothingmodified_d1p0{i}.lp.gz") for i in range(1, 6)]
    electricity = [(f"elec1p0{i}", OPT / f"scenario_catalogue/electricity/2050-100-el_eff-3costmods-1e9boundsremoved-investvarsmerged_electricity_d1p0{i}.lp.gz") for i in range(1, 6)]
    heat = [(f"heatboth1p0{i}", OPT / f"scenario_catalogue/heat_both/2050-100-el_eff-3costmods-1e9boundsremoved-investvarsmerged_heat_central_heat_decentral_d1p0{i}.lp.gz") for i in range(1, 6)]
    rows = []
    for prefix, instances in (("3cm", plain_3cm), ("nomods", plain_nomods)):
        if prefix == "3cm":
            for threads in (1, 16): barrier_rows(rows, prefix, instances, "xpress", threads, True, xpress_params / f"barrier_wCrssvr_{threads}Thrd{'s' if threads > 1 else ''}_TimeLimit5h.prs")
            for threads in (1, 4): barrier_rows(rows, prefix, instances, "xpress", threads, False, xpress_params / f"barrier_noCrssvr_{threads}Thrd{'s' if threads > 1 else ''}_TimeLimit5h.prs")
        else:
            for threads in (1, 16): barrier_rows(rows, prefix, instances, "copt", threads, True, copt_params / f"barrier_wCrssvr_{threads}Thrd{'s' if threads > 1 else ''}_TimeLimit5h.par")
            for solver, threads, parameter in (("xpress", 1, xpress_params / "barrier_noCrssvr_1Thrd_TimeLimit5h.prs"), ("xpress", 8, xpress_params / "barrier_noCrssvr_8Thrds_TimeLimit5h.prs"), ("copt", 16, copt_params / "barrier_noCrssvr_16Thrds_TimeLimit5h.par")):
                barrier_rows(rows, prefix, instances, solver, threads, False, parameter)
    for prefix, instances in (("elec", electricity), ("heatboth", heat)):
        for threads in (1, 16): barrier_rows(rows, prefix, instances, "xpress", threads, True, xpress_params / f"barrier_wCrssvr_{threads}Thrd{'s' if threads > 1 else ''}_TimeLimit5h.prs")
        for threads in (1, 4): barrier_rows(rows, prefix, instances, "xpress", threads, False, xpress_params / f"barrier_noCrssvr_{threads}Thrd{'s' if threads > 1 else ''}_TimeLimit5h.prs")
    warmstart_rows(rows, "3cm_xpress_dual", "3cm", PATH_CSVS / "3cm_br_ivm-1p00to1p05-dualsmplx_xpress.csv", ["3cm_br_ivm", *[f"3cm_br_ivm_d1p0{i}" for i in range(1, 6)]])
    warmstart_rows(rows, "3cm_xpress_barrier1", "3cm", PATH_CSVS / "3cm_br_ivm-1p00to1p05-barrier1_xpress.csv", ["3cm_br_ivm", *[f"3cm_br_ivm_d1p0{i}" for i in range(1, 6)]])
    warmstart_rows(rows, "nomods_xpress_dual", "nomods", PATH_CSVS / "nomods-1p00to1p05-dualsmplx_xpress.csv", ["nomods", *[f"nomods_d1p0{i}" for i in range(1, 6)]])
    warmstart_rows(rows, "nomods_xpress_barrier1", "nomods", PATH_CSVS / "nomods-1p00to1p05-barrier1_copt.csv", ["nomods", *[f"nomods_d1p0{i}" for i in range(1, 6)]])
    warmstart_rows(rows, "elec_xpress_barrier1", "elec", electricity_csv, ["3cm_br_ivm", *[f"3cm_br_ivm_elec1p0{i}" for i in range(1, 6)]])
    warmstart_rows(rows, "heatboth_xpress_barrier1", "heatboth", heat_csv, ["3cm_br_ivm", *[f"3cm_br_ivm_heatboth1p0{i}" for i in range(1, 6)]])
    two_stage_rows(rows)
    scenario_catalogue_rows = [row for row in rows if row["name"].startswith("scen_cat_")]
    two_stage_manifest_rows = [row for row in rows if row["name"].startswith("2ssp_")]
    if len(scenario_catalogue_rows) + len(two_stage_manifest_rows) != len(rows):
        raise ValueError("Every manifest row must be assigned to scenario-catalogue or 2SSP")
    write_manifest(Path("scen_catalogue_2ssp_benchmarks_rounds.csv"), rows)
    write_manifest(Path("scen_catalogue_benchmarks_rounds.csv"), scenario_catalogue_rows)
    write_manifest(Path("2ssp_benchmarks_rounds.csv"), two_stage_manifest_rows)


if __name__ == "__main__":
    main()

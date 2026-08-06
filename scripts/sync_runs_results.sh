#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  sync_runs_results.sh [--download|--upload|--mirror-upload] [options] [-- coscli sync flags...]

Modes:
  --download        Sync COS runs/results to the local project (default).
  --upload          Sync local runs/results to COS without deleting remote extras.
  --mirror-upload   Sync local runs/results to COS and delete remote files missing locally.

Options:
  --remote-uri URI   Override OTG_LAB_RUN_RESULTS_COS_URI.
  --project-dir DIR  Override the automatically detected OTG Lab project directory.
  --yes, -y          Required with --mirror-upload.
  --help, -h         Show this help.

Environment:
  OTG_LAB_RUN_RESULTS_COS_URI  Default: cos://psi-user-data-1351596430/litang/mc/otg-lab
  COSCLI_SNAPSHOT_PATH         Default: ~/.cache/coscli-snapshot/otg-lab-run-results-<mode>
  COSCLI_FAIL_OUTPUT_PATH      Default: ~/.cache/coscli-output/otg-lab-run-results-<mode>/failures
  COSCLI_PROCESS_LOG_PATH      Default: ~/.cache/coscli-output/otg-lab-run-results-<mode>/process
  COSCLI_ROUTINES              Default: 8
  COSCLI_THREAD_NUM            Default: 8

Scope:
  experiments/E*/runs/**       experiments/E*/results/**
  analyses/A*/runs/**          analyses/A*/results/**

  _template, sharded_runs, and files outside those directories are not synced.
  Empty directories are not represented in COS.

Examples:
  scripts/sync_runs_results.sh
  scripts/sync_runs_results.sh --upload
  scripts/sync_runs_results.sh --mirror-upload --yes
  scripts/sync_runs_results.sh --download --remote-uri cos://psi/team/otg-lab
  scripts/sync_runs_results.sh --upload -- --exclude '.*\.tmp$'

Safety:
  --mirror-upload permanently deletes matching remote objects that do not exist
  locally. Inspect the local runs/results tree before confirming with --yes.
  Extra --include flags are rejected so the protected sync scope cannot expand.
EOF
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mode="download"
mode_set=false
yes=false
coscli_args=()

remote_uri="${OTG_LAB_RUN_RESULTS_COS_URI:-cos://psi-user-data-1351596430/litang/mc/otg-lab}"
project_dir="${script_dir}/.."
routines="${COSCLI_ROUTINES:-8}"
thread_num="${COSCLI_THREAD_NUM:-8}"
scope_regex='(^|/)(experiments/E[0-9][^/]*/|analyses/A[0-9][^/]*/)(runs|results)/.*$'

die() {
    echo "error: $*" >&2
    exit 1
}

set_mode() {
    local next_mode="$1"
    if [[ "${mode_set}" == true && "${mode}" != "${next_mode}" ]]; then
        die "choose only one mode; got both --${mode} and --${next_mode}"
    fi
    mode="${next_mode}"
    mode_set=true
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --download)
            set_mode "download"
            shift
            ;;
        --upload)
            set_mode "upload"
            shift
            ;;
        --mirror-upload)
            set_mode "mirror-upload"
            shift
            ;;
        --remote-uri)
            [[ $# -ge 2 ]] || die "--remote-uri requires a value"
            remote_uri="$2"
            shift 2
            ;;
        --remote-uri=*)
            remote_uri="${1#*=}"
            shift
            ;;
        --project-dir)
            [[ $# -ge 2 ]] || die "--project-dir requires a value"
            project_dir="$2"
            shift 2
            ;;
        --project-dir=*)
            project_dir="${1#*=}"
            shift
            ;;
        --yes|-y)
            yes=true
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        --)
            shift
            coscli_args+=("$@")
            break
            ;;
        *)
            die "unknown argument '$1'; pass extra coscli flags after --"
            ;;
    esac
done

if (( ${#coscli_args[@]} )); then
    for arg in "${coscli_args[@]}"; do
        case "${arg}" in
            --include|--include=*)
                die "extra --include is not allowed because it could expand the protected sync scope"
                ;;
            --delete|--delete=*)
                if [[ "${mode}" != "mirror-upload" ]]; then
                    die "--delete is only allowed via --mirror-upload"
                fi
                ;;
        esac
    done
fi

if [[ "${mode}" == "mirror-upload" && "${yes}" != true ]]; then
    die "--mirror-upload deletes matching remote COS files missing locally; rerun with --yes after checking runs/results"
fi

if ! command -v coscli >/dev/null 2>&1; then
    die "coscli is not installed or not in PATH"
fi

[[ -d "${project_dir}" ]] || die "project directory does not exist: ${project_dir}"
project_dir="$(cd "${project_dir}" && pwd)"
[[ -d "${project_dir}/experiments" ]] || die "project directory is missing experiments/: ${project_dir}"
[[ -d "${project_dir}/analyses" ]] || die "project directory is missing analyses/: ${project_dir}"

remote_uri="${remote_uri%/}"
if [[ ! "${remote_uri}" =~ ^cos://[^/]+/.+ ]]; then
    die "remote URI must include a bucket alias and a non-empty prefix: ${remote_uri}"
fi

[[ "${routines}" =~ ^[1-9][0-9]*$ ]] || die "COSCLI_ROUTINES must be a positive integer"
[[ "${thread_num}" =~ ^[1-9][0-9]*$ ]] || die "COSCLI_THREAD_NUM must be a positive integer"

snapshot_path="${COSCLI_SNAPSHOT_PATH:-${HOME}/.cache/coscli-snapshot/otg-lab-run-results-${mode}}"
fail_output_path="${COSCLI_FAIL_OUTPUT_PATH:-${HOME}/.cache/coscli-output/otg-lab-run-results-${mode}/failures}"
process_log_path="${COSCLI_PROCESS_LOG_PATH:-${HOME}/.cache/coscli-output/otg-lab-run-results-${mode}/process}"
mkdir -p "${snapshot_path}" "${fail_output_path}" "${process_log_path}"
snapshot_path="$(cd "${snapshot_path}" && pwd)"
fail_output_path="$(cd "${fail_output_path}" && pwd)"
process_log_path="$(cd "${process_log_path}" && pwd)"

for managed_path in "${snapshot_path}" "${fail_output_path}" "${process_log_path}"; do
    if [[ "${managed_path}" == "${project_dir}" || "${managed_path}" == "${project_dir}/"* ]]; then
        die "COSCLI snapshot and output paths must be outside the project directory: ${managed_path}"
    fi
done

common_flags=(
    -r
    --log-path "${process_log_path}"
    --snapshot-path "${snapshot_path}"
    --fail-output-path "${fail_output_path}"
    --process-log-path "${process_log_path}"
    --routines "${routines}"
    --thread-num "${thread_num}"
)
protected_flags=(--include "${scope_regex}")

case "${mode}" in
    download)
        echo "download: ${remote_uri} -> ${project_dir}"
        sync_args=(sync "${remote_uri}" "${project_dir}" "${common_flags[@]}")
        if (( ${#coscli_args[@]} )); then
            sync_args+=("${coscli_args[@]}")
        fi
        sync_args+=("${protected_flags[@]}")
        coscli "${sync_args[@]}"
        ;;
    upload)
        echo "upload: ${project_dir} -> ${remote_uri}"
        sync_args=(sync "${project_dir}" "${remote_uri}" "${common_flags[@]}")
        if (( ${#coscli_args[@]} )); then
            sync_args+=("${coscli_args[@]}")
        fi
        sync_args+=(--skip-dir "${protected_flags[@]}")
        coscli "${sync_args[@]}"
        ;;
    mirror-upload)
        echo "mirror-upload: ${project_dir} -> ${remote_uri}"
        sync_args=(sync "${project_dir}" "${remote_uri}" "${common_flags[@]}")
        if (( ${#coscli_args[@]} )); then
            sync_args+=("${coscli_args[@]}")
        fi
        sync_args+=(--skip-dir --delete --force "${protected_flags[@]}")
        coscli "${sync_args[@]}"
        ;;
    *)
        die "unknown mode: ${mode}"
        ;;
esac

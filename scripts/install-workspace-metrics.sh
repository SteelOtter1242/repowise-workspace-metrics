#!/usr/bin/env bash
set -euo pipefail

readonly REPOWISE_REPO_URL="https://github.com/SteelOtter1242/repowise-ifx.git"
readonly REPOWISE_BRANCH="beta"
readonly WORKSHOP_BETA_COMMIT="86caf6a53b4c39aba5579be4229b4cb442c6038f"
readonly REPOWISE_SSH_URL="git@github.com:SteelOtter1242/repowise-ifx.git"
readonly METRICS_REPO_URL="https://github.com/SteelOtter1242/repowise-workspace-metrics.git"
readonly METRICS_REPO_SSH_URL="git@github.com:SteelOtter1242/repowise-workspace-metrics.git"
readonly METRICS_BRANCH="main"
readonly METRICS_COMMIT="3d42e58c8d0636fca727ea5b9cd4a9f667c3fc2d"
export REPOWISE_HOME="${REPOWISE_HOME:-${HOME}/.local/share/repowise-workshop}"
REPOWISE_SOURCE="${REPOWISE_HOME}/repowise-ifx"
METRICS_SOURCE="${REPOWISE_HOME}/repowise-workspace-metrics"
REPOWISE_VENV="${REPOWISE_HOME}/.venv"
REPOWISE_PYTHON="${REPOWISE_VENV}/bin/python"
REPOWISE_BIN="${REPOWISE_VENV}/bin/repowise"

UPDATE=0

usage() {
  cat <<'EOF'
Usage: install-workspace-metrics.sh [--update|--upgrade]

Install Repowise beta and the workspace-metrics extension into REPOWISE_HOME.
This script can be downloaded and run without checking out the extension repo.
The first install uses pinned snapshots. Re-running reuses the checkouts and
environment. Pass --update or --upgrade to fast-forward Repowise to beta and
the extension to main.

Set REPOWISE_HOME once to choose a different installation directory.
EOF
}

die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

while (($#)); do
  case "$1" in
    --update|--upgrade)
      UPDATE=1
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      die "Unknown option: $1 (use --help)"
      ;;
  esac
  shift
done

command -v git >/dev/null 2>&1 || die "git is required"
command -v uv >/dev/null 2>&1 || die "uv is required (https://docs.astral.sh/uv/)"

mkdir -p "${REPOWISE_HOME}"

if [[ -d "${REPOWISE_SOURCE}/.git" ]]; then
  origin="$(git -C "${REPOWISE_SOURCE}" remote get-url origin)"
  case "${origin}" in
    "${REPOWISE_REPO_URL}"|"${REPOWISE_SSH_URL}")
      ;;
    *)
      die "Existing checkout at ${REPOWISE_SOURCE} has unexpected origin: ${origin}"
      ;;
  esac
  repowise_changes="$(git -C "${REPOWISE_SOURCE}" status --porcelain)"
  [[ -z "${repowise_changes}" ]] ||
    die "Checkout has local changes; commit or stash them before continuing"

else
  [[ ! -e "${REPOWISE_SOURCE}" ]] ||
    die "Path exists but is not a Repowise git checkout: ${REPOWISE_SOURCE}"
  git clone --branch "${REPOWISE_BRANCH}" --single-branch \
    "${REPOWISE_REPO_URL}" "${REPOWISE_SOURCE}"
  git -C "${REPOWISE_SOURCE}" switch --detach "${WORKSHOP_BETA_COMMIT}"
fi

if [[ -d "${METRICS_SOURCE}/.git" ]]; then
  metrics_origin="$(git -C "${METRICS_SOURCE}" remote get-url origin)"
  case "${metrics_origin}" in
    "${METRICS_REPO_URL}"|"${METRICS_REPO_SSH_URL}")
      ;;
    *)
      die "Existing extension checkout at ${METRICS_SOURCE} has unexpected origin: ${metrics_origin}"
      ;;
  esac
  metrics_changes="$(git -C "${METRICS_SOURCE}" status --porcelain)"
  [[ -z "${metrics_changes}" ]] ||
    die "Extension checkout has local changes; commit or stash them before continuing"
else
  [[ ! -e "${METRICS_SOURCE}" ]] ||
    die "Path exists but is not an extension git checkout: ${METRICS_SOURCE}"
  git clone --branch "${METRICS_BRANCH}" --single-branch \
    "${METRICS_REPO_URL}" "${METRICS_SOURCE}"
  if ! git -C "${METRICS_SOURCE}" cat-file -e "${METRICS_COMMIT}^{commit}" 2>/dev/null; then
    git -C "${METRICS_SOURCE}" fetch origin "${METRICS_BRANCH}"
  fi
  git -C "${METRICS_SOURCE}" switch --detach "${METRICS_COMMIT}"
fi

if ((UPDATE)); then
  git -C "${REPOWISE_SOURCE}" fetch origin "${REPOWISE_BRANCH}"
  git -C "${METRICS_SOURCE}" fetch origin "${METRICS_BRANCH}"
  git -C "${REPOWISE_SOURCE}" merge-base --is-ancestor HEAD "origin/${REPOWISE_BRANCH}" ||
    die "Repowise checkout cannot fast-forward to origin/${REPOWISE_BRANCH}"
  git -C "${METRICS_SOURCE}" merge-base --is-ancestor HEAD "origin/${METRICS_BRANCH}" ||
    die "Extension checkout cannot fast-forward to origin/${METRICS_BRANCH}"
  git -C "${REPOWISE_SOURCE}" merge --ff-only "origin/${REPOWISE_BRANCH}"
  git -C "${METRICS_SOURCE}" merge --ff-only "origin/${METRICS_BRANCH}"
fi
[[ -f "${METRICS_SOURCE}/pyproject.toml" ]] ||
  die "Workspace-metrics project file was not found in ${METRICS_SOURCE}"

if [[ ! -x "${REPOWISE_PYTHON}" ]]; then
  uv venv --python 3.11 "${REPOWISE_VENV}"
fi

uv pip install --python "${REPOWISE_PYTHON}" \
  --reinstall-package repowise \
  --reinstall-package repowise-workspace-metrics \
  "${REPOWISE_SOURCE}" \
  "${METRICS_SOURCE}"

"${REPOWISE_PYTHON}" - <<'PY'
from importlib.metadata import version

installed = version("repowise").split("+", 1)[0]
if installed != "0.61.0":
    raise SystemExit(
        f"workspace-metrics requires Repowise 0.61.0; found {installed}. "
        "The beta update is not compatible with this extension revision."
    )

try:
    from repowise.core.repo_config import analysis_paths
    from repowise.core.test_paths import is_test_related_path
except ImportError as exc:
    raise SystemExit(f"Repowise beta is missing required metrics helpers: {exc}") from exc
PY
"${REPOWISE_BIN}" plugins
"${REPOWISE_BIN}" workspace-metrics --help >/dev/null

printf '\nInstalled Repowise at %s\n' "$(git -C "${REPOWISE_SOURCE}" rev-parse --short HEAD)"
printf 'Installed workspace-metrics at %s\n' "$(git -C "${METRICS_SOURCE}" rev-parse --short HEAD)"
printf 'Environment: %s\n' "${REPOWISE_VENV}"
printf 'Run commands with: %s\n' "${REPOWISE_BIN}"

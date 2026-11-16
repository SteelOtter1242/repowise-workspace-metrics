#!/usr/bin/env bash
set -euo pipefail

readonly REPOWISE_REPO_URL="https://github.com/SteelOtter1242/repowise-ifx.git"
readonly REPOWISE_BRANCH="beta"
readonly WORKSHOP_BETA_COMMIT="86caf6a53b4c39aba5579be4229b4cb442c6038f"
readonly REPOWISE_SSH_URL="git@github.com:SteelOtter1242/repowise-ifx.git"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SCRIPT_DIR}/../pyproject.toml" ]]; then
  METRICS_PACKAGE="${SCRIPT_DIR}/.."
else
  METRICS_PACKAGE="${SCRIPT_DIR}/../packages/repowise-workspace-metrics"
fi
METRICS_PACKAGE="$(cd -- "${METRICS_PACKAGE}" && pwd)"
export REPOWISE_HOME="${REPOWISE_HOME:-${HOME}/.local/share/repowise-workshop}"
REPOWISE_SOURCE="${REPOWISE_HOME}/repowise-ifx"
REPOWISE_VENV="${REPOWISE_HOME}/.venv"
REPOWISE_PYTHON="${REPOWISE_VENV}/bin/python"
REPOWISE_BIN="${REPOWISE_VENV}/bin/repowise"

UPDATE_BETA=0

usage() {
  cat <<'EOF'
Usage: install-workspace-metrics.sh [--update]

Install Repowise beta and the workspace-metrics extension into REPOWISE_HOME.
The first install uses the workshop beta snapshot. Re-running reuses the
existing checkout and environment. Pass --update to fast-forward the checkout
to the latest origin/beta before reinstalling.

Set REPOWISE_HOME once to choose a different installation directory.
EOF
}

die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

while (($#)); do
  case "$1" in
    --update)
      UPDATE_BETA=1
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
[[ -f "${METRICS_PACKAGE}/pyproject.toml" ]] ||
  die "Run this script from the repowise-extensions checkout; workspace-metrics source was not found"

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
  [[ -z "$(git -C "${REPOWISE_SOURCE}" status --porcelain)" ]] ||
    die "Checkout has local changes; commit or stash them before continuing"

  if ((UPDATE_BETA)); then
    git -C "${REPOWISE_SOURCE}" fetch origin "${REPOWISE_BRANCH}"
    if git -C "${REPOWISE_SOURCE}" show-ref --verify --quiet "refs/heads/${REPOWISE_BRANCH}"; then
      git -C "${REPOWISE_SOURCE}" switch "${REPOWISE_BRANCH}"
    else
      git -C "${REPOWISE_SOURCE}" switch --create "${REPOWISE_BRANCH}" \
        --track "origin/${REPOWISE_BRANCH}"
    fi
    git -C "${REPOWISE_SOURCE}" merge --ff-only "origin/${REPOWISE_BRANCH}"
  fi
else
  [[ ! -e "${REPOWISE_SOURCE}" ]] ||
    die "Path exists but is not a Repowise git checkout: ${REPOWISE_SOURCE}"
  git clone --branch "${REPOWISE_BRANCH}" --single-branch \
    "${REPOWISE_REPO_URL}" "${REPOWISE_SOURCE}"
  git -C "${REPOWISE_SOURCE}" switch --detach "${WORKSHOP_BETA_COMMIT}"
  if ((UPDATE_BETA)); then
    git -C "${REPOWISE_SOURCE}" fetch origin "${REPOWISE_BRANCH}"
    git -C "${REPOWISE_SOURCE}" switch "${REPOWISE_BRANCH}"
    git -C "${REPOWISE_SOURCE}" merge --ff-only "origin/${REPOWISE_BRANCH}"
  fi
fi

if [[ ! -x "${REPOWISE_PYTHON}" ]]; then
  uv venv --python 3.11 "${REPOWISE_VENV}"
fi

uv pip install --python "${REPOWISE_PYTHON}" \
  --reinstall-package repowise \
  --reinstall-package repowise-workspace-metrics \
  "${REPOWISE_SOURCE}" \
  "${METRICS_PACKAGE}"

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
printf 'Environment: %s\n' "${REPOWISE_VENV}"
printf 'Run commands with: %s\n' "${REPOWISE_BIN}"

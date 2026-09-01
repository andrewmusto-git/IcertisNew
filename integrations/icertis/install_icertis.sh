#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/VEZA/icertis-veza"
REPO_URL="https://github.com/andrewmusto-git/IcertisNew.git"
BRANCH="main"
NON_INTERACTIVE=0
OVERWRITE_ENV=0

usage() {
  cat <<'EOF'
Usage: install_icertis.sh [--non-interactive] [--overwrite-env] [--install-dir PATH] [--repo-url URL] [--branch NAME]

Installs the Icertis SaaS connector into /opt/VEZA and prompts for the required auth values.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --non-interactive) NON_INTERACTIVE=1 ;;
    --overwrite-env) OVERWRITE_ENV=1 ;;
    --install-dir) INSTALL_DIR="$2"; shift ;;
    --repo-url) REPO_URL="$2"; shift ;;
    --branch) BRANCH="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1"; usage; exit 1 ;;
  esac
  shift
done

show_milestone() { printf '\033[36m[MILESTONE %s/%s]\033[0m %s\n' "$1" "$2" "$3"; }
info() { printf '\033[36m[i]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[!]\033[0m %s\n' "$*"; }
pass() { printf '\033[32m[+]\033[0m %s\n' "$*"; }
fail() { printf '\033[31m[-]\033[0m %s\n' "$*" >&2; }

_detect_pkg_manager() {
  if command -v dnf >/dev/null 2>&1; then echo dnf
  elif command -v yum >/dev/null 2>&1; then echo yum
  elif command -v apt-get >/dev/null 2>&1; then echo apt-get
  else echo ""; fi
}

_install_pkg() {
  local pkg="$1"
  local pm="$( _detect_pkg_manager )"
  case "$pm" in
    dnf|yum)
      dnf install -y "$pkg" >/dev/null 2>&1 || yum install -y "$pkg" >/dev/null 2>&1 ;;
    apt-get)
      apt-get update >/dev/null 2>&1 && apt-get install -y "$pkg" >/dev/null 2>&1 ;;
    *)
      echo "Unsupported package manager" >&2
      return 1 ;;
  esac
}

ensure_prereqs() {
  show_milestone 1 7 "Checking system prerequisites"
  if ! command -v git >/dev/null 2>&1; then _install_pkg git; fi
  if ! command -v curl >/dev/null 2>&1; then _install_pkg curl; fi
  if ! command -v python3 >/dev/null 2>&1; then _install_pkg python3; fi
  if ! python3 -m pip --version >/dev/null 2>&1; then _install_pkg python3-pip; fi
  if ! python3 -m venv --help >/dev/null 2>&1; then
    case "$( _detect_pkg_manager )" in
      dnf|yum) _install_pkg python3-virtualenv ;;
      apt-get) _install_pkg python3-venv ;;
    esac
  fi

  python3 - <<'PY'
import sys
if sys.version_info < (3, 9):
    raise SystemExit('Python 3.9+ is required')
PY
  pass "Prerequisites validated"
}

prompt_or_default() {
  local prompt="$1"
  local default_value="$2"
  local value
  if [[ "$NON_INTERACTIVE" -eq 1 ]]; then
    printf '%s\n' "$default_value"
    return
  fi
  printf '%s [%s]: ' "$prompt" "$default_value" >&2
  IFS= read -r value </dev/tty
  if [[ -z "$value" ]]; then
    printf '%s\n' "$default_value"
  else
    printf '%s\n' "$value"
  fi
}

prompt_secret() {
  local prompt="$1"
  local default_value="$2"
  local value
  if [[ "$NON_INTERACTIVE" -eq 1 ]]; then
    printf '%s\n' "$default_value"
    return
  fi
  printf '%s [%s]: ' "$prompt" "$default_value" >&2
  IFS= read -r -s value </dev/tty
  echo >&2
  if [[ -z "$value" ]]; then
    printf '%s\n' "$default_value"
  else
    printf '%s\n' "$value"
  fi
}

create_env_file() {
  local env_path="$1"
  if [[ -f "$env_path" && "$OVERWRITE_ENV" -ne 1 ]]; then
    warn "Keeping existing $env_path"
    return 0
  fi

  cat > "$env_path" <<EOF
# Icertis SaaS connector configuration
ICERTIS_BASE_URL=${ICERTIS_BASE_URL_VALUE}
ICERTIS_USERS_URL=${ICERTIS_USERS_URL_VALUE}
ICERTIS_GROUPS_URL=${ICERTIS_GROUPS_URL_VALUE}
ICERTIS_ORG_UNITS_URL=${ICERTIS_ORG_UNITS_URL_VALUE}
ICERTIS_USERS_PATH=/api/v1/users
ICERTIS_ROLES_PATH=/api/v1/roles
ICERTIS_PERMISSIONS_PATH=/api/v1/permissions
ICERTIS_GRANT_TYPE=${ICERTIS_GRANT_TYPE_VALUE}
ICERTIS_TOKEN_URL=${ICERTIS_TOKEN_URL_VALUE}
ICERTIS_CLIENT_ID=${ICERTIS_CLIENT_ID_VALUE}
ICERTIS_CLIENT_SECRET=${ICERTIS_CLIENT_SECRET_VALUE}
ICERTIS_OAUTH_REQUEST_PARAMETERS={"${ICERTIS_OAUTH_KEY_VALUE}":"${ICERTIS_OAUTH_PARAM_VALUE}"}

# Veza settings
VEZA_URL=${VEZA_URL_VALUE}
VEZA_API_KEY=${VEZA_API_KEY_VALUE}

# Optional overrides
# PROVIDER_NAME=Icertis
# DATASOURCE_NAME=Icertis-Production
EOF
  chmod 600 "$env_path"
  pass "Created $env_path"
}

install_connector() {
  local scripts_dir="$INSTALL_DIR/scripts"
  mkdir -p "$INSTALL_DIR" "$scripts_dir" "$INSTALL_DIR/logs"

  show_milestone 2 7 "Creating the /opt/VEZA folder structure"
  mkdir -p "$INSTALL_DIR" "$scripts_dir" "$INSTALL_DIR/logs"

  show_milestone 3 7 "Creating Python virtual environment"
  python3 -m venv "$scripts_dir/venv"

  show_milestone 4 7 "Installing connector dependencies"
  "$scripts_dir/venv/bin/pip" install --upgrade pip >/dev/null 2>&1
  "$scripts_dir/venv/bin/pip" install -r "$SCRIPT_DIR/requirements.txt" >/dev/null 2>&1

  show_milestone 5 7 "Copying integration files into the install directory"
  cp "$SCRIPT_DIR/icertis.py" "$scripts_dir/icertis.py"
  cp "$SCRIPT_DIR/.env.example" "$scripts_dir/.env.example"

  show_milestone 6 7 "Writing environment configuration"
  create_env_file "$scripts_dir/.env"

  show_milestone 7 7 "Validating final installation state"
  pass "Installed connector under $INSTALL_DIR"
}

main() {
  ensure_prereqs

  if [[ -n "$REPO_URL" ]]; then
    info "Repository URL provided; staging connector files from $REPO_URL"
    tmp_dir="$(mktemp -d)"
    GIT_TERMINAL_PROMPT=0 git clone --branch "$BRANCH" --depth 1 --single-branch "$REPO_URL" "$tmp_dir" >/dev/null 2>&1 || {
      fail "git clone failed"
      exit 1
    }
    mkdir -p "$INSTALL_DIR/scripts"
    cp -f "$tmp_dir/integrations/icertis"/*.py "$INSTALL_DIR/scripts/" 2>/dev/null || true
    cp -f "$tmp_dir/integrations/icertis/requirements.txt" "$INSTALL_DIR/scripts/requirements.txt" 2>/dev/null || true
    rm -rf "$tmp_dir"
  fi

  if [[ "$NON_INTERACTIVE" -eq 0 ]]; then
    info "Collecting environment values..."
    VEZA_URL_VALUE="$(prompt_or_default "Veza URL" "https://your-veza-instance.example.com")"
    VEZA_API_KEY_VALUE="$(prompt_or_default "Veza API key" "your_veza_api_key_here")"
    ICERTIS_BASE_URL_VALUE="$(prompt_or_default "Icertis Base URL" "https://your-icertis-base-url.example.com")"
    ICERTIS_USERS_URL_VALUE="$(prompt_or_default "Icertis Users URL" "https://tenant-api.icertis.com/api/Users")"
    ICERTIS_GROUPS_URL_VALUE="$(prompt_or_default "Icertis Groups URL" "https://tenant-api.icertis.com/api/Groups")"
    ICERTIS_ORG_UNITS_URL_VALUE="$(prompt_or_default "Icertis Org Units URL" "https://tenant-business-api.icertis.com/api/v1/organizationunits")"
    ICERTIS_TOKEN_URL_VALUE="$(prompt_or_default "Icertis Token URL" "https://login.example.com/oauth2/v2.0/token")"
    ICERTIS_GRANT_TYPE_VALUE="$(prompt_or_default "OAuth Grant Type" "client_credentials")"
    ICERTIS_CLIENT_ID_VALUE="$(prompt_or_default "Client ID" "your_client_id_here")"
    ICERTIS_CLIENT_SECRET_VALUE="$(prompt_secret "Client Secret" "your_client_secret_here")"
    ICERTIS_OAUTH_KEY_VALUE="$(prompt_or_default "OAuth Request Parameter Key" "scope")"
    ICERTIS_OAUTH_PARAM_VALUE="$(prompt_or_default "OAuth Request Parameter Value" "api://your-app-id/.default")"
  else
    VEZA_URL_VALUE="${VEZA_URL:-https://your-veza-instance.example.com}"
    VEZA_API_KEY_VALUE="${VEZA_API_KEY:-your_veza_api_key_here}"
    ICERTIS_BASE_URL_VALUE="${ICERTIS_BASE_URL:-https://your-icertis-base-url.example.com}"
    ICERTIS_USERS_URL_VALUE="${ICERTIS_USERS_URL:-https://tenant-api.icertis.com/api/Users}"
    ICERTIS_GROUPS_URL_VALUE="${ICERTIS_GROUPS_URL:-https://tenant-api.icertis.com/api/Groups}"
    ICERTIS_ORG_UNITS_URL_VALUE="${ICERTIS_ORG_UNITS_URL:-https://tenant-business-api.icertis.com/api/v1/organizationunits}"
    ICERTIS_TOKEN_URL_VALUE="${ICERTIS_TOKEN_URL:-https://login.example.com/oauth2/v2.0/token}"
    ICERTIS_GRANT_TYPE_VALUE="${ICERTIS_GRANT_TYPE:-client_credentials}"
    ICERTIS_CLIENT_ID_VALUE="${ICERTIS_CLIENT_ID:-your_client_id_here}"
    ICERTIS_CLIENT_SECRET_VALUE="${ICERTIS_CLIENT_SECRET:-your_client_secret_here}"
    ICERTIS_OAUTH_KEY_VALUE="${ICERTIS_OAUTH_KEY:-scope}"
    ICERTIS_OAUTH_PARAM_VALUE="${ICERTIS_OAUTH_VALUE:-api://your-app-id/.default}"
  fi

  install_connector

  cat <<EOF

Installation complete.

Install path: $INSTALL_DIR
Scripts: $INSTALL_DIR/scripts
Logs: $INSTALL_DIR/logs

Next step:
  cd $INSTALL_DIR/scripts
  source venv/bin/activate
  python3 icertis.py --env-file .env --dry-run --save-json --log-level DEBUG
EOF
}

main "$@"

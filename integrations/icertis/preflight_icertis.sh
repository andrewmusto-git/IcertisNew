#!/usr/bin/env bash
set -u
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="${SCRIPT_DIR}/preflight_$(date +%Y%m%d_%H%M%S).log"
TESTS_PASSED=0
TESTS_FAILED=0
TESTS_WARNING=0

GREEN='\033[32m'; RED='\033[31m'; YELLOW='\033[33m'; CYAN='\033[36m'; RESET='\033[0m'

log_line() { printf '%s\n' "$*" | tee -a "$LOG_FILE" >/dev/null; }
pass() { printf '%b%s%b\n' "$GREEN" "✓ $*" "$RESET" | tee -a "$LOG_FILE" >/dev/null; TESTS_PASSED=$((TESTS_PASSED + 1)); }
fail() { printf '%b%s%b\n' "$RED" "✗ $*" "$RESET" | tee -a "$LOG_FILE" >/dev/null; TESTS_FAILED=$((TESTS_FAILED + 1)); }
warn() { printf '%b%s%b\n' "$YELLOW" "⚠ $*" "$RESET" | tee -a "$LOG_FILE" >/dev/null; TESTS_WARNING=$((TESTS_WARNING + 1)); }
info() { printf '%b%s%b\n' "$CYAN" "ℹ $*" "$RESET" | tee -a "$LOG_FILE" >/dev/null; }

check_system_requirements() {
  info "Checking system requirements"
  command -v python3 >/dev/null 2>&1 && pass "python3 is available" || fail "python3 is missing"
  command -v pip3 >/dev/null 2>&1 && pass "pip3 is available" || fail "pip3 is missing"
  command -v curl >/dev/null 2>&1 && pass "curl is available" || warn "curl is recommended but missing"
  command -v jq >/dev/null 2>&1 && pass "jq is available" || warn "jq is optional and not installed"
  python3 - <<'PY'
import sys
if sys.version_info < (3, 9):
    raise SystemExit(1)
PY
  if [[ $? -eq 0 ]]; then pass "Python version is 3.9+"; else fail "Python version must be 3.9+"; fi
}

check_python_dependencies() {
  info "Checking Python dependencies"
  if [[ -x "${SCRIPT_DIR}/venv/bin/python" ]]; then
    PYTHON_BIN="${SCRIPT_DIR}/venv/bin/python"
  else
    PYTHON_BIN="$(command -v python3)"
  fi

  while IFS= read -r pkg; do
    [[ -z "$pkg" || "$pkg" =~ ^# ]] && continue
    pkg_name="${pkg%%>=*}"
    pkg_name="${pkg_name%%==*}"
    pkg_name="${pkg_name%%<=*}"
    if "$PYTHON_BIN" -c "import importlib; importlib.import_module('${pkg_name}')" >/dev/null 2>&1; then
      pass "$pkg_name importable"
    else
      fail "$pkg_name is not importable"
    fi
  done < "${SCRIPT_DIR}/requirements.txt"
}

check_configuration() {
  info "Checking configuration"
  if [[ -f "${SCRIPT_DIR}/.env" ]]; then
    if [[ "$(stat -c '%a' "${SCRIPT_DIR}/.env" 2>/dev/null || stat -f '%p' "${SCRIPT_DIR}/.env" 2>/dev/null)" =~ 600 ]]; then
      pass ".env exists and has restrictive permissions"
    else
      warn ".env permissions are not 600"
    fi
  else
    warn ".env file is missing; create one from .env.example"
  fi

  for var in ICERTIS_BASE_URL VEZA_URL VEZA_API_KEY; do
    if [[ -n "${!var:-}" ]]; then
      pass "$var is set"
    else
      warn "$var is missing"
    fi
  done
}

check_network_connectivity() {
  info "Checking network connectivity"
  target_host="${ICERTIS_BASE_URL:-https://example.com}"
  if command -v curl >/dev/null 2>&1; then
    if curl -fsS -o /dev/null --connect-timeout 5 "$target_host" >/dev/null 2>&1; then
      pass "Icertis host responds over HTTPS"
    else
      fail "Icertis host is not reachable"
    fi
  else
    warn "curl not installed; skipping outbound network check"
  fi
}

check_api_authentication() {
  info "Checking API authentication"
  if [[ -n "${ICERTIS_CLIENT_ID:-}" && -n "${ICERTIS_CLIENT_SECRET:-}" && -n "${ICERTIS_TOKEN_URL:-}" ]]; then
    pass "OAuth credentials appear configured"
  elif [[ -n "${ICERTIS_API_TOKEN:-}" || -n "${ICERTIS_API_KEY:-}" ]]; then
    pass "Static token or API key is configured"
  else
    warn "No Icertis auth values were detected"
  fi
}

check_veza_endpoint_access() {
  info "Checking Veza endpoint access"
  if [[ -n "${VEZA_URL:-}" && -n "${VEZA_API_KEY:-}" ]]; then
    if curl -fsS -H "Authorization: Bearer ${VEZA_API_KEY}" "${VEZA_URL}/api/v1/providers" >/dev/null 2>&1; then
      pass "Veza providers endpoint is reachable"
    else
      warn "Veza providers endpoint did not respond successfully"
    fi
  else
    warn "Veza endpoint check skipped because the key and URL are not configured"
  fi
}

check_deployment_structure() {
  info "Checking deployment structure"
  [[ -f "${SCRIPT_DIR}/icertis.py" ]] && pass "Main script exists" || fail "Main script is missing"
  [[ -d "${SCRIPT_DIR}/logs" ]] && pass "logs directory exists" || warn "logs directory missing; the script will create it"
  [[ -w "${SCRIPT_DIR}" ]] && pass "Workspace is writable" || fail "Workspace is not writable"
}

show_summary() {
  printf '\n\n' | tee -a "$LOG_FILE" >/dev/null
  printf '%b=== Preflight Summary ===%b\n' "$CYAN" "$RESET" | tee -a "$LOG_FILE" >/dev/null
  printf 'Passed: %s\n' "$TESTS_PASSED" | tee -a "$LOG_FILE" >/dev/null
  printf 'Failed: %s\n' "$TESTS_FAILED" | tee -a "$LOG_FILE" >/dev/null
  printf 'Warnings: %s\n' "$TESTS_WARNING" | tee -a "$LOG_FILE" >/dev/null
  printf 'Log: %s\n' "$LOG_FILE" | tee -a "$LOG_FILE" >/dev/null
}

main() {
  info "Starting Icertis preflight check: $LOG_FILE"
  check_system_requirements
  check_python_dependencies
  check_configuration
  check_network_connectivity
  check_api_authentication
  check_veza_endpoint_access
  check_deployment_structure
  show_summary

  if [[ "$TESTS_FAILED" -gt 0 ]]; then
    exit 1
  fi
  exit 0
}

main "$@"

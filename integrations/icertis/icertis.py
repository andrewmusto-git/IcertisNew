#!/usr/bin/env python3
"""Icertis SaaS connector for Veza OAA.

This integration queries Icertis over HTTPS and builds a Veza CustomApplication
payload for users, roles, app permissions, and resource access. It is built for
SaaS deployments and does not assume CSV import files.
"""

import argparse
import getpass
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import requests
from dotenv import load_dotenv
from oaaclient.client import OAAClient, OAAClientError
from oaaclient.templates import CustomApplication, OAAPermission

LOGGER = logging.getLogger(__name__)


def _setup_logging(log_level: str = "INFO") -> None:
    """Configure the logger for console output and a timestamped file."""
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)

    script_dir = Path(__file__).resolve().parent
    logs_dir = script_dir / "logs"
    logs_dir.mkdir(exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    file_handler = logging.FileHandler(logs_dir / f"icertis_{timestamp}.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)


def _first_nonempty(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, dict):
        return [value]
    return [value]


def _normalize_scalar(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _parse_oauth_request_params(raw: Any) -> dict[str, str]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return {str(key): str(value) for key, value in raw.items() if key is not None}
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        if text.startswith("{"):
            try:
                payload = json.loads(text)
                if isinstance(payload, dict):
                    return {str(key): str(value) for key, value in payload.items()}
            except json.JSONDecodeError:
                pass
        params: dict[str, str] = {}
        pieces = [p for p in text.replace("&", ";").split(";") if p]
        for piece in pieces:
            if "=" in piece:
                key, value = piece.split("=", 1)
                params[key.strip()] = value.strip()
        return params
    return {}


def _load_env_file(path: str | None) -> None:
    if path and os.path.exists(path):
        load_dotenv(path)


def _apply_json_config(cfg: dict[str, Any], path: str | None) -> dict[str, Any]:
    if not path or not os.path.exists(path):
        return cfg
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return cfg

    objects = data.get("objects") or []
    for item in objects:
        obj = item.get("object") if isinstance(item, dict) else {}
        attrs = obj.get("connectorAttributes") or {}
        if not isinstance(attrs, dict):
            continue
        if attrs.get("token_url"):
            cfg["token_url"] = str(attrs["token_url"]).strip()
        if attrs.get("grant_type"):
            cfg["grant_type"] = str(attrs["grant_type"]).strip() or "client_credentials"
        if attrs.get("oauth_request_parameters"):
            cfg["oauth_request_parameters"] = {
                **(cfg.get("oauth_request_parameters") or {}),
                **_parse_oauth_request_params(attrs.get("oauth_request_parameters")),
            }
        if attrs.get("genericWebServiceBaseUrl"):
            cfg["base_url"] = str(attrs["genericWebServiceBaseUrl"]).strip().rstrip("/")
        if attrs.get("client_id"):
            cfg["client_id"] = str(attrs["client_id"]).strip()

        connection_parameters = attrs.get("connectionParameters") or []
        for endpoint in connection_parameters:
            if not isinstance(endpoint, dict):
                continue
            context_url = endpoint.get("contextUrl")
            if not context_url:
                continue
            context_url = str(context_url).strip()
            if "http://" not in context_url and "https://" not in context_url:
                continue
            if "Users" in context_url and "users_url" not in cfg:
                cfg["users_url"] = context_url.rstrip("/")
            if "Groups" in context_url and "groups_url" not in cfg:
                cfg["groups_url"] = context_url.rstrip("/")
            if "organizationunits" in context_url.lower() and "org_units_url" not in cfg:
                cfg["org_units_url"] = context_url.rstrip("/")
        break
    return cfg


def _read_config(args: argparse.Namespace) -> dict[str, Any]:
    _load_env_file(args.env_file)
    cfg: dict[str, Any] = {
        "veza_url": args.veza_url or os.getenv("VEZA_URL"),
        "veza_api_key": args.veza_api_key or os.getenv("VEZA_API_KEY"),
        "base_url": (args.base_url or os.getenv("ICERTIS_BASE_URL") or "").rstrip("/"),
        "users_url": args.users_url or os.getenv("ICERTIS_USERS_URL") or "",
        "groups_url": args.groups_url or os.getenv("ICERTIS_GROUPS_URL") or "",
        "org_units_url": args.org_units_url or os.getenv("ICERTIS_ORG_UNITS_URL") or os.getenv("ICERTIS_ORGUNITS_URL") or "",
        "api_key": args.api_key or os.getenv("ICERTIS_API_KEY"),
        "api_token": args.api_token or os.getenv("ICERTIS_API_TOKEN"),
        "client_id": args.client_id or os.getenv("ICERTIS_CLIENT_ID"),
        "client_secret": args.client_secret or os.getenv("ICERTIS_CLIENT_SECRET"),
        "token_url": args.token_url or os.getenv("ICERTIS_TOKEN_URL"),
        "grant_type": (args.grant_type or os.getenv("ICERTIS_GRANT_TYPE") or "client_credentials").strip() or "client_credentials",
        "scope": args.scope or os.getenv("ICERTIS_SCOPE") or "",
        "users_path": args.users_path or os.getenv("ICERTIS_USERS_PATH", "/api/v1/users"),
        "roles_path": args.roles_path or os.getenv("ICERTIS_ROLES_PATH", "/api/v1/roles"),
        "permissions_path": args.permissions_path or os.getenv("ICERTIS_PERMISSIONS_PATH", "/api/v1/permissions"),
        "timeout": args.timeout,
        "oauth_request_parameters": _parse_oauth_request_params(os.getenv("ICERTIS_OAUTH_REQUEST_PARAMETERS")),
    }
    cfg = _apply_json_config(cfg, args.config_json)
    for item in args.oauth_request_param:
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        cfg["oauth_request_parameters"][key.strip()] = value.strip()
    if cfg.get("scope"):
        cfg["oauth_request_parameters"].setdefault("scope", cfg["scope"])
    if not cfg.get("grant_type"):
        cfg["grant_type"] = "client_credentials"
    return cfg


def _prompt_config_value(prompt: str, default: str = "", *, secret: bool = False) -> str:
    if secret:
        value = getpass.getpass(f"{prompt}{' [' + default + ']' if default else ''}: ")
    else:
        value = input(f"{prompt}{' [' + default + ']' if default else ''}: ")
    if value.strip() == "":
        return default
    return value.strip()


def _prompt_for_missing_values(cfg: dict[str, Any]) -> dict[str, Any]:
    print("\nIcertis OAuth profile")
    print("Provide the values required to connect to the SaaS application. Leave blank to keep the current value.")

    cfg["base_url"] = _prompt_config_value("Base URL", cfg.get("base_url") or "").rstrip("/")
    cfg["token_url"] = _prompt_config_value("Token URL", cfg.get("token_url") or "")
    cfg["grant_type"] = _prompt_config_value("Grant Type", cfg.get("grant_type") or "client_credentials")
    cfg["client_id"] = _prompt_config_value("Client ID", cfg.get("client_id") or "")
    cfg["client_secret"] = _prompt_config_value("Client Secret", cfg.get("client_secret") or "", secret=True)

    params = dict(cfg.get("oauth_request_parameters") or {})
    while True:
        key = _prompt_config_value("OAuth Request Parameter Key (blank to finish)", "")
        if not key:
            break
        value = _prompt_config_value(f"Value for {key}", "")
        params[key] = value
    cfg["oauth_request_parameters"] = params
    if cfg.get("grant_type"):
        cfg["grant_type"] = cfg["grant_type"].strip() or "client_credentials"
    return cfg


def _merge_query_payload(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("items", "value", "data", "results", "users", "roles", "permissions", "records"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                return [value]
        return [payload]
    return []


def _request_json(session: requests.Session, url: str, timeout: int, token: str | None, api_key: str | None, method: str = "GET") -> Any:
    headers = {"Accept": "application/json", "User-Agent": "veza-icertis-oaa/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif api_key:
        headers["X-API-Key"] = api_key
    response = session.request(method=method, url=url, headers=headers, timeout=timeout)
    if response.status_code in (401, 403):
        raise PermissionError(f"Icertis API auth failed for {url}: HTTP {response.status_code}")
    if response.status_code >= 400:
        raise RuntimeError(f"Icertis API request failed for {url}: HTTP {response.status_code} - {response.text[:300]}")
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(f"Non-JSON response from Icertis API for {url}: {response.text[:300]}") from exc


def _get_oauth_token(cfg: dict[str, Any]) -> str:
    if cfg.get("api_token"):
        return cfg["api_token"]
    if not cfg.get("client_id") or not cfg.get("client_secret") or not cfg.get("token_url"):
        raise RuntimeError("OAuth auth requires ICERTIS_CLIENT_ID, ICERTIS_CLIENT_SECRET, and ICERTIS_TOKEN_URL")

    grant_type = str(cfg.get("grant_type") or "client_credentials").strip() or "client_credentials"
    payload = {
        "grant_type": grant_type,
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
    }
    params = cfg.get("oauth_request_parameters") or {}
    for key, value in params.items():
        payload[key] = value
    response = requests.post(cfg["token_url"], data=payload, timeout=30)
    if response.status_code >= 400:
        raise RuntimeError(f"OAuth token fetch failed: HTTP {response.status_code} {response.text[:300]}")
    payload_json = response.json()
    token = payload_json.get("access_token") or payload_json.get("token")
    if not token:
        raise RuntimeError("OAuth token response did not include an access_token")
    return str(token)


def fetch_i_certis_data(cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    base_url = cfg.get("base_url")
    users_url = (cfg.get("users_url") or "").rstrip("/")
    groups_url = (cfg.get("groups_url") or "").rstrip("/")
    org_units_url = (cfg.get("org_units_url") or "").rstrip("/")

    if not base_url and not users_url and not groups_url:
        raise RuntimeError("ICERTIS_BASE_URL or exact Icertis endpoint URLs are required for the SaaS connector")

    token = _get_oauth_token(cfg) if cfg.get("client_id") or cfg.get("api_token") else (cfg.get("api_key") or "")
    session = requests.Session()

    if users_url:
        user_url = users_url
    else:
        user_url = (base_url + cfg["users_path"]).rstrip("/")

    if groups_url:
        role_url = groups_url
    else:
        role_url = (base_url + cfg["roles_path"]).rstrip("/")

    permission_url = (base_url + cfg["permissions_path"]).rstrip("/") if base_url else ""

    LOGGER.info("Fetching users from %s", user_url)
    users_payload = _request_json(session, user_url, cfg["timeout"], token if cfg.get("client_id") or cfg.get("api_token") else None, cfg.get("api_key"), method="GET")
    users = _merge_query_payload(users_payload)

    LOGGER.info("Fetching groups from %s", role_url)
    roles_payload = _request_json(session, role_url, cfg["timeout"], token if cfg.get("client_id") or cfg.get("api_token") else None, cfg.get("api_key"), method="GET")
    roles = _merge_query_payload(roles_payload)

    if permission_url:
        LOGGER.info("Fetching permissions from %s", permission_url)
        permissions_payload = _request_json(session, permission_url, cfg["timeout"], token if cfg.get("client_id") or cfg.get("api_token") else None, cfg.get("api_key"), method="GET")
        permissions = _merge_query_payload(permissions_payload)
    else:
        permissions = []

    if org_units_url:
        LOGGER.info("Org-unit URL configured: %s", org_units_url)

    return users, roles, permissions


def _resolve_user_key(record: dict[str, Any]) -> str:
    for key in ("id", "userId", "user_id", "uid", "uuid"):
        if record.get(key):
            return str(record[key])
    for key in ("email", "Email", "mail", "username", "userName"):
        if record.get(key):
            return str(record[key])
    return ""


def _resolve_role_key(record: dict[str, Any]) -> str:
    for key in ("id", "roleId", "role_id", "name", "displayName"):
        if record.get(key):
            return str(record[key])
    return ""


def _resolve_permission_name(record: dict[str, Any]) -> str:
    for key in ("name", "permissionName", "permission_name", "action", "operation"):
        if record.get(key):
            return str(record[key])
    return "read"


def _extract_group_names(record: dict[str, Any]) -> list[str]:
    for key in ("roles", "groups", "assignedRoles", "roleNames"):
        value = record.get(key)
        if value is None:
            continue
        values = []
        for item in _as_list(value):
            if isinstance(item, dict):
                name = _first_nonempty(item.get("name"), item.get("roleName"), item.get("displayName"))
                if name:
                    values.append(name)
            else:
                text = _normalize_scalar(item)
                if text:
                    values.append(text)
        if values:
            return values
    return []


def _extract_permission_names(record: dict[str, Any]) -> list[str]:
    values = []
    for key in ("permissions", "scopes", "actions", "access"):
        value = record.get(key)
        if value is None:
            continue
        for item in _as_list(value):
            if isinstance(item, dict):
                name = _resolve_permission_name(item)
                if name:
                    values.append(name)
            else:
                text = _normalize_scalar(item)
                if text:
                    values.append(text)
    if values:
        return values
    return ["read"]


def build_oaa_payload(users: Iterable[dict[str, Any]], roles: Iterable[dict[str, Any]], permissions: Iterable[dict[str, Any]], datasource_name: str, provider_name: str) -> CustomApplication:
    app = CustomApplication(name=datasource_name, application_type=provider_name)
    app.add_custom_permission("read", [OAAPermission.DataRead])
    app.add_custom_permission("write", [OAAPermission.DataRead, OAAPermission.DataWrite])
    app.add_custom_permission("admin", [OAAPermission.DataRead, OAAPermission.DataWrite, OAAPermission.MetadataRead, OAAPermission.MetadataWrite])

    for role_record in roles:
        role_id = _resolve_role_key(role_record)
        role_name = _first_nonempty(role_record.get("name"), role_record.get("displayName"), role_record.get("title"), role_id)
        if not role_id:
            continue
        app.add_local_group(name=role_name, unique_id=role_id)

    for permission_record in permissions:
        permission_id = _first_nonempty(permission_record.get("id"), permission_record.get("resourceId"), permission_record.get("resource_id"))
        resource_name = _first_nonempty(permission_record.get("resourceName"), permission_record.get("resource"), permission_record.get("name"), permission_id)
        if permission_id:
            app.add_resource(resource_key=permission_id, resource_type="Application Resource", name=resource_name, description=_first_nonempty(permission_record.get("description"), permission_record.get("resourceDescription")))

    seen_users: set[str] = set()
    for user_record in users:
        user_key = _resolve_user_key(user_record)
        if not user_key or user_key in seen_users:
            continue
        seen_users.add(user_key)

        user_name = _first_nonempty(user_record.get("displayName"), user_record.get("name"), user_record.get("fullName"), user_record.get("email"), user_record.get("username"), user_key)
        user_email = _first_nonempty(user_record.get("email"), user_record.get("mail"), user_record.get("login"))
        local_user = app.add_local_user(name=user_name, identities=[user_email] if user_email else [], unique_id=user_key)
        local_user.is_active = not str(_first_nonempty(user_record.get("active"), user_record.get("isActive"), "true")).lower() in {"false", "0", "no"}

        for role_name in _extract_group_names(user_record):
            try:
                local_user.add_group(role_name)
            except Exception as exc:
                LOGGER.warning("Failed to assign %s to group %s: %s", user_key, role_name, exc)

        for permission_record in permissions:
            if not permission_record:
                continue
            permission_scope = _resolve_permission_name(permission_record)
            permission_resource = _first_nonempty(permission_record.get("resourceId"), permission_record.get("resource_id"), permission_record.get("id"))
            if permission_record.get("userId") == user_key or permission_record.get("user_id") == user_key:
                if permission_resource:
                    local_user.add_permission(permission_scope, resources=[permission_resource], apply_to_sub_resources=False)
                else:
                    local_user.add_permission(permission_scope)

    for role_record in roles:
        role_id = _resolve_role_key(role_record)
        role_name = _first_nonempty(role_record.get("name"), role_record.get("displayName"), role_record.get("title"), role_id)
        if not role_id:
            continue
        for action in _extract_permission_names(role_record):
            app.add_local_group_permission(group_name=role_name, permission_name=action)

    return app


def _dump_payload(app: CustomApplication, save_json: bool) -> str | None:
    if not save_json:
        return None
    payload_path = Path(__file__).resolve().with_name("icertis_payload.json")
    payload = app.get_payload() if hasattr(app, "get_payload") else app
    with open(payload_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)
    return str(payload_path)


def _validate_required_settings(cfg: dict[str, Any], dry_run: bool) -> None:
    if dry_run:
        return
    missing = []
    if not cfg.get("veza_url"):
        missing.append("VEZA_URL")
    if not cfg.get("veza_api_key"):
        missing.append("VEZA_API_KEY")
    if missing:
        raise RuntimeError(f"Missing required Veza configuration: {', '.join(missing)}")


def push_to_veza(veza_url: str, veza_api_key: str, provider_name: str, datasource_name: str, app: CustomApplication, dry_run: bool = False) -> dict[str, Any]:
    if dry_run:
        LOGGER.info("[DRY RUN] Payload built successfully; skipping Veza push")
        return {"dry_run": True, "warnings": []}

    client = OAAClient(url=veza_url, token=veza_api_key)
    try:
        response = client.push_application(
            provider_name=provider_name,
            data_source_name=datasource_name,
            application_object=app,
            create_provider=True,
        )
        if isinstance(response, dict) and response.get("warnings"):
            for warning in response["warnings"]:
                LOGGER.warning("Veza warning: %s", warning)
        LOGGER.info("Successfully pushed to Veza")
        return response or {"dry_run": False, "warnings": []}
    except OAAClientError as exc:
        raise RuntimeError(f"Veza push failed: {exc}") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Icertis SaaS connector for Veza OAA")
    parser.add_argument("--env-file", default=".env", help="Path to .env file")
    parser.add_argument("--data-dir", default="./samples", help="Compatibility option only; SaaS connectors query the app, not CSV files")
    parser.add_argument("--dry-run", action="store_true", help="Build the payload without pushing to Veza")
    parser.add_argument("--save-json", action="store_true", help="Save the payload as icertis_payload.json")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Logging verbosity")
    parser.add_argument("--provider-name", default="Icertis", help="Veza provider name")
    parser.add_argument("--datasource-name", default="Icertis", help="Veza datasource name")
    parser.add_argument("--veza-url", help="Veza URL (or set VEZA_URL)")
    parser.add_argument("--veza-api-key", help="Veza API key (or set VEZA_API_KEY)")
    parser.add_argument("--base-url", help="Base URL for the Icertis SaaS application")
    parser.add_argument("--users-url", help="Exact Icertis users endpoint URL, such as https://tenant-api.icertis.com/api/Users")
    parser.add_argument("--groups-url", help="Exact Icertis groups endpoint URL, such as https://tenant-api.icertis.com/api/Groups")
    parser.add_argument("--org-units-url", help="Exact Icertis org-unit endpoint URL, such as https://tenant-business-api.icertis.com/api/v1/organizationunits")
    parser.add_argument("--api-key", help="API key used by the Icertis SaaS application")
    parser.add_argument("--api-token", help="Bearer token for the Icertis SaaS application")
    parser.add_argument("--client-id", help="OAuth client ID")
    parser.add_argument("--client-secret", help="OAuth client secret")
    parser.add_argument("--token-url", help="OAuth token endpoint")
    parser.add_argument("--scope", help="OAuth scope")
    parser.add_argument("--grant-type", default="client_credentials", help="OAuth grant type")
    parser.add_argument("--config-json", help="Optional JSON config export to read base URL, token URL, grant type, and OAuth request parameters")
    parser.add_argument("--oauth-request-param", action="append", default=[], help="Extra OAuth request param in key=value form; can be repeated")
    parser.add_argument("--prompt-config", action="store_true", help="Prompt interactively for the exact Icertis OAuth settings")
    parser.add_argument("--users-path", default="/api/v1/users", help="Endpoint path for user queries")
    parser.add_argument("--roles-path", default="/api/v1/roles", help="Endpoint path for role queries")
    parser.add_argument("--permissions-path", default="/api/v1/permissions", help="Endpoint path for permission queries")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    _setup_logging(args.log_level)
    LOGGER.info("Starting Icertis SaaS connector")

    cfg = _read_config(args)
    if args.prompt_config:
        cfg = _prompt_for_missing_values(cfg)

    if not cfg.get("base_url"):
        raise RuntimeError("ICERTIS_BASE_URL is required to query the SaaS application")
    if not cfg.get("token_url") and (cfg.get("client_id") or cfg.get("api_token")):
        raise RuntimeError("ICERTIS_TOKEN_URL is required when using OAuth client credentials")
    if not cfg.get("grant_type"):
        cfg["grant_type"] = "client_credentials"

    _validate_required_settings(cfg, args.dry_run)

    try:
        users, roles, permissions = fetch_i_certis_data(cfg)
    except Exception as exc:
        LOGGER.error("Failed to query Icertis SaaS application: %s", exc)
        raise

    LOGGER.info("Retrieved %d users, %d roles, %d permissions", len(users), len(roles), len(permissions))
    app = build_oaa_payload(users, roles, permissions, args.datasource_name, args.provider_name)
    json_path = _dump_payload(app, args.save_json)
    if json_path:
        LOGGER.info("Payload saved to %s", json_path)

    if args.dry_run:
        LOGGER.info("Dry-run successful; payload built without sending to Veza")
        return 0

    veza_url = (cfg.get("veza_url") or args.veza_url or "").rstrip("/")
    veza_api_key = cfg.get("veza_api_key") or args.veza_api_key or ""
    push_to_veza(veza_url, veza_api_key, args.provider_name, args.datasource_name, app, dry_run=False)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        LOGGER.warning("Interrupted by user")
        raise SystemExit(130)
    except Exception as exc:
        logging.exception("Icertis connector failed: %s", exc)
        if not str(exc).startswith("Missing required Veza configuration"):
            print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)

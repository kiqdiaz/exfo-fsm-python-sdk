"""
EXFO FMS - Obtención y renovación de Access Token (Keycloak / Direct Access Grants)

Flujo:
  1. Intenta cargar el token cacheado y verificar que no haya expirado.
  2. Si el access_token venció pero el refresh_token sigue vigente, renueva con refresh.
  3. Si ambos vencieron (o no hay caché), hace password grant con soporte MFA/TOTP.
"""

import json
import os
import time
from pathlib import Path

import requests
import urllib3
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

TOKEN_FILE = Path(__file__).parent.parent / "fms_token.json"

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

KEYCLOAK_TOKEN_URL = os.environ["FMS_AUTH_URL"]
CLIENT_ID = os.environ["FMS_CLIENT_ID"]
USERNAME  = os.environ["FMS_USERNAME"]
PASSWORD  = os.environ["FMS_PASSWORD"]

HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "*/*",
}

_MFA_HINTS = {"mfa_required", "account is not fully set up", "invalid_grant"}


# ── Caché ──────────────────────────────────────────────────────────────────────

def _load_cached() -> dict | None:
    if not TOKEN_FILE.exists():
        return None
    try:
        return json.loads(TOKEN_FILE.read_text())
    except Exception:
        return None


def _access_valid(data: dict) -> bool:
    if not data or "access_token" not in data:
        return False
    age = time.time() - TOKEN_FILE.stat().st_mtime
    return age < data.get("expires_in", 900) - 30


def _refresh_valid(data: dict) -> bool:
    if not data or "refresh_token" not in data:
        return False
    age = time.time() - TOKEN_FILE.stat().st_mtime
    return age < data.get("refresh_expires_in", 0) - 60


def _save(data: dict) -> dict:
    print("[OK]  Token obtenido.")
    TOKEN_FILE.write_text(json.dumps(data, indent=2))
    print(f"[+]   Token guardado en: '{TOKEN_FILE}'")
    return data


# ── HTTP helper ────────────────────────────────────────────────────────────────

def _post(payload: dict) -> tuple[int, dict | None]:
    try:
        r = requests.post(
            KEYCLOAK_TOKEN_URL,
            data=payload,
            headers=HEADERS,
            verify=False,
            timeout=10,
        )
    except requests.exceptions.ConnectionError as e:
        print(f"[FAIL] Sin conexión: {e}")
        return -1, None
    print(f"       Status: {r.status_code}")
    try:
        return r.status_code, r.json()
    except Exception:
        print(f"[FAIL] Respuesta no JSON: {r.text[:300]}")
        return r.status_code, None


# ── Refresh ────────────────────────────────────────────────────────────────────

def _refresh(refresh_token: str) -> dict | None:
    print("[*] Renovando token con refresh_token...")
    status, data = _post({
        "client_id":     CLIENT_ID,
        "grant_type":    "refresh_token",
        "refresh_token": refresh_token,
    })
    if status == 200 and data and "access_token" in data:
        return _save(data)
    print(f"[!] No se pudo renovar: {data}")
    return None


# ── Password grant con MFA ──────────────────────────────────────────────────────

def _is_mfa(data: dict) -> bool:
    desc = data.get("error_description", "").lower()
    return (
        data.get("error") in _MFA_HINTS
        or any(k in desc for k in ("otp", "totp", "authenticator", "second factor", "mfa"))
    )


def _password_grant() -> dict | None:
    base = {
        "client_id":  CLIENT_ID,
        "grant_type": "password",
        "username":   USERNAME,
        "password":   PASSWORD,
    }

    print(f"[*] POST {KEYCLOAK_TOKEN_URL}")
    status, data = _post(base)
    if data is None:
        return None
    if status == 200 and "access_token" in data:
        return _save(data)

    if status not in (400, 401) or not _is_mfa(data):
        print(f"[FAIL] {data}")
        return None

    # TOTP ronda 1
    print(f"[*] MFA requerido — ({data.get('error_description', data.get('error'))})")
    otp1 = input("    Código TOTP #1 (6 dígitos): ").strip()
    if not otp1:
        return None

    status, data = _post({**base, "totp": otp1})
    if data is None:
        return None
    if status == 200 and "access_token" in data:
        return _save(data)

    if status not in (400, 401) or not _is_mfa(data):
        print(f"[FAIL] {data}")
        return None

    # TOTP ronda 2
    print(f"[*] Segundo factor — ({data.get('error_description', data.get('error'))})")
    otp2 = input("    Código TOTP #2 (6 dígitos): ").strip()
    if not otp2:
        return None

    for extra in [{"totp": f"{otp1} {otp2}"}, {"totp": otp2}, {"totp": otp1, "otp": otp2}]:
        status, data = _post({**base, **extra})
        if data is None:
            return None
        if status == 200 and "access_token" in data:
            return _save(data)
        if status not in (400, 401):
            break

    print(f"[FAIL] {data}")
    return None


# ── Punto de entrada público ────────────────────────────────────────────────────

def get_token() -> dict | None:
    cached = _load_cached()
    if _access_valid(cached):
        return cached
    if _refresh_valid(cached):
        result = _refresh(cached["refresh_token"])
        if result:
            return result
    return _password_grant()


if __name__ == "__main__":
    get_token()

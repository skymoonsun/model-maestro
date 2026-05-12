"""Google OAuth 2.0 and v1internal API integration for Antigravity proxy.

Handles:
- OAuth 2.0 flow (auth URL, callback, token exchange, refresh)
- loadCodeAssist API for project ID resolution
- Version management (remote latest vs known stable)
- Machine ID and session generation
"""

import logging
import uuid
import platform
import subprocess
import httpx
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================

# Google OAuth 2.0 endpoints
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

# Scopes required for Google v1internal API access
GOOGLE_OAUTH_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
]

# Google v1internal API endpoints (fallback order: Sandbox -> Daily -> Prod)
V1_INTERNAL_BASE_URLS = [
    "https://daily-cloudcode-pa.sandbox.googleapis.com/v1internal",
    "https://daily-cloudcode-pa.googleapis.com/v1internal",
    "https://cloudcode-pa.googleapis.com/v1internal",
]

# Remote version endpoint
VERSION_URL = "https://antigravity-auto-updater-974169037036.us-central1.run.app"

# Known stable version (fallback if remote fails)
KNOWN_STABLE_VERSION = "1.23.2"

# Chrome/Electron version for User-Agent spoofing
CHROME_VERSION = "132.0.6834.160"
ELECTRON_VERSION = "39.2.3"


# =============================================================================
# OAuth Flow
# =============================================================================

class GoogleOAuthManager:
    """Manages Google OAuth 2.0 flow for Antigravity proxy."""

    def __init__(self, client_id: str, client_secret: str, redirect_uri: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    def get_auth_url(self, state: Optional[str] = None) -> str:
        """Generate Google OAuth consent screen URL."""
        scopes = " ".join(GOOGLE_OAUTH_SCOPES)
        url = (
            f"{GOOGLE_AUTH_URL}"
            f"?client_id={self.client_id}"
            f"&redirect_uri={self.redirect_uri}"
            f"&response_type=code"
            f"&scope={scopes}"
            f"&access_type=offline"
            f"&prompt=consent"
        )
        if state:
            url += f"&state={state}"
        return url

    async def exchange_code(self, code: str) -> Dict[str, Any]:
        """Exchange authorization code for access_token and refresh_token."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": self.redirect_uri,
                },
            )

            if response.status_code != 200:
                logger.error(f"Token exchange failed: {response.status_code} - {response.text}")
                raise Exception(f"Token exchange failed: {response.status_code}")

            data = response.json()
            return {
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token"),
                "expires_in": data.get("expires_in", 3600),
                "token_type": data.get("token_type", "Bearer"),
                "scope": data.get("scope"),
                "obtained_at": datetime.now(timezone.utc).isoformat(),
            }

    async def refresh_access_token(self, refresh_token: str) -> Dict[str, Any]:
        """Refresh expired access token."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )

            if response.status_code != 200:
                logger.error(f"Token refresh failed: {response.status_code} - {response.text}")
                raise Exception(f"Token refresh failed: {response.status_code}")

            data = response.json()
            return {
                "access_token": data["access_token"],
                "expires_in": data.get("expires_in", 3600),
                "token_type": data.get("token_type", "Bearer"),
                "scope": data.get("scope"),
                "obtained_at": datetime.now(timezone.utc).isoformat(),
            }

    async def get_user_info(self, access_token: str) -> Dict[str, Any]:
        """Get user info (email, name, picture) from Google."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )

            if response.status_code != 200:
                logger.error(f"User info fetch failed: {response.status_code}")
                raise Exception(f"User info fetch failed: {response.status_code}")

            return response.json()


# =============================================================================
# Project ID Resolver
# =============================================================================

async def load_code_assist(access_token: str) -> str:
    """
    Call Google's loadCodeAssist API to get cloudaicompanionProject ID.

    Returns project_id like 'swift-flow-zr34p'.
    Raises exception if account has no eligibility.
    """
    url = "https://daily-cloudcode-pa.sandbox.googleapis.com/v1internal:loadCodeAssist"
    body = {"metadata": {"ideType": "ANTIGRAVITY"}}

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": get_user_agent(),
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(url, headers=headers, json=body)

        if response.status_code != 200:
            logger.error(f"loadCodeAssist failed: {response.status_code} - {response.text}")
            raise Exception(f"loadCodeAssist failed: {response.status_code}")

        data = response.json()
        project_id = data.get("cloudaicompanionProject")
        if not project_id:
            raise Exception("Account has no eligibility for cloudaicompanionProject")

        return project_id


# =============================================================================
# Version Management
# =============================================================================

async def get_remote_version() -> Optional[str]:
    """Fetch latest Antigravity version from remote update server."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(VERSION_URL)
            if response.status_code == 200:
                text = response.text
                # Parse "Stable Version: 1.23.2" from response
                if "Stable Version:" in text:
                    version = text.split("Stable Version:")[1].strip().split()[0]
                    return version
    except Exception as e:
        logger.warning(f"Failed to fetch remote version: {e}")

    return None


def get_current_version() -> str:
    """
    Get the best version to report to Google.
    Returns max(remote_latest, KNOWN_STABLE_VERSION).
    """
    # In a real implementation, we might cache the remote version.
    # For now, return the known stable version.
    return KNOWN_STABLE_VERSION


# =============================================================================
# Machine & Session Identity
# =============================================================================

def get_machine_uuid() -> str:
    """
    Get persistent machine UUID.
    - macOS: IOPlatformUUID from ioreg
    - Linux: /etc/machine-id or dbus-machine-id
    - Windows: registry (simplified, returns random UUID)
    """
    system = platform.system()

    if system == "Darwin":
        try:
            result = subprocess.run(
                ["ioreg", "-l", "|", "grep", "IOPlatformUUID"],
                capture_output=True,
                text=True,
                shell=True,
                timeout=5,
            )
            output = result.stdout
            if "IOPlatformUUID" in output:
                # Parse: "IOPlatformUUID" = "550e8400-e29b-41d4-a716-446655440000"
                uuid_str = output.split('"')[3] if '"' in output else None
                if uuid_str:
                    return uuid_str
        except Exception as e:
            logger.warning(f"Failed to get macOS machine UUID: {e}")

    elif system == "Linux":
        try:
            with open("/etc/machine-id", "r") as f:
                machine_id = f.read().strip()
                if machine_id:
                    return machine_id
        except Exception:
            pass

        try:
            result = subprocess.run(
                ["dbus-machine-id"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            machine_id = result.stdout.strip()
            if machine_id:
                return machine_id
        except Exception:
            pass

    # Fallback: generate a persistent UUID from MAC address
    node = uuid.getnode()
    if node:
        return str(uuid.UUID(int=node))

    # Last resort: random UUID (not persistent across restarts)
    return str(uuid.uuid4())


# Cache machine UUID
_MACHINE_UUID: Optional[str] = None


def get_cached_machine_uuid() -> str:
    """Get cached machine UUID (computed once per process)."""
    global _MACHINE_UUID
    if _MACHINE_UUID is None:
        _MACHINE_UUID = get_machine_uuid()
    return _MACHINE_UUID


def get_session_id() -> str:
    """Generate a new session ID (per app launch)."""
    return str(uuid.uuid4())


# =============================================================================
# User-Agent & Headers
# =============================================================================

def get_user_agent() -> str:
    """
    Build User-Agent string matching Antigravity Manager's format.
    Example: "Antigravity/1.23.2 (Macintosh; Intel Mac OS X 10_15_7) Chrome/132.0.6834.160 Electron/39.2.3"
    """
    system = platform.system()
    if system == "Darwin":
        platform_str = "Macintosh; Intel Mac OS X 10_15_7"
    elif system == "Windows":
        platform_str = "Windows NT 10.0; Win64; x64"
    else:
        platform_str = "X11; Linux x86_64"

    version = get_current_version()
    return f"Antigravity/{version} ({platform_str}) Chrome/{CHROME_VERSION} Electron/{ELECTRON_VERSION}"


def build_v1internal_headers(
    access_token: str,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """
    Build headers for Google v1internal API requests.
    Includes all required identity headers to pass Google's validation.
    """
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
        "User-Agent": get_user_agent(),
        "x-client-name": "antigravity",
        "x-client-version": get_current_version(),
        "x-machine-id": get_cached_machine_uuid(),
        "x-vscode-sessionid": get_session_id(),
    }

    if extra_headers:
        headers.update(extra_headers)

    return headers


# =============================================================================
# Token Validation
# =============================================================================

def is_token_expired(oauth_tokens: Dict[str, Any]) -> bool:
    """Check if access token is expired or about to expire (within 5 minutes)."""
    obtained_at_str = oauth_tokens.get("obtained_at")
    expires_in = oauth_tokens.get("expires_in", 3600)

    if not obtained_at_str:
        return True

    try:
        obtained_at = datetime.fromisoformat(obtained_at_str.replace("Z", "+00:00"))
        expiry = obtained_at + timedelta(seconds=expires_in)
        return datetime.now(timezone.utc) > (expiry - timedelta(minutes=5))
    except (ValueError, TypeError):
        return True


async def ensure_fresh_token(oauth_tokens: Dict[str, Any], client_id: str = "", client_secret: str = "") -> str:
    """
    Ensure access token is valid. Refresh if needed.
    Returns the valid access_token string.
    """
    if not is_token_expired(oauth_tokens):
        return oauth_tokens["access_token"]

    refresh_token = oauth_tokens.get("refresh_token")
    if not refresh_token:
        raise Exception("Token expired and no refresh_token available")

    # Use global manager credentials if not provided explicitly
    if not client_id or not client_secret:
        global_mgr = get_google_oauth_manager()
        if global_mgr:
            client_id = global_mgr.client_id
            client_secret = global_mgr.client_secret

    if not client_id or not client_secret:
        raise Exception("Google OAuth client_id/client_secret not configured for token refresh")

    manager = GoogleOAuthManager(client_id, client_secret, "")
    new_tokens = await manager.refresh_access_token(refresh_token)

    # Update the token dict in-place (caller should persist to DB)
    oauth_tokens["access_token"] = new_tokens["access_token"]
    oauth_tokens["expires_in"] = new_tokens["expires_in"]
    oauth_tokens["obtained_at"] = new_tokens["obtained_at"]
    if new_tokens.get("scope"):
        oauth_tokens["scope"] = new_tokens["scope"]

    return new_tokens["access_token"]


# =============================================================================
# Global OAuth Manager (configured from settings)
# =============================================================================

_google_oauth_manager: Optional[GoogleOAuthManager] = None


def init_google_oauth(client_id: str, client_secret: str, redirect_uri: str):
    """Initialize the global Google OAuth manager."""
    global _google_oauth_manager
    _google_oauth_manager = GoogleOAuthManager(client_id, client_secret, redirect_uri)


def get_google_oauth_manager() -> Optional[GoogleOAuthManager]:
    """Get the global Google OAuth manager."""
    return _google_oauth_manager

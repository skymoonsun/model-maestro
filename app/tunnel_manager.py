"""Tunnel manager for exposing local API via cloudflared or ngrok"""

import asyncio
import logging
import re
import shutil
from typing import Optional, Dict, Any
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)


@dataclass
class TunnelStatus:
    running: bool = False
    provider: str = ""
    public_url: str = ""
    pid: Optional[int] = None
    error: str = ""
    tunnel_id: str = ""
    tunnel_token: str = ""


class CloudflareTunnelClient:
    """Cloudflare API client for tunnel management"""

    def __init__(self, api_token: str, account_id: str):
        self.api_token = api_token
        self.account_id = account_id
        self.base_url = "https://api.cloudflare.com/client/v4"

    async def _request(self, method: str, path: str, json_data: Optional[dict] = None) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.request(
                method,
                f"{self.base_url}{path}",
                headers={
                    "Authorization": f"Bearer {self.api_token}",
                    "Content-Type": "application/json",
                },
                json=json_data,
                timeout=30.0,
            )
            data = resp.json()
            if not data.get("success"):
                errors = data.get("errors", [])
                error_msg = errors[0].get("message", "Unknown error") if errors else "Unknown error"
                raise RuntimeError(f"Cloudflare API error: {error_msg}")
            return data.get("result", {})

    async def create_tunnel(self, name: str) -> dict:
        return await self._request(
            "POST",
            f"/accounts/{self.account_id}/cfd_tunnel",
            {"name": name, "config_src": "cloudflare"},
        )

    async def configure_tunnel(self, tunnel_id: str, hostname: str, service: str) -> dict:
        return await self._request(
            "PUT",
            f"/accounts/{self.account_id}/cfd_tunnel/{tunnel_id}/configurations",
            {
                "config": {
                    "ingress": [
                        {"hostname": hostname, "service": service, "originRequest": {}},
                        {"service": "http_status:404"},
                    ]
                }
            },
        )

    async def create_dns_record(self, zone_id: str, hostname: str, tunnel_id: str) -> dict:
        return await self._request(
            "POST",
            f"/zones/{zone_id}/dns_records",
            {
                "type": "CNAME",
                "proxied": True,
                "name": hostname,
                "content": f"{tunnel_id}.cfargotunnel.com",
            },
        )

    async def get_tunnel(self, tunnel_id: str) -> dict:
        return await self._request(
            "GET",
            f"/accounts/{self.account_id}/cfd_tunnel/{tunnel_id}",
        )


class TunnelManager:
    """Manages cloudflared or ngrok tunnel subprocess"""

    def __init__(self):
        self._process: Optional[asyncio.subprocess.Process] = None
        self._status = TunnelStatus()
        self._monitor_task: Optional[asyncio.Task] = None

    async def get_status(self) -> Dict[str, Any]:
        if self._process is not None and self._process.returncode is not None:
            self._status.running = False
            self._status.pid = None
        return {
            "running": self._status.running,
            "provider": self._status.provider,
            "public_url": self._status.public_url,
            "pid": self._status.pid,
            "error": self._status.error,
            "tunnel_id": self._status.tunnel_id,
            "tunnel_token": self._status.tunnel_token,
        }

    async def start(self, provider: str, local_port: int = 8000, api_token: str = "",
                    account_id: str = "", zone_id: str = "", hostname: str = "") -> Dict[str, Any]:
        if self._status.running:
            return await self.get_status()

        self._status.error = ""

        if provider == "cloudflare":
            return await self._start_cloudflare(local_port, api_token, account_id, zone_id, hostname)
        elif provider == "ngrok":
            return await self._start_ngrok(local_port, api_token)
        else:
            self._status.error = f"Unknown provider: {provider}"
            return await self.get_status()

    async def _start_cloudflare(self, local_port: int, api_token: str,
                                 account_id: str, zone_id: str, hostname: str) -> Dict[str, Any]:
        if not api_token or not account_id:
            self._status.error = "Cloudflare API token and account ID are required"
            return await self.get_status()

        if not shutil.which("cloudflared"):
            self._status.error = (
                "cloudflared not found in PATH. Install it first:\n"
                "  Ubuntu/Debian: wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb && dpkg -i cloudflared-linux-amd64.deb\n"
                "  Or run: docker exec -it <container> bash  && install cloudflared inside the container"
            )
            return await self.get_status()

        try:
            cf = CloudflareTunnelClient(api_token, account_id)

            # Create tunnel
            tunnel = await cf.create_tunnel("model-maestro-tunnel")
            tunnel_id = tunnel["id"]
            tunnel_token = tunnel.get("token", "")
            credentials = tunnel.get("credentials_file", {})
            if credentials and not tunnel_token:
                # Build token from credentials
                import base64
                import json as _json
                token_payload = {
                    "a": credentials.get("AccountTag", ""),
                    "t": credentials.get("TunnelID", ""),
                    "s": credentials.get("TunnelSecret", ""),
                }
                tunnel_token = base64.b64encode(
                    _json.dumps(token_payload).encode()
                ).decode()

            self._status.tunnel_id = tunnel_id
            self._status.tunnel_token = tunnel_token

            # Configure ingress
            if hostname:
                service = f"http://localhost:{local_port}"
                await cf.configure_tunnel(tunnel_id, hostname, service)

                # Create DNS record if zone_id provided
                if zone_id:
                    await cf.create_dns_record(zone_id, hostname, tunnel_id)

                self._status.public_url = f"https://{hostname}"
            else:
                self._status.public_url = ""

            # Run cloudflared with token
            self._status = TunnelStatus(
                running=True, provider="cloudflare",
                public_url=self._status.public_url,
                tunnel_id=tunnel_id, tunnel_token=tunnel_token,
            )

            self._process = await asyncio.create_subprocess_exec(
                "cloudflared", "tunnel", "run", "--token", tunnel_token,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            self._status.pid = self._process.pid

            self._monitor_task = asyncio.create_task(self._monitor_cloudflare(cf, tunnel_id))
            return await self.get_status()

        except Exception as e:
            logger.error("Cloudflare tunnel start error: %s", e)
            self._status.error = str(e)
            return await self.get_status()

    async def _monitor_cloudflare(self, cf: CloudflareTunnelClient, tunnel_id: str):
        if self._process is None or self._process.stdout is None:
            return
        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace")
                logger.info("cloudflared: %s", text.strip())
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("cloudflared monitor error: %s", e)
        finally:
            self._status.running = False
            self._status.pid = None

    async def _start_ngrok(self, local_port: int, api_token: str) -> Dict[str, Any]:
        try:
            from pyngrok import ngrok
        except ImportError:
            self._status.error = "pyngrok not installed. Run: pip install pyngrok"
            return await self.get_status()

        try:
            if api_token:
                ngrok.set_auth_token(api_token)

            self._status = TunnelStatus(running=True, provider="ngrok")
            public_url = ngrok.connect(local_port, "http")
            self._status.public_url = public_url
            return await self.get_status()
        except Exception as e:
            logger.error("ngrok start error: %s", e)
            self._status.error = str(e)
            return await self.get_status()

    async def stop(self) -> Dict[str, Any]:
        if self._status.provider == "ngrok":
            try:
                from pyngrok import ngrok
                ngrok.kill()
            except Exception:
                pass
        elif self._process is not None and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()

        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        self._status = TunnelStatus()
        return await self.get_status()


tunnel_manager = TunnelManager()

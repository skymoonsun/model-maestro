"""Tunnel manager for exposing local API via cloudflared or ngrok"""

import asyncio
import logging
import re
import shutil
from typing import Optional, Dict, Any
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


@dataclass
class TunnelStatus:
    running: bool = False
    provider: str = ""
    public_url: str = ""
    pid: Optional[int] = None
    error: str = ""


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
        }

    async def start(self, provider: str, local_port: int = 8000, api_token: str = "") -> Dict[str, Any]:
        if self._status.running:
            return await self.get_status()

        self._status.error = ""

        if provider == "cloudflare":
            return await self._start_cloudflare(local_port)
        elif provider == "ngrok":
            return await self._start_ngrok(local_port, api_token)
        else:
            self._status.error = f"Unknown provider: {provider}"
            return await self.get_status()

    async def _start_cloudflare(self, local_port: int) -> Dict[str, Any]:
        if not shutil.which("cloudflared"):
            self._status.error = "cloudflared not found in PATH"
            return await self.get_status()

        self._status = TunnelStatus(running=True, provider="cloudflare")

        self._process = await asyncio.create_subprocess_exec(
            "cloudflared", "tunnel", "--url", f"http://localhost:{local_port}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._status.pid = self._process.pid

        self._monitor_task = asyncio.create_task(self._monitor_cloudflare())
        return await self.get_status()

    async def _monitor_cloudflare(self):
        if self._process is None or self._process.stdout is None:
            return

        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace")
                logger.info("cloudflared: %s", text.strip())

                match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", text)
                if match:
                    self._status.public_url = match.group(0)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("cloudflared monitor error: %s", e)
        finally:
            self._status.running = False
            self._status.pid = None

    async def _start_ngrok(self, local_port: int, api_token: str) -> Dict[str, Any]:
        if not shutil.which("ngrok"):
            self._status.error = "ngrok not found in PATH"
            return await self.get_status()

        self._status = TunnelStatus(running=True, provider="ngrok")

        cmd = ["ngrok", "http", str(local_port)]
        if api_token:
            cmd.extend(["--authtoken", api_token])

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._status.pid = self._process.pid

        self._monitor_task = asyncio.create_task(self._monitor_ngrok())
        return await self.get_status()

    async def _monitor_ngrok(self):
        """Poll ngrok local API for tunnel URL"""
        await asyncio.sleep(2)

        try:
            async with httpx.AsyncClient() as client:
                for _ in range(30):
                    try:
                        resp = await client.get("http://127.0.0.1:4040/api/tunnels")
                        if resp.status_code == 200:
                            data = resp.json()
                            tunnels = data.get("tunnels", [])
                            if tunnels:
                                self._status.public_url = tunnels[0].get("public_url", "")
                                break
                    except Exception:
                        pass
                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("ngrok monitor error: %s", e)

    async def stop(self) -> Dict[str, Any]:
        if self._process is not None and self._process.returncode is None:
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

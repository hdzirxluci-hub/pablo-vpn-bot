from __future__ import annotations

import json
import secrets
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx

from .config import Settings, get_settings


class PanelConnectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProvisionedAccount:
    external_id: str
    username: str
    subscription_url: str


@dataclass(frozen=True)
class ConnectionReport:
    panel_type: str
    configured: bool
    online: bool
    authenticated: bool
    latency_ms: int | None
    deep_test: bool
    create_ok: bool | None = None
    read_ok: bool | None = None
    delete_ok: bool | None = None
    detail: str = ""
    mock: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class BasePanelClient(ABC):
    panel_type: str

    @abstractmethod
    async def test_connection(self, deep: bool = False) -> ConnectionReport:
        raise NotImplementedError

    @abstractmethod
    async def create_user(
        self,
        *,
        username: str,
        volume_mb: int,
        expires_at: datetime,
        inbound_id: str | None = None,
    ) -> ProvisionedAccount:
        raise NotImplementedError

    @abstractmethod
    async def delete_user(self, external_id: str, inbound_id: str | None = None) -> None:
        raise NotImplementedError


class MockPanelClient(BasePanelClient):
    def __init__(self, panel_type: str, latency_ms: int) -> None:
        self.panel_type = panel_type
        self.latency_ms = latency_ms

    async def test_connection(self, deep: bool = False) -> ConnectionReport:
        return ConnectionReport(
            panel_type=self.panel_type,
            configured=True,
            online=True,
            authenticated=True,
            latency_ms=self.latency_ms,
            deep_test=deep,
            create_ok=True if deep else None,
            read_ok=True if deep else None,
            delete_ok=True if deep else None,
            detail="Mock adapter — no real panel request was made",
            mock=True,
        )

    async def create_user(
        self,
        *,
        username: str,
        volume_mb: int,
        expires_at: datetime,
        inbound_id: str | None = None,
    ) -> ProvisionedAccount:
        token = secrets.token_urlsafe(12)
        return ProvisionedAccount(
            external_id=f"demo-{self.panel_type.lower()}-{token[:8]}",
            username=username,
            subscription_url=f"https://demo.pablovpn.local/sub/{token}",
        )

    async def delete_user(self, external_id: str, inbound_id: str | None = None) -> None:
        return None


class TokenPanelClient(BasePanelClient):
    """Common API contract used by PasarGuard and Marzban."""

    health_path = "/api/system"

    def __init__(
        self,
        *,
        panel_type: str,
        base_url: str | None,
        username: str | None,
        password: str | None,
        timeout: float,
        verify_tls: bool,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.panel_type = panel_type
        self.base_url = (base_url or "").rstrip("/")
        self.username = username or ""
        self.password = password or ""
        self.timeout = timeout
        self.verify_tls = verify_tls
        self.transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.username and self.password)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.timeout,
            verify=self.verify_tls,
            follow_redirects=True,
            transport=self.transport,
        )

    async def authenticate(self, client: httpx.AsyncClient) -> str:
        response = await client.post(
            f"{self.base_url}/api/admin/token",
            data={"username": self.username, "password": self.password},
        )
        if response.status_code != 200:
            raise PanelConnectionError(f"authentication rejected with HTTP {response.status_code}")
        token = response.json().get("access_token")
        if not token:
            raise PanelConnectionError("authentication response contained no access token")
        return str(token)

    async def request(
        self,
        client: httpx.AsyncClient,
        token: str,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {token}"
        response = await client.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)
        if response.status_code >= 400:
            raise PanelConnectionError(f"{method} {path} failed with HTTP {response.status_code}")
        return response

    async def test_connection(self, deep: bool = False) -> ConnectionReport:
        if not self.configured:
            return ConnectionReport(
                panel_type=self.panel_type,
                configured=False,
                online=False,
                authenticated=False,
                latency_ms=None,
                deep_test=deep,
                detail="Panel URL or credentials are not configured",
            )
        started = time.perf_counter()
        try:
            async with self.client() as client:
                token = await self.authenticate(client)
                await self.request(client, token, "GET", self.health_path)
            latency = round((time.perf_counter() - started) * 1000)
            if not deep:
                return ConnectionReport(
                    panel_type=self.panel_type,
                    configured=True,
                    online=True,
                    authenticated=True,
                    latency_ms=latency,
                    deep_test=False,
                    detail="Authentication and read endpoint succeeded",
                )
            return await self._deep_test(latency)
        except (httpx.HTTPError, ValueError, PanelConnectionError) as exc:
            return ConnectionReport(
                panel_type=self.panel_type,
                configured=True,
                online=False,
                authenticated=False,
                latency_ms=round((time.perf_counter() - started) * 1000),
                deep_test=deep,
                detail=str(exc)[:240],
            )

    async def _deep_test(self, latency: int) -> ConnectionReport:
        username = f"pablo_check_{secrets.token_hex(4)}"[:32]
        account: ProvisionedAccount | None = None
        create_ok = read_ok = delete_ok = False
        detail = ""
        try:
            account = await self.create_user(
                username=username,
                volume_mb=1,
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            )
            create_ok = True
            read_ok = await self.user_exists(username)
            if not read_ok:
                raise PanelConnectionError("created user could not be read back")
        except (httpx.HTTPError, ValueError, PanelConnectionError) as exc:
            detail = str(exc)[:240]
        finally:
            if account is not None:
                try:
                    await self.delete_user(account.external_id)
                    delete_ok = True
                except (httpx.HTTPError, ValueError, PanelConnectionError) as exc:
                    detail = (detail + "; cleanup: " + str(exc))[:240]
        return ConnectionReport(
            panel_type=self.panel_type,
            configured=True,
            online=create_ok and read_ok and delete_ok,
            authenticated=True,
            latency_ms=latency,
            deep_test=True,
            create_ok=create_ok,
            read_ok=read_ok,
            delete_ok=delete_ok,
            detail=detail or "Created, read and deleted a temporary 1MB user",
        )

    @abstractmethod
    async def user_exists(self, username: str) -> bool:
        raise NotImplementedError


class PasarGuardClient(TokenPanelClient):
    health_path = "/api/groups"

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(
            panel_type="PasarGuard",
            base_url=settings.pasarguard_url,
            username=settings.pasarguard_username,
            password=settings.pasarguard_password.get_secret_value() if settings.pasarguard_password else None,
            timeout=settings.panel_timeout_seconds,
            verify_tls=settings.panel_verify_tls,
            transport=transport,
        )
        self.group_id = settings.pasarguard_group_id

    async def resolve_group(self, client: httpx.AsyncClient, token: str) -> int:
        if self.group_id is not None:
            return self.group_id
        response = await self.request(client, token, "GET", "/api/groups")
        payload = response.json()
        groups = payload.get("groups", payload) if isinstance(payload, dict) else payload
        if not isinstance(groups, list) or not groups:
            raise PanelConnectionError("PasarGuard has no group available for users")
        return int(groups[0]["id"])

    async def create_user(
        self,
        *,
        username: str,
        volume_mb: int,
        expires_at: datetime,
        inbound_id: str | None = None,
    ) -> ProvisionedAccount:
        if not self.configured:
            raise PanelConnectionError("PasarGuard is not configured")
        async with self.client() as client:
            token = await self.authenticate(client)
            group_id = await self.resolve_group(client, token)
            response = await self.request(
                client,
                token,
                "POST",
                "/api/user",
                json={
                    "username": username,
                    "data_limit": volume_mb * 1024 * 1024,
                    "expire": int(expires_at.timestamp()),
                    "status": "active",
                    "group_ids": [group_id],
                    "note": "PABLO VPN Telegram bot",
                },
            )
            payload = response.json()
            subscription_url = str(payload.get("subscription_url") or "")
            if not subscription_url:
                read_back = await self.request(client, token, "GET", f"/api/user/{username}")
                subscription_url = str(read_back.json().get("subscription_url") or "")
            if not subscription_url:
                try:
                    await self.request(client, token, "DELETE", f"/api/user/{username}")
                except PanelConnectionError:
                    pass
                raise PanelConnectionError("PasarGuard created the user without a subscription URL")
            if subscription_url.startswith("/"):
                subscription_url = self.base_url + subscription_url
            return ProvisionedAccount(username, username, subscription_url)

    async def user_exists(self, username: str) -> bool:
        async with self.client() as client:
            token = await self.authenticate(client)
            response = await client.get(
                f"{self.base_url}/api/user/{username}",
                headers={"Authorization": f"Bearer {token}"},
            )
            return response.status_code == 200 and response.json().get("username") == username

    async def delete_user(self, external_id: str, inbound_id: str | None = None) -> None:
        async with self.client() as client:
            token = await self.authenticate(client)
            await self.request(client, token, "DELETE", f"/api/user/{external_id}")


class MarzbanClient(TokenPanelClient):
    health_path = "/api/system"

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(
            panel_type="Marzban",
            base_url=settings.marzban_url,
            username=settings.marzban_username,
            password=settings.marzban_password.get_secret_value() if settings.marzban_password else None,
            timeout=settings.panel_timeout_seconds,
            verify_tls=settings.panel_verify_tls,
            transport=transport,
        )
        try:
            self.proxies = json.loads(settings.marzban_proxies_json)
            self.inbounds = json.loads(settings.marzban_inbounds_json)
        except json.JSONDecodeError as exc:
            raise PanelConnectionError("Marzban proxy/inbound JSON setting is invalid") from exc

    async def create_user(
        self,
        *,
        username: str,
        volume_mb: int,
        expires_at: datetime,
        inbound_id: str | None = None,
    ) -> ProvisionedAccount:
        if not self.configured:
            raise PanelConnectionError("Marzban is not configured")
        async with self.client() as client:
            token = await self.authenticate(client)
            response = await self.request(
                client,
                token,
                "POST",
                "/api/user",
                json={
                    "username": username[:32],
                    "proxies": self.proxies,
                    "inbounds": self.inbounds,
                    "expire": int(expires_at.timestamp()),
                    "data_limit": volume_mb * 1024 * 1024,
                    "data_limit_reset_strategy": "no_reset",
                    "status": "active",
                    "note": "PABLO VPN Telegram bot",
                },
            )
            payload = response.json()
            subscription_url = str(payload.get("subscription_url") or "")
            if not subscription_url:
                read_back = await self.request(client, token, "GET", f"/api/user/{username}")
                subscription_url = str(read_back.json().get("subscription_url") or "")
            if not subscription_url:
                try:
                    await self.request(client, token, "DELETE", f"/api/user/{username}")
                except PanelConnectionError:
                    pass
                raise PanelConnectionError("Marzban created the user without a subscription URL")
            if subscription_url.startswith("/"):
                subscription_url = self.base_url + subscription_url
            return ProvisionedAccount(username, username, subscription_url)

    async def user_exists(self, username: str) -> bool:
        async with self.client() as client:
            token = await self.authenticate(client)
            response = await client.get(
                f"{self.base_url}/api/user/{username}",
                headers={"Authorization": f"Bearer {token}"},
            )
            return response.status_code == 200 and response.json().get("username") == username

    async def delete_user(self, external_id: str, inbound_id: str | None = None) -> None:
        async with self.client() as client:
            token = await self.authenticate(client)
            await self.request(client, token, "DELETE", f"/api/user/{external_id}")


class XUIClient(BasePanelClient):
    panel_type = "3x-ui"

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = (settings.xui_url or "").rstrip("/")
        self.api_token = settings.xui_api_token.get_secret_value() if settings.xui_api_token else ""
        self.username = settings.xui_username or ""
        self.password = settings.xui_password.get_secret_value() if settings.xui_password else ""
        self.inbound_id = settings.xui_inbound_id
        self.subscription_base_url = (settings.xui_subscription_base_url or "").rstrip("/")
        self.timeout = settings.panel_timeout_seconds
        self.verify_tls = settings.panel_verify_tls
        self.transport = transport

    @property
    def configured(self) -> bool:
        has_auth = bool(self.api_token or (self.username and self.password))
        return bool(self.base_url and has_auth)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.timeout,
            verify=self.verify_tls,
            follow_redirects=True,
            transport=self.transport,
        )

    async def authenticate(self, client: httpx.AsyncClient) -> dict[str, str]:
        if self.api_token:
            return {"Authorization": f"Bearer {self.api_token}"}
        csrf = ""
        csrf_response = await client.get(f"{self.base_url}/csrf-token")
        if csrf_response.status_code == 200:
            try:
                csrf = str(csrf_response.json().get("obj") or "")
            except ValueError:
                csrf = ""
        headers = {"X-CSRF-Token": csrf} if csrf else {}
        response = await client.post(
            f"{self.base_url}/login",
            data={"username": self.username, "password": self.password},
            headers=headers,
        )
        try:
            success = response.status_code == 200 and response.json().get("success") is True
        except ValueError:
            success = False
        if not success:
            raise PanelConnectionError(f"3x-ui authentication rejected with HTTP {response.status_code}")
        return headers

    async def panel_request(
        self,
        client: httpx.AsyncClient,
        auth_headers: dict[str, str],
        method: str,
        path: str,
        *,
        allow_not_found: bool = False,
        **kwargs: Any,
    ) -> httpx.Response:
        headers = dict(auth_headers)
        headers.update(kwargs.pop("headers", {}))
        response = await client.request(method, f"{self.base_url}/panel/api{path}", headers=headers, **kwargs)
        if response.status_code >= 400 and not (allow_not_found and response.status_code == 404):
            raise PanelConnectionError(f"3x-ui {method} {path} failed with HTTP {response.status_code}")
        return response

    async def api_request(
        self,
        client: httpx.AsyncClient,
        auth_headers: dict[str, str],
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        return await self.panel_request(client, auth_headers, method, f"/inbounds{path}", **kwargs)

    async def test_connection(self, deep: bool = False) -> ConnectionReport:
        if not self.configured:
            return ConnectionReport(
                panel_type=self.panel_type,
                configured=False,
                online=False,
                authenticated=False,
                latency_ms=None,
                deep_test=deep,
                detail="3x-ui URL or authentication is not configured",
            )
        started = time.perf_counter()
        try:
            async with self.client() as client:
                headers = await self.authenticate(client)
                response = await self.api_request(client, headers, "GET", "/list")
                payload = response.json()
                if payload.get("success") is not True or not isinstance(payload.get("obj"), list):
                    raise PanelConnectionError("3x-ui inbound list returned an unexpected response")
                if self.inbound_id is not None and not any(
                    int(item.get("id", -1)) == self.inbound_id for item in payload["obj"]
                ):
                    raise PanelConnectionError(f"3x-ui inbound {self.inbound_id} was not found")
            latency = round((time.perf_counter() - started) * 1000)
            if not deep:
                return ConnectionReport(
                    panel_type=self.panel_type,
                    configured=True,
                    online=True,
                    authenticated=True,
                    latency_ms=latency,
                    deep_test=False,
                    detail="Authentication and inbound list succeeded",
                )
            return await self._deep_test(latency)
        except (httpx.HTTPError, ValueError, PanelConnectionError) as exc:
            return ConnectionReport(
                panel_type=self.panel_type,
                configured=True,
                online=False,
                authenticated=False,
                latency_ms=round((time.perf_counter() - started) * 1000),
                deep_test=deep,
                detail=str(exc)[:240],
            )

    async def _deep_test(self, latency: int) -> ConnectionReport:
        username = f"pablo_check_{secrets.token_hex(4)}"[:32]
        account: ProvisionedAccount | None = None
        create_ok = read_ok = delete_ok = False
        detail = ""
        try:
            account = await self.create_user(
                username=username,
                volume_mb=1,
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            )
            create_ok = True
            read_ok = await self.user_exists(username)
            if not read_ok:
                raise PanelConnectionError("created 3x-ui client could not be read back")
        except (httpx.HTTPError, ValueError, PanelConnectionError) as exc:
            detail = str(exc)[:240]
        finally:
            if account is not None:
                try:
                    await self.delete_user(account.external_id, str(self.inbound_id or ""))
                    delete_ok = True
                except (httpx.HTTPError, ValueError, PanelConnectionError) as exc:
                    detail = (detail + "; cleanup: " + str(exc))[:240]
        return ConnectionReport(
            panel_type=self.panel_type,
            configured=True,
            online=create_ok and read_ok and delete_ok,
            authenticated=True,
            latency_ms=latency,
            deep_test=True,
            create_ok=create_ok,
            read_ok=read_ok,
            delete_ok=delete_ok,
            detail=detail or "Created, read and deleted a temporary 1MB client",
        )

    async def create_user(
        self,
        *,
        username: str,
        volume_mb: int,
        expires_at: datetime,
        inbound_id: str | None = None,
    ) -> ProvisionedAccount:
        target_inbound = int(inbound_id or self.inbound_id or 0)
        if not self.configured or target_inbound <= 0:
            raise PanelConnectionError("3x-ui URL, authentication or inbound ID is not configured")
        if not self.subscription_base_url:
            raise PanelConnectionError("3x-ui subscription base URL is not configured")
        client_uuid = str(uuid.uuid4())
        sub_id = secrets.token_urlsafe(12).replace("_", "").replace("-", "")[:16]
        client_data = {
            "id": client_uuid,
            "flow": "",
            "email": username,
            "limitIp": 0,
            "limitHwid": 0,
            "totalGB": volume_mb * 1024 * 1024,
            "expiryTime": int(expires_at.timestamp() * 1000),
            "enable": True,
            "tgId": 0,
            "subId": sub_id,
            "reset": 0,
        }
        external_id = f"email:{username}"
        async with self.client() as client:
            headers = await self.authenticate(client)
            # Current 3x-ui exposes clients as first-class resources. A 404 means an
            # older panel, in which case the legacy inbound-scoped endpoint is used.
            response = await self.panel_request(
                client,
                headers,
                "POST",
                "/clients/add",
                allow_not_found=True,
                json={"client": client_data, "inboundIds": [target_inbound]},
            )
            if response.status_code == 404:
                response = await self.api_request(
                    client,
                    headers,
                    "POST",
                    "/addClient",
                    json={"id": target_inbound, "settings": json.dumps({"clients": [client_data]})},
                )
                external_id = f"uuid:{client_uuid}"
            try:
                payload = response.json()
            except ValueError as exc:
                raise PanelConnectionError("3x-ui add client returned a non-JSON response") from exc
            if payload.get("success") is not True:
                raise PanelConnectionError(f"3x-ui rejected add client: {payload.get('msg', 'unknown error')}")
        return ProvisionedAccount(external_id, username, f"{self.subscription_base_url}/{sub_id}")

    async def user_exists(self, username: str) -> bool:
        if self.inbound_id is None:
            return False
        async with self.client() as client:
            headers = await self.authenticate(client)
            response = await self.panel_request(
                client,
                headers,
                "GET",
                f"/clients/get/{quote(username, safe='')}",
                allow_not_found=True,
            )
            if response.status_code != 404:
                payload = response.json()
                item = payload.get("obj") or {}
                return payload.get("success") is True and item.get("email") == username

            response = await self.api_request(client, headers, "GET", f"/get/{self.inbound_id}")
            payload = response.json().get("obj") or {}
            settings_payload = payload.get("settings") or {}
            if isinstance(settings_payload, str):
                settings_payload = json.loads(settings_payload)
            clients = settings_payload.get("clients") or []
            return any(item.get("email") == username for item in clients)

    async def delete_user(self, external_id: str, inbound_id: str | None = None) -> None:
        target_inbound = int(inbound_id or self.inbound_id or 0)
        if target_inbound <= 0:
            raise PanelConnectionError("3x-ui inbound ID is not configured")
        async with self.client() as client:
            headers = await self.authenticate(client)
            if external_id.startswith("email:"):
                email = external_id.removeprefix("email:")
                response = await self.panel_request(
                    client,
                    headers,
                    "POST",
                    f"/clients/del/{quote(email, safe='')}?keepTraffic=0",
                )
            else:
                client_uuid = external_id.removeprefix("uuid:")
                response = await self.api_request(
                    client,
                    headers,
                    "POST",
                    f"/{target_inbound}/delClient/{client_uuid}",
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise PanelConnectionError("3x-ui delete client returned a non-JSON response") from exc
            if payload.get("success") is not True:
                raise PanelConnectionError(f"3x-ui rejected delete client: {payload.get('msg', 'unknown error')}")


def build_panel_client(
    panel_type: str,
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> BasePanelClient:
    settings = settings or get_settings()
    if settings.panel_mode == "mock":
        latency = {"PasarGuard": 24, "3x-ui": 31, "Marzban": 39}.get(panel_type, 50)
        return MockPanelClient(panel_type, latency)
    if panel_type == "PasarGuard":
        return PasarGuardClient(settings, transport)
    if panel_type == "3x-ui":
        return XUIClient(settings, transport)
    if panel_type == "Marzban":
        return MarzbanClient(settings, transport)
    raise PanelConnectionError(f"Unsupported panel type: {panel_type}")

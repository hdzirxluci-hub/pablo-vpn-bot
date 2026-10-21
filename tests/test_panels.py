import json

import httpx
import pytest

from app.config import Settings
from app.panel_clients import MarzbanClient, PasarGuardClient, XUIClient, build_panel_client


@pytest.mark.asyncio
async def test_all_mock_adapters_are_explicitly_marked_mock() -> None:
    for name in ("PasarGuard", "3x-ui", "Marzban"):
        report = await build_panel_client(name, Settings(panel_mode="mock")).test_connection(deep=True)
        assert report.online is True
        assert report.panel_type == name
        assert report.mock is True
        assert report.create_ok is True
        assert report.read_ok is True
        assert report.delete_ok is True


@pytest.mark.asyncio
async def test_pasarguard_deep_test_creates_reads_and_deletes() -> None:
    users: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/admin/token":
            return httpx.Response(200, json={"access_token": "token"})
        if path == "/api/groups" and request.method == "GET":
            return httpx.Response(200, json=[{"id": 7, "name": "default"}])
        if path == "/api/user" and request.method == "POST":
            payload = json.loads(request.content)
            users[payload["username"]] = payload
            return httpx.Response(200, json={"subscription_url": f"/sub/{payload['username']}"})
        if path.startswith("/api/user/") and request.method == "GET":
            username = path.rsplit("/", 1)[1]
            return httpx.Response(200, json={"username": username}) if username in users else httpx.Response(404)
        if path.startswith("/api/user/") and request.method == "DELETE":
            users.pop(path.rsplit("/", 1)[1], None)
            return httpx.Response(204)
        return httpx.Response(404)

    settings = Settings(
        panel_mode="real",
        pasarguard_url="https://pasar.test",
        pasarguard_username="admin",
        pasarguard_password="secret",
        pasarguard_group_id=7,
    )
    report = await PasarGuardClient(settings, httpx.MockTransport(handler)).test_connection(deep=True)
    assert report.online is True
    assert (report.create_ok, report.read_ok, report.delete_ok) == (True, True, True)
    assert report.mock is False
    assert users == {}


@pytest.mark.asyncio
async def test_marzban_deep_test_creates_reads_and_deletes() -> None:
    users: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/admin/token":
            return httpx.Response(200, json={"access_token": "token"})
        if path == "/api/system":
            return httpx.Response(200, json={"version": "test"})
        if path == "/api/user" and request.method == "POST":
            payload = json.loads(request.content)
            users[payload["username"]] = payload
            return httpx.Response(200, json={"subscription_url": f"https://marzban.test/sub/{payload['username']}"})
        if path.startswith("/api/user/") and request.method == "GET":
            username = path.rsplit("/", 1)[1]
            return httpx.Response(200, json={"username": username}) if username in users else httpx.Response(404)
        if path.startswith("/api/user/") and request.method == "DELETE":
            users.pop(path.rsplit("/", 1)[1], None)
            return httpx.Response(204)
        return httpx.Response(404)

    settings = Settings(
        panel_mode="real",
        marzban_url="https://marzban.test",
        marzban_username="admin",
        marzban_password="secret",
    )
    report = await MarzbanClient(settings, httpx.MockTransport(handler)).test_connection(deep=True)
    assert report.online is True
    assert (report.create_ok, report.read_ok, report.delete_ok) == (True, True, True)
    assert users == {}


@pytest.mark.asyncio
async def test_xui_deep_test_uses_dedicated_client_endpoints_and_cleans_up() -> None:
    clients: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/panel/api/inbounds/list":
            return httpx.Response(200, json={"success": True, "obj": [{"id": 42}]})
        if path == "/panel/api/clients/add" and request.method == "POST":
            payload = json.loads(request.content)
            data = payload["client"]
            assert payload["inboundIds"] == [42]
            clients[data["email"]] = data
            return httpx.Response(200, json={"success": True, "msg": "Client added"})
        if path.startswith("/panel/api/clients/get/") and request.method == "GET":
            email = path.rsplit("/", 1)[1]
            if email not in clients:
                return httpx.Response(200, json={"success": False, "obj": None})
            return httpx.Response(200, json={"success": True, "obj": clients[email]})
        if path.startswith("/panel/api/clients/del/") and request.method == "POST":
            clients.pop(path.rsplit("/", 1)[1], None)
            assert request.url.params["keepTraffic"] == "0"
            return httpx.Response(200, json={"success": True, "msg": "Client deleted"})
        return httpx.Response(404)

    settings = Settings(
        panel_mode="real",
        xui_url="https://xui.test",
        xui_api_token="api-token",
        xui_inbound_id=42,
        xui_subscription_base_url="https://sub.test/sub",
    )
    report = await XUIClient(settings, httpx.MockTransport(handler)).test_connection(deep=True)
    assert report.online is True
    assert (report.create_ok, report.read_ok, report.delete_ok) == (True, True, True)
    assert clients == {}


@pytest.mark.asyncio
async def test_xui_legacy_client_endpoints_are_supported_as_a_fallback() -> None:
    clients: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/panel/api/inbounds/list":
            return httpx.Response(200, json={"success": True, "obj": [{"id": 42}]})
        if path.startswith("/panel/api/clients/"):
            return httpx.Response(404)
        if path == "/panel/api/inbounds/addClient" and request.method == "POST":
            payload = json.loads(request.content)
            data = json.loads(payload["settings"])["clients"][0]
            clients[data["id"]] = data
            return httpx.Response(200, json={"success": True})
        if path == "/panel/api/inbounds/get/42":
            return httpx.Response(
                200,
                json={"success": True, "obj": {"settings": json.dumps({"clients": list(clients.values())})}},
            )
        if path.startswith("/panel/api/inbounds/42/delClient/"):
            clients.pop(path.rsplit("/", 1)[1], None)
            return httpx.Response(200, json={"success": True})
        return httpx.Response(404)

    settings = Settings(
        panel_mode="real",
        xui_url="https://legacy-xui.test",
        xui_api_token="api-token",
        xui_inbound_id=42,
        xui_subscription_base_url="https://sub.test/sub",
    )
    report = await XUIClient(settings, httpx.MockTransport(handler)).test_connection(deep=True)
    assert report.online is True
    assert (report.create_ok, report.read_ok, report.delete_ok) == (True, True, True)
    assert clients == {}

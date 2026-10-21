from fastapi.testclient import TestClient

from app.main import app

CUSTOMER_HEADERS = {"X-Demo-User-ID": "842103"}
OWNER_HEADERS = {"X-Demo-User-ID": "900001", "X-Demo-Owner": "1"}


def test_complete_order_to_service_flow() -> None:
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["bot_username"] == "PablloVPN3_bot"
        assert health.json()["bot_configured"] is False

        bootstrap = client.get("/api/bootstrap", headers=CUSTOMER_HEADERS)
        assert bootstrap.status_code == 200
        body = bootstrap.json()
        assert len(body["plans"]) == 5
        assert body["orders"] == []
        assert body["services"] == []

        created = client.post("/api/orders", headers=CUSTOMER_HEADERS, json={"plan_id": "smart40"})
        assert created.status_code == 201
        order = created.json()
        assert order["status"] == "awaiting_receipt"
        order_id = order["id"]

        duplicate = client.post("/api/orders", headers=CUSTOMER_HEADERS, json={"plan_id": "smart40"})
        assert duplicate.status_code == 201
        assert duplicate.json()["id"] == order_id

        receipt = client.post(f"/api/demo/orders/{order_id}/receipt", headers=CUSTOMER_HEADERS)
        assert receipt.status_code == 200
        assert receipt.json()["status"] == "pending"

        owner_orders = client.get("/api/owner/orders", headers=OWNER_HEADERS)
        assert owner_orders.status_code == 200
        assert owner_orders.json()[0]["id"] == order_id

        forbidden = client.post(f"/api/owner/orders/{order_id}/approve", headers=CUSTOMER_HEADERS)
        assert forbidden.status_code == 403

        approved = client.post(f"/api/owner/orders/{order_id}/approve", headers=OWNER_HEADERS)
        assert approved.status_code == 200
        service = approved.json()
        assert service["panel"] == "PasarGuard"
        assert service["total"] == 40
        assert service["days"] in {30, 31}
        assert service["sub"].startswith("https://demo.pablovpn.local/sub/")

        approved_again = client.post(f"/api/owner/orders/{order_id}/approve", headers=OWNER_HEADERS)
        assert approved_again.status_code == 200
        assert approved_again.json()["id"] == service["id"]

        final_state = client.get("/api/bootstrap", headers=CUSTOMER_HEADERS)
        assert final_state.status_code == 200
        final_body = final_state.json()
        assert final_body["orders"][0]["status"] == "approved"
        assert len(final_body["services"]) == 1


def test_unknown_plan_is_rejected() -> None:
    with TestClient(app) as client:
        response = client.post("/api/orders", headers=CUSTOMER_HEADERS, json={"plan_id": "does-not-exist"})
        assert response.status_code == 404


def test_free_trial_limit_owner_configuration_and_global_reset() -> None:
    first_user = {"X-Demo-User-ID": "843001"}
    second_user = {"X-Demo-User-ID": "843002"}
    with TestClient(app) as client:
        initial = client.get("/api/bootstrap", headers=first_user)
        assert initial.status_code == 200
        assert initial.json()["trial"]["eligible"] is True
        assert initial.json()["trial"]["volumeMb"] == 100
        assert initial.json()["trial"]["durationHours"] == 24

        claimed = client.post("/api/trial", headers=first_user)
        assert claimed.status_code == 201
        service = claimed.json()["service"]
        assert service["isTrial"] is True
        assert service["totalMb"] == 100
        assert service["hoursRemaining"] in {23, 24}

        blocked = client.post("/api/trial", headers=first_user)
        assert blocked.status_code == 409

        configured = client.post(
            "/api/owner/trial/configure",
            headers=OWNER_HEADERS,
            json={
                "volume_mb": 250,
                "duration_hours": 12,
                "max_claims": 1,
                "window_days": 0,
                "panel_id": "pg-ams-1",
            },
        )
        assert configured.status_code == 200
        assert configured.json()["volumeMb"] == 250

        new_claim = client.post("/api/trial", headers=second_user)
        assert new_claim.status_code == 201
        assert new_claim.json()["service"]["totalMb"] == 250

        reset = client.post("/api/owner/trial/reset", headers=OWNER_HEADERS)
        assert reset.status_code == 200
        assert reset.json()["generation"] >= 2

        after_reset = client.post("/api/trial", headers=first_user)
        assert after_reset.status_code == 201
        assert after_reset.json()["service"]["totalMb"] == 250


def test_owner_can_run_deep_panel_audit_without_claiming_real_connection() -> None:
    with TestClient(app) as client:
        response = client.get("/api/owner/panels/health?deep=true", headers=OWNER_HEADERS)
        assert response.status_code == 200
        reports = response.json()
        assert {item["panel_type"] for item in reports} == {"PasarGuard", "3x-ui", "Marzban"}
        assert all(item["mock"] is True for item in reports)
        assert all(item["create_ok"] and item["read_ok"] and item["delete_ok"] for item in reports)

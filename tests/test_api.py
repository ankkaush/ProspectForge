from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app, docs_urls_for_environment

client = TestClient(app)
API_KEY = get_settings().prospectforge_api_key
AUTH_HEADERS = {"Authorization": f"Bearer {API_KEY}"}


def test_docs_are_disabled_in_production():
    assert docs_urls_for_environment("production") == {
        "docs_url": None, "redoc_url": None, "openapi_url": None,
    }


def test_docs_are_enabled_outside_production():
    assert docs_urls_for_environment("development")["docs_url"] == "/docs"
    assert docs_urls_for_environment("staging")["docs_url"] == "/docs"


def test_health_check_confirms_db_connectivity():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_run_without_api_key_is_rejected():
    response = client.post("/runs", json={"icp_config_id": "saas-fictional-v1"})
    assert response.status_code == 401


def test_create_run_with_wrong_api_key_is_rejected():
    response = client.post(
        "/runs",
        json={"icp_config_id": "saas-fictional-v1"},
        headers={"Authorization": "Bearer totally-wrong-key"},
    )
    assert response.status_code == 401


def test_create_run_with_valid_api_key_succeeds():
    response = client.post(
        "/runs",
        json={"icp_config_id": "saas-fictional-v1"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["icp_config_id"] == "saas-fictional-v1"
    assert body["status"] == "completed"
    assert body["summary"]["stages_run"] == [
        "discovery",
        "prefilter",
        "enrichment",
        "fit_evaluation",
        "research",
        "people_discovery",
        "contact_enrichment",
        "dedup",
        "qualification",
        "prioritization",
    ]
    assert body["summary"]["discovery"]["persisted_new"] == 2
    assert body["summary"]["prefilter"]["advanced"] == 2
    assert body["summary"]["enrichment"]["enriched"] == 2
    assert body["summary"]["fit_evaluation"]["tier_1"] == 2
    assert body["summary"]["research"]["researched"] == 2
    assert body["summary"]["people_discovery"]["contacts_found"] == 2
    assert body["summary"]["contact_enrichment"]["enriched"] == 2
    assert body["summary"]["dedup"]["accounts_merged"] == 0
    assert body["summary"]["qualification"]["accounts_qualified"] == 2
    assert body["summary"]["prioritization"]["prospects_scored"] == 2


def test_get_run_reachable_after_creation():
    created = client.post(
        "/runs",
        json={"icp_config_id": "saas-fictional-v1"},
        headers=AUTH_HEADERS,
    ).json()

    fetched = client.get(f"/runs/{created['id']}", headers=AUTH_HEADERS)
    assert fetched.status_code == 200
    assert fetched.json()["id"] == created["id"]


def test_get_unknown_run_is_404():
    response = client.get(
        "/runs/00000000-0000-0000-0000-000000000000", headers=AUTH_HEADERS
    )
    assert response.status_code == 404


def test_get_run_without_api_key_is_rejected():
    response = client.get("/runs/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 401

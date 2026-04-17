import pytest
from app import app, db


@pytest.fixture
def client():
    app.config["TESTING"]               = True
    app.config["SQLALCHEMY_DATABASE_URI"] = \
        "postgresql://dairywatch_user:dairywatch_pass@localhost:5432/dairywatch_test"

    with app.test_client() as client:
        with app.app_context():
            db.create_all()
            yield client
            db.drop_all()


# ── Auth tests ────────────────────────────────

def test_register(client):
    res = client.post("/api/auth/register", json={
        "name":     "Test Farmer",
        "email":    "test@farm.com",
        "password": "password123"
    })
    assert res.status_code == 201
    assert "token" in res.get_json()


def test_login(client):
    client.post("/api/auth/register", json={
        "name":     "Test Farmer",
        "email":    "test@farm.com",
        "password": "password123"
    })
    res = client.post("/api/auth/login", json={
        "email":    "test@farm.com",
        "password": "password123"
    })
    assert res.status_code == 200
    assert "token" in res.get_json()


def test_login_wrong_password(client):
    client.post("/api/auth/register", json={
        "name":     "Test Farmer",
        "email":    "test@farm.com",
        "password": "password123"
    })
    res = client.post("/api/auth/login", json={
        "email":    "test@farm.com",
        "password": "wrongpassword"
    })
    assert res.status_code == 401


# ── Cow tests ─────────────────────────────────

def test_add_cow(client):
    reg = client.post("/api/auth/register", json={
        "name":     "Test Farmer",
        "email":    "test@farm.com",
        "password": "password123"
    })
    token = reg.get_json()["token"]

    res = client.post("/api/cows", json={
        "tag_number": "KE-001",
        "name":       "Daisy",
        "breed":      "Friesian"
    }, headers={"Authorization": f"Bearer {token}"})

    assert res.status_code == 201
    assert res.get_json()["tag_number"] == "KE-001"


def test_get_cows(client):
    reg = client.post("/api/auth/register", json={
        "name":     "Test Farmer",
        "email":    "test@farm.com",
        "password": "password123"
    })
    token = reg.get_json()["token"]

    res = client.get("/api/cows",
        headers={"Authorization": f"Bearer {token}"})

    assert res.status_code == 200
    assert isinstance(res.get_json(), list)


def test_duplicate_tag(client):
    reg = client.post("/api/auth/register", json={
        "name":     "Test Farmer",
        "email":    "test@farm.com",
        "password": "password123"
    })
    token = reg.get_json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    client.post("/api/cows",
        json={"tag_number": "KE-001"}, headers=headers)

    res = client.post("/api/cows",
        json={"tag_number": "KE-001"}, headers=headers)

    assert res.status_code == 409


def test_get_herd_stats(client):
    reg = client.post("/api/auth/register", json={
        "name":     "Test Farmer",
        "email":    "test@farm.com",
        "password": "password123"
    })
    token = reg.get_json()["token"]

    res = client.get("/api/cows/stats",
        headers={"Authorization": f"Bearer {token}"})

    data = res.get_json()
    assert res.status_code == 200
    assert "total"   in data
    assert "healthy" in data
    assert "sick"    in data
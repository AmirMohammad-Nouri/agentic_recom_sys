"""server.mode: local (everyone signs in, one administrator account), demo (public showcase
with an admin/user view and a read-only Config page) and open (development and tests)."""
import json
import os
import subprocess
import sys

from conftest import FIXTURE, OfflineLLM
from fastapi.testclient import TestClient

from foodrec.api import create_app
from foodrec.appconfig import AppConfig, defaults
from foodrec.config import load_settings
from foodrec.study import check_password, verify_password

VISITOR = "visitor-0123456789ab"


def _client(tmp_path, mode, admin_pw="admin-pass-1", participants=None):
    cfg = defaults()
    cfg["server"]["mode"] = mode
    cfg["access"]["admin"] = {"username": "admin", "password": admin_pw}
    cfg["study"]["participants"] = participants or [{"username": "p01", "password": "maple-river-42", "role": "baseline"}]
    path = tmp_path / "c.json"
    conf = AppConfig({}, path).save(cfg, path)          # the password is hashed on save
    s = load_settings(config=conf, data_dir=tmp_path / "d", qdrant="memory", catalogue_path=FIXTURE)
    return TestClient(create_app(s, llm=OfflineLLM())), path


def _login(c, user, pw):
    r = c.post("/api/login", json={"username": user, "password": pw})
    return {"Authorization": f"Bearer {r.json()['token']}"} if r.status_code == 200 else r


# ---- the administrator account ----------------------------------------------------------------------
def test_admin_password_is_hashed_on_save(tmp_path):
    c, path = _client(tmp_path, "local")
    adm = json.loads(path.read_text())["access"]["admin"]
    assert adm["password"] is None and verify_password("admin-pass-1", adm["password_hash"])
    assert "admin-pass-1" not in path.read_text()
    cfg = AppConfig.load(path)
    assert check_password(cfg, "admin", "admin-pass-1") and not check_password(cfg, "admin", "nope")


def test_local_mode_refuses_to_start_without_admin_password(tmp_path):
    cfg = defaults()
    cfg["server"].update({"mode": "local", "port": 8799})
    cfg["dataset"].update({"catalogue_path": str(FIXTURE), "data_dir": str(tmp_path / "d")})
    path = tmp_path / "c.json"
    path.write_text(json.dumps(cfg))
    env = {**os.environ, "FOODREC_CONFIG": str(path)}
    env.pop("FOODREC_MODE")
    r = subprocess.run([sys.executable, "-m", "foodrec", "serve"], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 2 and "foodrec admin password" in r.stderr


# ---- local mode -------------------------------------------------------------------------------------------
def test_local_mode_everyone_signs_in(tmp_path):
    c, _ = _client(tmp_path, "local")
    assert c.get("/api/users/p01/state", headers={"X-User": "p01"}).status_code == 401   # X-User is ignored
    assert c.get("/api/config", headers={"X-User": "admin"}).status_code == 401
    admin = _login(c, "admin", "admin-pass-1")
    p01 = _login(c, "p01", "maple-river-42")
    assert c.get("/api/config", headers=admin).status_code == 200
    assert c.get("/api/config", headers=p01).status_code == 403                         # participants: no Config
    st = c.get("/api/users/p01/state", headers=p01).json()
    assert st["access"]["role"] == "baseline"
    assert c.get("/api/users/admin/state", headers=admin).json()["access"]["features"]["configure"]
    v = c.get("/api/config", headers=admin).json()
    assert "password_hash" not in json.dumps(v) and v["effective"]["access"]["admin"]["password_set"]


def test_sessions_end_when_the_password_changes_or_the_account_goes(tmp_path):
    c, path = _client(tmp_path, "local", participants=[
        {"username": "p01", "password": "maple-river-42", "role": "baseline"},
        {"username": "p02", "password": "cedar-oak-11", "role": "full"}])
    admin, p01, p02 = _login(c, "admin", "admin-pass-1"), _login(c, "p01", "maple-river-42"), _login(c, "p02", "cedar-oak-11")
    data = json.loads(path.read_text())
    data["study"]["participants"] = [{"username": "p01", "password": "new-pass-99", "role": "baseline"}]  # p02 removed
    assert c.put("/api/config", headers=admin, json={"config": data}).status_code == 200
    assert c.get("/api/users/p01/state", headers=p01).status_code == 401
    assert c.get("/api/users/p02/state", headers=p02).status_code == 401
    assert c.get("/api/config", headers=admin).status_code == 200      # the admin's hash was kept on save
    assert json.loads(path.read_text())["access"]["admin"]["password_hash"]


def test_sign_in_attempts_are_limited(tmp_path):
    c, _ = _client(tmp_path, "local")
    codes = [c.post("/api/login", json={"username": "p01", "password": f"x{i}"}).status_code for i in range(12)]
    assert codes[:10] == [401] * 10 and codes[-1] == 429


def test_export_never_contains_passwords(tmp_path):
    c, _ = _client(tmp_path, "local")
    doc = c.get("/api/export", headers=_login(c, "admin", "admin-pass-1")).text
    assert "maple-river-42" not in doc and "pbkdf2" not in doc


# ---- demo mode --------------------------------------------------------------------------------------------
def test_demo_admin_and_user_views(tmp_path):
    c, path = _client(tmp_path, "demo")
    h = {"X-User": VISITOR}
    assert c.post("/api/login", json={"username": "admin", "password": "admin-pass-1"}).status_code == 400
    assert c.get("/api/users/p01/state", headers={"X-User": "p01"}).status_code == 403   # not a visitor id
    assert c.get(f"/api/users/{VISITOR}/state", headers=h).json()["access"]["role"] == "full"
    assert c.get("/api/config", headers=h).status_code == 403                           # user view
    c.post("/api/demo/view", headers=h, json={"view": "admin"})
    v = c.get("/api/config", headers=h).json()
    assert v["read_only"] and v["api_keys"] == {}
    before = path.read_text()
    assert c.put("/api/config", headers=h, json={"config": {}}).status_code == 403
    assert c.post("/api/config/secret", headers=h, json={"name": "X_API_KEY", "value": "1"}).status_code == 403
    assert path.read_text() == before
    r = c.post("/api/config/population", headers=h, json={"population": {"size": 4, "prefix": "q", "digits": 2,
                                                                          "roles": {"full": 50, "baseline": 50}}})
    assert r.status_code == 200 and len(r.json()["participants"]) == 4          # a preview, nothing saved
    other = "visitor-ffffffffffff"
    c.post(f"/api/users/{other}/onboard", headers={"X-User": other}, json={"declared": {}, "weights": {"h": .4, "s": .3, "t": .3}})
    c.post(f"/api/users/{VISITOR}/onboard", headers=h, json={"declared": {}, "weights": {"h": .4, "s": .3, "t": .3}})
    users = [u["user_id"] for u in c.get("/api/admin/users", headers=h).json()["users"]]
    assert users == [VISITOR]                                                   # visitors never see each other
    doc = json.loads(c.get("/api/export", headers=h).text)
    assert [u["user_id"] for u in doc["users"]] == [VISITOR]
    assert c.get(f"/api/users/{other}/state", headers=h).status_code == 403


def test_export_file_names_carry_the_user(tmp_path):
    c, _ = _client(tmp_path, "local", participants=[
        {"username": "p01", "password": "maple-river-42", "role": "baseline"},
        {"username": "p02", "password": "cedar-oak-11", "role": "full"}])
    admin = _login(c, "admin", "admin-pass-1")
    for uid, pw in (("p01", "maple-river-42"), ("p02", "cedar-oak-11")):
        c.post(f"/api/users/{uid}/onboard", headers=_login(c, uid, pw), json={"declared": {}, "weights": {"h": .4, "s": .3, "t": .3}})
    name = lambda r: r.headers["content-disposition"].split('filename="')[1].rstrip('"')  # noqa: E731
    assert name(c.get("/api/export?users=p01", headers=admin)).startswith("foodrec-p01-")
    assert name(c.get("/api/export?users=p01,p02&format=zip", headers=admin)).startswith("foodrec-p01-p02-")
    assert name(c.get("/api/export", headers=admin)).startswith("foodrec-all-users-")
    own = c.get("/api/users/p02/export", headers=_login(c, "p02", "cedar-oak-11"))
    assert name(own).startswith("foodrec-p02-") and name(own).endswith(".json")

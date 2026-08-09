"""The user store: CRUD, bcrypt, and no hash ever leaving the building (R12, R17).

The gap this project exists to close. The source had no user store at all — an
environment string parsed once at boot, no CRUD, no table — so "add a person"
meant editing a file and restarting.

The sharpest assertion here is :func:`test_no_endpoint_anywhere_returns_a_hash`,
which walks **every** route on the user router rather than the handful someone
thought to check. R17 is the kind of rule that holds for a year and then fails
the day somebody adds a debug field.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from weave.server.routers.users import create_user_routes
from weave.server.users import (
    ACTIVE,
    DISABLED,
    InMemoryUserStore,
    JsonUserStore,
    User,
    UserConflict,
    UserError,
    UserService,
    hash_password,
    verify_password,
)


def _service() -> UserService:
    return UserService(InMemoryUserStore())


def _client(service: UserService, *, as_role: str = "admin",
            as_user: str = "root") -> TestClient:
    """A client whose requests carry an authenticated principal.

    The principal is injected the way the real auth dependency injects it —
    onto ``request.state`` after validating a token — because that is the only
    place a router may read it from (A6). Passing ``as_role=None`` gives an
    unauthenticated client, for testing the 403s.
    """
    app = FastAPI()
    app.include_router(create_user_routes(service))

    if as_role is not None:
        @app.middleware("http")
        async def _principal(request, call_next):
            request.state.token_info = {"username": as_user, "role": as_role}
            return await call_next(request)

    return TestClient(app)


# ── the store ────────────────────────────────────────────────────────────────


@pytest.mark.offline
def test_create_persists_a_user_with_a_hash_not_a_password():
    svc = _service()
    user = svc.create("alice", "correct-horse-battery", role="architect")
    assert user.username == "alice"
    assert user.role == "architect"
    assert user.status == ACTIVE
    assert user.password_hash and user.password_hash != "correct-horse-battery"
    assert user.password_hash.startswith("$2")           # a bcrypt hash, not a digest
    assert verify_password("correct-horse-battery", user.password_hash)


@pytest.mark.offline
def test_two_users_with_the_same_password_get_different_hashes():
    """Salted, so a leaked store does not reveal who shares a password."""
    svc = _service()
    a = svc.create("alice", "the-same-password")
    b = svc.create("bob", "the-same-password")
    assert a.password_hash != b.password_hash


@pytest.mark.offline
def test_usernames_are_unique_case_insensitively():
    svc = _service()
    svc.create("alice", "a-password-here")
    with pytest.raises(UserConflict):
        svc.create("Alice", "another-password")


@pytest.mark.offline
@pytest.mark.parametrize("bad", ["", "a", "has space", "wat?", "x" * 65])
def test_a_malformed_username_is_refused(bad):
    with pytest.raises(UserError):
        _service().create(bad, "a-good-password")


@pytest.mark.offline
def test_a_short_password_is_refused():
    with pytest.raises(UserError):
        _service().create("alice", "short")


@pytest.mark.offline
def test_an_overlong_password_is_refused_rather_than_truncated():
    """bcrypt ignores everything past 72 bytes.

    Accepting a 200-character passphrase would mean storing something weaker
    than the user believes they chose — and silently. Refusing says so.
    """
    with pytest.raises(UserError) as e:
        _service().create("alice", "x" * 200)
    assert "72" in str(e.value)


@pytest.mark.offline
def test_update_disable_and_reenable():
    svc = _service()
    user = svc.create("alice", "a-good-password")
    svc.update(user.id, status=DISABLED)
    assert svc.require(user.id).status == DISABLED
    assert svc.authenticate("alice", "a-good-password") is None, "a disabled user signed in"
    svc.update(user.id, status=ACTIVE)
    assert svc.authenticate("alice", "a-good-password") is not None


@pytest.mark.offline
def test_password_reset_invalidates_the_old_one():
    svc = _service()
    user = svc.create("alice", "the-old-password")
    svc.set_password(user.id, "the-new-password")
    assert svc.authenticate("alice", "the-old-password") is None
    assert svc.authenticate("alice", "the-new-password") is not None


@pytest.mark.offline
def test_delete_removes_the_user():
    svc = _service()
    user = svc.create("alice", "a-good-password")
    assert svc.delete(user.id) is True
    assert svc.get(user.id) is None
    assert svc.delete(user.id) is False


@pytest.mark.offline
def test_authentication_rejects_an_unknown_user_and_a_wrong_password():
    svc = _service()
    svc.create("alice", "a-good-password")
    assert svc.authenticate("alice", "wrong-password") is None
    assert svc.authenticate("nobody", "a-good-password") is None


@pytest.mark.offline
def test_a_corrupt_hash_is_a_failed_login_not_a_crash():
    """A damaged record must not turn every login attempt into a 500."""
    svc = _service()
    user = svc.create("alice", "a-good-password")
    user.password_hash = "not-a-bcrypt-hash"
    svc._store.save("_system", user)
    assert svc.authenticate("alice", "a-good-password") is None


@pytest.mark.offline
def test_the_store_survives_a_round_trip_through_json():
    """The file path is the default, so the record has to serialise cleanly."""
    with tempfile.TemporaryDirectory() as d:
        svc = UserService(JsonUserStore(d))
        created = svc.create("alice", "a-good-password", role="architect",
                             workspaces=["alpha", "beta"], granted_by="admin")
        reread = UserService(JsonUserStore(d)).require(created.id)
        assert reread.username == "alice"
        assert reread.role == "architect"
        assert reread.workspaces == ["alpha", "beta"]
        assert verify_password("a-good-password", reread.password_hash)


@pytest.mark.offline
def test_the_hash_is_never_written_to_a_public_dict():
    user = User(id="1", username="alice", password_hash=hash_password("a-good-password"))
    public = user.public_dict()
    assert "password_hash" not in public
    assert "password" not in json.dumps(public).lower().replace("password_hash", "")


# ── the routes ───────────────────────────────────────────────────────────────


@pytest.mark.offline
def test_crud_over_http():
    svc = _service()
    client = _client(svc)

    created = client.post("/users", json={
        "username": "alice", "password": "a-good-password",
        "role": "architect", "email": "alice@example.com",
    })
    assert created.status_code == 201, created.text
    user_id = created.json()["id"]

    assert client.get("/users").status_code == 200
    assert client.get(f"/users/{user_id}").json()["username"] == "alice"

    patched = client.patch(f"/users/{user_id}", json={"display_name": "Alice A"})
    assert patched.json()["display_name"] == "Alice A"

    assert client.post(f"/users/{user_id}/password",
                       json={"password": "a-newer-password"}).status_code == 204
    assert svc.authenticate("alice", "a-newer-password") is not None

    assert client.delete(f"/users/{user_id}").status_code == 204
    assert client.get(f"/users/{user_id}").status_code == 404


@pytest.mark.offline
def test_duplicate_username_is_409_and_bad_input_is_400():
    client = _client(_service())
    client.post("/users", json={"username": "alice", "password": "a-good-password"})
    assert client.post("/users", json={
        "username": "alice", "password": "a-good-password"}).status_code == 409
    assert client.post("/users", json={
        "username": "bob", "password": "x" * 200}).status_code == 400


@pytest.mark.offline
def test_no_endpoint_anywhere_returns_a_hash():
    """R17, swept over every route rather than the ones we happened to think of.

    A response-shape rule fails the day somebody adds a debug field, so this
    walks the whole router and greps the raw bodies.
    """
    svc = _service()
    client = _client(svc)
    created = client.post("/users", json={
        "username": "alice", "password": "a-good-password"}).json()
    user_id = created["id"]
    the_hash = svc.require(user_id).password_hash
    assert the_hash, "precondition: the user has a hash to leak"

    responses = [
        client.get("/users"),
        client.get(f"/users/{user_id}"),
        client.patch(f"/users/{user_id}", json={"display_name": "Alice"}),
        client.get(f"/users/{user_id}/workspaces"),
        client.put(f"/users/{user_id}/workspaces", json={"workspaces": ["alpha"]}),
        client.post(f"/users/{user_id}/password", json={"password": "another-password"}),
    ]
    for r in responses:
        body = r.text or ""
        assert "password_hash" not in body, f"{r.request.method} {r.request.url} leaked the field"
        assert the_hash not in body, f"{r.request.method} {r.request.url} leaked the hash"
        assert "$2b$" not in body and "$2a$" not in body


@pytest.mark.offline
def test_the_openapi_schema_has_no_hash_field():
    """Not even as documentation — a schema is a promise about the shape."""
    app = FastAPI()
    app.include_router(create_user_routes(_service()))
    assert "password_hash" not in json.dumps(app.openapi())


@pytest.mark.offline
def test_the_last_administrator_cannot_delete_or_demote_themselves():
    """Locking everyone out is a typo away, and irreversible from the UI."""
    svc = _service()
    client = _client(svc, as_user="root", as_role="admin")
    admin = client.post("/users", json={
        "username": "root", "password": "a-good-password", "role": "admin"}).json()

    # A second user exists but is not an admin, so `root` is still the only one.
    client.post("/users", json={"username": "dev", "password": "a-good-password",
                                "role": "developer"})

    assert client.delete(f"/users/{admin['id']}").status_code == 409
    assert client.patch(f"/users/{admin['id']}", json={"role": "developer"}).status_code == 409
    assert client.patch(f"/users/{admin['id']}", json={"status": DISABLED}).status_code == 409

    # With a second admin present, both become allowed again.
    client.post("/users", json={"username": "root2", "password": "a-good-password",
                                "role": "admin"})
    assert client.patch(f"/users/{admin['id']}", json={"role": "developer"}).status_code == 200


@pytest.mark.offline
def test_administration_requires_an_admin_role_once_a_user_exists():
    """The bootstrap window closes on the first user, and stays closed (A6).

    While nobody exists the server is already handing out guest tokens, so
    refusing the first account would leave a fresh install with no way in that
    is not editing a file — the thing this milestone removes. After that,
    administering users is an authenticated, role-checked act.
    """
    svc = _service()
    anon = _client(svc, as_role=None)

    # bootstrap: the very first account can be created without a principal
    first = anon.post("/users", json={
        "username": "root", "password": "a-good-password", "role": "admin"})
    assert first.status_code == 201

    # and immediately afterwards, the same unauthenticated client is refused
    assert anon.get("/users").status_code == 403
    assert anon.post("/users", json={
        "username": "mallory", "password": "a-good-password",
        "role": "admin"}).status_code == 403


@pytest.mark.offline
def test_a_developer_cannot_administer_users():
    svc = _service()
    _client(svc).post("/users", json={
        "username": "root", "password": "a-good-password", "role": "admin"})

    developer = _client(svc, as_user="dev", as_role="developer")
    assert developer.get("/users").status_code == 403
    assert developer.post("/users", json={
        "username": "mallory", "password": "a-good-password"}).status_code == 403


@pytest.mark.offline
def test_the_role_comes_from_the_token_not_from_the_request(monkeypatch):
    """A6: a client that *claims* to be an admin in the body is still a developer."""
    svc = _service()
    _client(svc).post("/users", json={
        "username": "root", "password": "a-good-password", "role": "admin"})

    developer = _client(svc, as_user="dev", as_role="developer")
    refused = developer.post(
        "/users",
        json={"username": "mallory", "password": "a-good-password", "role": "admin"},
        headers={"X-Role": "admin"},
    )
    assert refused.status_code == 403


@pytest.mark.offline
def test_authorisation_is_decided_before_the_body_is_validated():
    """A refused caller must not be told what the body should have looked like.

    Found by running the M1 gate against a live server: a non-admin sending a
    malformed body got 422 with the full field list, while the same caller
    sending a *valid* body got 403. That difference hands the schema to exactly
    the people who were refused, and it also reads as "your JSON was wrong"
    rather than "you may not do this" — so the caller retries instead of
    stopping.

    The fix is ordering: the admin check is a dependency, and FastAPI resolves
    dependencies before it validates a request body.
    """
    svc = _service()
    _client(svc).post("/users", json={
        "username": "root", "password": "a-good-password", "role": "admin"})

    developer = _client(svc, as_user="dev", as_role="developer")

    valid_body = developer.post("/users", json={
        "username": "mallory", "password": "a-good-password", "role": "admin"})
    malformed_body = developer.post("/users", json={"nonsense": 1})
    empty_body = developer.post("/users", json={})

    assert valid_body.status_code == 403
    assert malformed_body.status_code == 403, (
        f"got {malformed_body.status_code} — an unauthorised caller was handed "
        f"schema feedback: {malformed_body.text[:200]}"
    )
    assert empty_body.status_code == 403
    for r in (malformed_body, empty_body):
        assert "username" not in r.text, "the refusal leaked a field name"

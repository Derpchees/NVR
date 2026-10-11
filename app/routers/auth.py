"""Login, logout and changes to the panel account."""

from __future__ import annotations

import hmac

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from .. import config, db, security
from ..schemas import CredentialsInput, LoginInput

router = APIRouter(prefix="/api/auth")


@router.post("/login")
def login(credentials: LoginInput, request: Request):
    client = request.client.host if request.client else "?"
    wait = security.login_locked_for(client)
    if wait:
        raise HTTPException(status_code=429, detail=f"Demasiados intentos. Espera {wait} s.")
    with db.connect() as connection:
        user = connection.execute("SELECT username, password_hash FROM users").fetchone()
    valid_user = hmac.compare_digest(credentials.username, user["username"])
    # Always hash so response time does not reveal whether the username exists.
    valid_password = security.check_password(credentials.password, user["password_hash"])
    security.record_login(client, valid_user and valid_password)
    if not (valid_user and valid_password):
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos")
    response = JSONResponse({"username": user["username"]})
    security.set_session_cookie(response, security.new_session())
    return response


@router.post("/logout", status_code=204)
def logout(request: Request):
    token = request.cookies.get(config.SESSION_COOKIE)
    if token:
        security.end_session(token)
    response = Response(status_code=204)
    response.delete_cookie(config.SESSION_COOKIE, path="/")
    return response


@router.get("/me")
def current_user():
    with db.connect() as connection:
        user = connection.execute("SELECT username, updated_at FROM users").fetchone()
    return {"username": user["username"], "updated_at": user["updated_at"]}


@router.put("/credentials")
def change_credentials(command: CredentialsInput):
    with db.connect() as connection:
        user = connection.execute("SELECT password_hash FROM users").fetchone()
        if not security.check_password(command.current_password, user["password_hash"]):
            raise HTTPException(status_code=403, detail="La contraseña actual no es correcta")
        password_hash = (
            security.hash_password(command.new_password)
            if command.new_password
            else user["password_hash"]
        )
        connection.execute(
            "UPDATE users SET username = ?, password_hash = ?, updated_at = CURRENT_TIMESTAMP",
            (command.username, password_hash),
        )
        # Credentials changed: every existing session must log in again.
        connection.execute("DELETE FROM sessions")
    response = JSONResponse({"username": command.username})
    security.set_session_cookie(response, security.new_session())
    return response

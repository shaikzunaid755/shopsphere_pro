# Module 1: Authentication and Authorization
from datetime import datetime, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.audit import track_activity
from app.core.database import get_db
from app.core.deps import get_current_user, get_token_payload
from app.core.roles import CUSTOMER
from app.core.security import (verify_password, hash_password, create_access_token,
                               create_refresh_token, decode_token)
from app.core.utils import utcnow
from app.models import User, RefreshToken, RevokedToken, RolePermission, Permission
from app.schemas.common import Message
from app.schemas.user import (LoginRequest, TokenResponse, RefreshRequest, LogoutRequest,
                              ChangePasswordRequest, UserOut)

router = APIRouter(prefix="/auth", tags=["01 Authentication & Authorization"])


def _issue_tokens(db, user):
    access, _, _ = create_access_token(user.id, user.role_name)
    refresh, jti, exp = create_refresh_token(user.id, user.role_name)
    db.add(RefreshToken(user_id=user.id, jti=jti, expires_at=exp.replace(tzinfo=None)))
    return TokenResponse(access_token=access, refresh_token=refresh, role=user.role_name, user_id=user.id)


def _login(db, data, request, staff: bool):
    user = db.query(User).filter(User.email == data.email.lower()).first()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")

    if staff and user.role_name == CUSTOMER:
        raise HTTPException(403, "Use customer login")
    if not staff and user.role_name != CUSTOMER:
        raise HTTPException(403, "Use employee login")
    if user.status != "active":
        raise HTTPException(403, f"Account is {user.status}")
    if user.role_name == CUSTOMER and not user.is_email_verified:
        raise HTTPException(403, "Please verify your email before logging in")

    user.last_login_at = utcnow()
    tokens = _issue_tokens(db, user)
    track_activity(db, user.id, "login", "staff login" if staff else "customer login",
                   request.client.host if request.client else None)
    db.commit()
    return tokens


@router.post("/login", response_model=TokenResponse, summary="Customer login")
def customer_login(data: LoginRequest, request: Request, db: Session = Depends(get_db)):
    return _login(db, data, request, staff=False)


@router.post("/staff/login", response_model=TokenResponse, summary="Employee login")
def staff_login(data: LoginRequest, request: Request, db: Session = Depends(get_db)):
    return _login(db, data, request, staff=True)


@router.post("/refresh", response_model=TokenResponse)
def refresh_tokens(data: RefreshRequest, db: Session = Depends(get_db)):
    try:
        payload = decode_token(data.refresh_token)
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired refresh token")
    if payload.get("type") != "refresh":
        raise HTTPException(401, "Refresh token required")

    stored = db.query(RefreshToken).filter(RefreshToken.jti == payload["jti"]).first()
    if not stored or stored.revoked:
        raise HTTPException(401, "Refresh token revoked")
    user = db.get(User, int(payload["sub"]))
    if not user or user.status != "active":
        raise HTTPException(401, "User not active")

    # rotate: old refresh token can't be used again
    stored.revoked = True
    tokens = _issue_tokens(db, user)
    db.commit()
    return tokens


@router.post("/logout", response_model=Message)
def logout(data: LogoutRequest, request: Request, payload: dict = Depends(get_token_payload),
           user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    exp = None
    if "exp" in payload:
        exp = datetime.fromtimestamp(payload["exp"], timezone.utc).replace(tzinfo=None)
    db.add(RevokedToken(jti=payload["jti"], expires_at=exp))

    if data.refresh_token:
        try:
            rp = decode_token(data.refresh_token)
            row = db.query(RefreshToken).filter(RefreshToken.jti == rp.get("jti"),
                                                RefreshToken.user_id == user.id).first()
            if row:
                row.revoked = True
        except jwt.PyJWTError:
            pass  # expired refresh token, nothing to revoke
    track_activity(db, user.id, "logout", ip=request.client.host if request.client else None)
    db.commit()
    return {"message": "Logged out"}


@router.post("/logout-all", response_model=Message, summary="Revoke all sessions of current user")
def logout_all(payload: dict = Depends(get_token_payload), user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    db.query(RefreshToken).filter(RefreshToken.user_id == user.id, RefreshToken.revoked == False) \
        .update({"revoked": True})  # noqa: E712
    db.add(RevokedToken(jti=payload["jti"]))
    track_activity(db, user.id, "logout_all")
    db.commit()
    return {"message": "All sessions revoked"}


@router.post("/change-password", response_model=Message)
def change_password(data: ChangePasswordRequest, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    if not verify_password(data.old_password, user.password_hash):
        raise HTTPException(400, "Old password is wrong")
    if data.old_password == data.new_password:
        raise HTTPException(400, "New password must be different")
    user.password_hash = hash_password(data.new_password)
    # force login again on other devices
    db.query(RefreshToken).filter(RefreshToken.user_id == user.id).update({"revoked": True})
    track_activity(db, user.id, "password_changed")
    db.commit()
    return {"message": "Password changed, please login again on other devices"}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.get("/me/permissions", summary="Role and permission codes of current user")
def my_permissions(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    codes = (db.query(Permission.code)
             .join(RolePermission, RolePermission.permission_id == Permission.id)
             .filter(RolePermission.role_id == user.role_id).all())
    return {"role": user.role_name, "permissions": [c[0] for c in codes]}
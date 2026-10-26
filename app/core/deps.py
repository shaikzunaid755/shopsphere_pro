import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_token
from app.core.roles import SUPER_ADMIN, CUSTOMER
from app.models import User, RevokedToken, RolePermission, Permission

bearer = HTTPBearer(auto_error=False)


def get_token_payload(creds: HTTPAuthorizationCredentials = Depends(bearer)):
    if creds is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_token(creds.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Access token required")
    return payload


def get_current_user(payload: dict = Depends(get_token_payload), db: Session = Depends(get_db)) -> User:
    revoked = db.query(RevokedToken).filter(RevokedToken.jti == payload["jti"]).first()
    if revoked:
        raise HTTPException(status_code=401, detail="Token has been revoked, login again")

    user = db.get(User, int(payload["sub"]))
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if user.status != "active":
        raise HTTPException(status_code=403, detail=f"Account is {user.status}")
    return user


def require_roles(*roles):
    """use like: user = Depends(require_roles(ADMIN, ORDER_MANAGER))
    super admin is always allowed"""
    def checker(user: User = Depends(get_current_user)):
        if user.role_name == SUPER_ADMIN or user.role_name in roles:
            return user
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="You don't have permission for this action")
    return checker


def require_permission(code: str):
    # checks the role_permissions table, so admins can change access without code changes
    def checker(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
        if user.role_name == SUPER_ADMIN:
            return user
        found = (db.query(RolePermission)
                 .join(Permission, Permission.id == RolePermission.permission_id)
                 .filter(RolePermission.role_id == user.role_id, Permission.code == code)
                 .first())
        if not found:
            raise HTTPException(status_code=403, detail=f"Missing permission: {code}")
        return user
    return checker


def is_customer(user: User) -> bool:
    return user.role_name == CUSTOMER


def check_owner_or_staff(user: User, owner_id: int):
    # customers can only touch their own records
    if is_customer(user) and user.id != owner_id:
        raise HTTPException(status_code=404, detail="Not found")


def get_or_404(db: Session, model, obj_id, name=None):
    obj = db.get(model, obj_id)
    if not obj:
        raise HTTPException(status_code=404, detail=f"{name or model.__name__} not found")
    return obj
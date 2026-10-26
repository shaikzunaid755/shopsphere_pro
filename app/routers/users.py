# Module 2: User Management
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.audit import audit, track_activity
from app.core.database import get_db
from app.core.deps import require_roles, get_or_404
from app.core.roles import ADMIN, SUPER_ADMIN
from app.core.security import hash_password
from app.models import User, Role, Permission, RolePermission, UserActivity, RefreshToken
from app.schemas.user import (UserCreate, UserUpdate, UserOut, AssignRoleRequest, UserStatusUpdate,
                              ActivityOut, RoleOut, PermissionOut, RolePermissionsUpdate)

router = APIRouter(prefix="/users", tags=["02 User Management"])

admin_only = require_roles(ADMIN)


def _role(db, name):
    role = db.query(Role).filter(Role.name == name).first()
    if not role:
        raise HTTPException(400, f"Role {name} not found, run the seed script")
    return role


@router.post("", response_model=UserOut, status_code=201)
def create_user(data: UserCreate, db: Session = Depends(get_db), admin: User = Depends(admin_only)):
    if data.role == SUPER_ADMIN and admin.role_name != SUPER_ADMIN:
        raise HTTPException(403, "Only super admin can create another super admin")
    if db.query(User).filter(User.email == data.email.lower()).first():
        raise HTTPException(409, "Email already exists")
    if data.phone and db.query(User).filter(User.phone == data.phone).first():
        raise HTTPException(409, "Phone already exists")
    user = User(full_name=data.full_name, email=data.email.lower(), phone=data.phone,
                password_hash=hash_password(data.password), role_id=_role(db, data.role).id,
                is_email_verified=True, registration_status="verified")
    db.add(user)
    db.flush()
    audit(db, admin, "user_created", "user", user.id, None, {"email": user.email, "role": data.role})
    db.commit()
    db.refresh(user)
    return user


@router.get("", response_model=List[UserOut])
def list_users(search: Optional[str] = None, role: Optional[str] = None, status: Optional[str] = None,
               page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
               db: Session = Depends(get_db), admin: User = Depends(admin_only)):
    q = db.query(User)
    if search:
        like = f"%{search}%"
        q = q.filter(or_(User.full_name.ilike(like), User.email.ilike(like), User.phone.ilike(like)))
    if role:
        q = q.join(Role, Role.id == User.role_id).filter(Role.name == role)
    if status:
        q = q.filter(User.status == status)
    return q.order_by(User.id.desc()).offset((page - 1) * size).limit(size).all()


@router.get("/roles", response_model=List[RoleOut])
def list_roles(db: Session = Depends(get_db), admin: User = Depends(admin_only)):
    return db.query(Role).all()


@router.get("/permissions", response_model=List[PermissionOut])
def list_permissions(db: Session = Depends(get_db), admin: User = Depends(admin_only)):
    return db.query(Permission).all()


@router.get("/roles/{role_name}/permissions", response_model=List[PermissionOut])
def role_permissions(role_name: str, db: Session = Depends(get_db), admin: User = Depends(admin_only)):
    role = _role(db, role_name)
    return [rp.permission for rp in role.permissions]


@router.put("/roles/{role_name}/permissions", response_model=List[PermissionOut])
def set_role_permissions(role_name: str, data: RolePermissionsUpdate, db: Session = Depends(get_db),
                         admin: User = Depends(require_roles())):  # super admin only
    role = _role(db, role_name)
    perms = db.query(Permission).filter(Permission.code.in_(data.permission_codes)).all()
    missing = set(data.permission_codes) - {p.code for p in perms}
    if missing:
        raise HTTPException(400, f"Unknown permissions: {', '.join(missing)}")
    old = [rp.permission.code for rp in role.permissions]
    role.permissions.clear()
    db.flush()
    for p in perms:
        role.permissions.append(RolePermission(permission_id=p.id))
    audit(db, admin, "role_permissions_changed", "role", role.id, old, data.permission_codes)
    db.commit()
    db.refresh(role)
    return [rp.permission for rp in role.permissions]


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: int, db: Session = Depends(get_db), admin: User = Depends(admin_only)):
    return get_or_404(db, User, user_id)


@router.put("/{user_id}", response_model=UserOut)
def update_user(user_id: int, data: UserUpdate, db: Session = Depends(get_db),
                admin: User = Depends(admin_only)):
    user = get_or_404(db, User, user_id)
    changes = data.model_dump(exclude_unset=True)
    if "phone" in changes and changes["phone"]:
        clash = db.query(User).filter(User.phone == changes["phone"], User.id != user.id).first()
        if clash:
            raise HTTPException(409, "Phone already used")
    old = {k: getattr(user, k) for k in changes}
    for k, v in changes.items():
        setattr(user, k, v)
    audit(db, admin, "user_updated", "user", user.id, old, changes)
    db.commit()
    db.refresh(user)
    return user


@router.put("/{user_id}/role", response_model=UserOut)
def assign_role(user_id: int, data: AssignRoleRequest, db: Session = Depends(get_db),
                admin: User = Depends(admin_only)):
    user = get_or_404(db, User, user_id)
    if (data.role == SUPER_ADMIN or user.role_name == SUPER_ADMIN) and admin.role_name != SUPER_ADMIN:
        raise HTTPException(403, "Only super admin can change super admin roles")
    if user.id == admin.id:
        raise HTTPException(400, "You cannot change your own role")
    old = user.role_name
    user.role_id = _role(db, data.role).id
    db.query(RefreshToken).filter(RefreshToken.user_id == user.id).update({"revoked": True})
    audit(db, admin, "role_assigned", "user", user.id, {"role": old}, {"role": data.role})
    db.commit()
    db.refresh(user)
    return user


@router.patch("/{user_id}/status", response_model=UserOut, summary="Activate / deactivate / suspend user")
def change_status(user_id: int, data: UserStatusUpdate, db: Session = Depends(get_db),
                  admin: User = Depends(admin_only)):
    user = get_or_404(db, User, user_id)
    if user.id == admin.id:
        raise HTTPException(400, "You cannot change your own status")
    if user.role_name == SUPER_ADMIN and admin.role_name != SUPER_ADMIN:
        raise HTTPException(403, "Cannot change super admin status")
    old = user.status
    user.status = data.status
    if data.status != "active":
        db.query(RefreshToken).filter(RefreshToken.user_id == user.id).update({"revoked": True})
    else:
        user.deactivation_requested = False
    audit(db, admin, "user_status_changed", "user", user.id, {"status": old},
          {"status": data.status, "reason": data.reason})
    track_activity(db, user.id, f"status_{data.status}", data.reason)
    db.commit()
    db.refresh(user)
    return user


@router.delete("/{user_id}", response_model=UserOut, summary="Deactivate user (soft delete)")
def deactivate_user(user_id: int, db: Session = Depends(get_db), admin: User = Depends(admin_only)):
    return change_status(user_id, UserStatusUpdate(status="inactive", reason="deactivated by admin"), db, admin)


@router.get("/{user_id}/activity", response_model=List[ActivityOut])
def user_activity(user_id: int, limit: int = Query(50, le=200), db: Session = Depends(get_db),
                  admin: User = Depends(admin_only)):
    get_or_404(db, User, user_id)
    return (db.query(UserActivity).filter(UserActivity.user_id == user_id)
            .order_by(UserActivity.id.desc()).limit(limit).all())
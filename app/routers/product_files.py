# Module 10: Product Images and Documents
import os
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.config import settings
from app.core.database import get_db
from app.core.deps import require_roles, get_or_404
from app.core.roles import ADMIN, PRODUCT_MANAGER
from app.models import Product, ProductVariant, ProductFile, User
from app.schemas.catalog import FileOut
from app.schemas.common import Message

router = APIRouter(tags=["10 Product Images & Documents"])

managers = require_roles(ADMIN, PRODUCT_MANAGER)

IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
DOC_TYPES = {"application/pdf": ".pdf"}

# first bytes of each format, so a renamed .exe can't pass as .jpg
MAGIC = {
    ".jpg": [b"\xff\xd8\xff"],
    ".png": [b"\x89PNG"],
    ".webp": [b"RIFF"],
    ".pdf": [b"%PDF"],
}


@router.post("/products/{product_id}/files", response_model=FileOut, status_code=201)
async def upload_file(product_id: int, file: UploadFile = File(...), variant_id: Optional[int] = Form(None),
                      db: Session = Depends(get_db), user: User = Depends(managers)):
    product = get_or_404(db, Product, product_id)
    variant_id = variant_id or None  # swagger sends 0 when the field is left empty
    if variant_id:
        v = db.get(ProductVariant, variant_id)
        if not v or v.product_id != product.id:
            raise HTTPException(400, "Variant does not belong to this product")

    ctype = file.content_type
    if ctype in IMAGE_TYPES:
        kind, ext = "image", IMAGE_TYPES[ctype]
    elif ctype in DOC_TYPES:
        kind, ext = "document", DOC_TYPES[ctype]
    else:
        raise HTTPException(400, "Only JPG, PNG, WEBP images and PDF documents are allowed")
    content = await file.read()
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    if len(content) == 0:
        raise HTTPException(400, "File is empty")
    if len(content) > max_bytes:
        raise HTTPException(400, f"File too large, max {settings.MAX_UPLOAD_MB} MB")
    if not any(content.startswith(m) for m in MAGIC[ext]):
        raise HTTPException(400, "File content does not match its type")

    folder = os.path.join(settings.UPLOAD_DIR, "products", str(product.id))
    os.makedirs(folder, exist_ok=True)
    stored = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(folder, stored)
    with open(path, "wb") as f:
        f.write(content)

    row = ProductFile(product_id=product.id, variant_id=variant_id, file_name=os.path.basename(file.filename or stored),
                      stored_name=stored, file_path=path, content_type=ctype, size_bytes=len(content),
                      file_kind=kind, uploaded_by=user.id)
    db.add(row)
    db.flush()
    audit(db, user, "file_uploaded", "product_file", row.id, None, {"product_id": product.id, "name": row.file_name})
    db.commit()
    db.refresh(row)
    return row


@router.get("/products/{product_id}/files", response_model=List[FileOut])
def list_files(product_id: int, kind: Optional[str] = None, variant_id: Optional[int] = None,
               db: Session = Depends(get_db)):
    get_or_404(db, Product, product_id)
    q = db.query(ProductFile).filter(ProductFile.product_id == product_id)
    if kind:
        q = q.filter(ProductFile.file_kind == kind)
    if variant_id:
        q = q.filter(ProductFile.variant_id == variant_id)
    return q.all()


@router.get("/files/{file_id}", response_model=FileOut, summary="File metadata")
def file_meta(file_id: int, db: Session = Depends(get_db)):
    return get_or_404(db, ProductFile, file_id, "File")


@router.get("/files/{file_id}/download")
def download(file_id: int, db: Session = Depends(get_db)):
    row = get_or_404(db, ProductFile, file_id, "File")
    product = db.get(Product, row.product_id)
    # files of inactive products are not public
    if product.status != "active":
        raise HTTPException(404, "File not found")
    if not os.path.exists(row.file_path):
        raise HTTPException(404, "File missing on disk")
    return FileResponse(row.file_path, media_type=row.content_type, filename=row.file_name)


@router.delete("/files/{file_id}", response_model=Message)
def delete_file(file_id: int, db: Session = Depends(get_db), user: User = Depends(managers)):
    row = get_or_404(db, ProductFile, file_id, "File")
    if os.path.exists(row.file_path):
        os.remove(row.file_path)
    audit(db, user, "file_deleted", "product_file", row.id, {"name": row.file_name}, None)
    db.delete(row)
    db.commit()
    return {"message": "File removed"}

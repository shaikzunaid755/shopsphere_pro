# Module 38: Product Reviews and Ratings
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.database import get_db
from app.core.deps import get_current_user, require_roles, is_customer, get_or_404
from app.core.roles import SUPER_ADMIN, CUSTOMER, ADMIN
from app.core.utils import utcnow
from app.models import Order, OrderItem, Product, Review, User
from app.schemas.reviews import ReviewCreate, ReviewUpdate, ReviewModerate, ReviewOut

router = APIRouter(prefix="/reviews", tags=["38 Product Reviews and Ratings"])

customer_only = require_roles(CUSTOMER)
moderators = require_roles(ADMIN, "product_manager", "support_agent")

# policy: new reviews wait for a moderator before they show on the product page
AUTO_APPROVE = False
# items in these states were really received by the customer
RECEIVED = ("delivered", "returned", "exchanged")
NEW_STATUS = {"approve": "approved", "reject": "rejected", "hide": "hidden", "remove": "removed"}


def _purchase(db, user_id, product_id, order_id=None):
    """latest delivered order of this user that has the product, None if he never got it"""
    q = (db.query(Order).join(OrderItem, OrderItem.order_id == Order.id)
         .filter(Order.user_id == user_id, Order.status == "delivered", OrderItem.product_id == product_id,
                 OrderItem.fulfillment_status.in_(RECEIVED)))
    if order_id:
        q = q.filter(Order.id == order_id)
    return q.order_by(Order.id.desc()).first()


def _product(db, product_id):
    product = db.get(Product, product_id)
    if not product or product.status != "active":
        raise HTTPException(404, "Product not found")
    return product


def _own_review(db, review_id, user):
    review = get_or_404(db, Review, review_id, "Review")
    if review.user_id != user.id:
        raise HTTPException(404, "Review not found")
    return review


def _clean(text):
    text = (text or "").strip()
    return text or None


def rating_summary(db, product_id):
    """average + star breakdown, only approved reviews count"""
    rows = (db.query(Review.rating, func.count(Review.id))
            .filter(Review.product_id == product_id, Review.status == "approved")
            .group_by(Review.rating).all())
    stars = {star: 0 for star in (5, 4, 3, 2, 1)}
    for rating, count in rows:
        stars[rating] = count
    total = sum(stars.values())
    average = round(sum(star * count for star, count in stars.items()) / total, 1) if total else 0
    return {
        "product_id": product_id,
        "average_rating": average,
        "total_reviews": total,
        "breakdown": [{"stars": star, "count": count, "percent": round(count * 100 / total, 1) if total else 0}
                      for star, count in stars.items()],
    }


# ---------------- public: product page ----------------

@router.get("/products/{product_id:int}/summary")
def product_summary(product_id: int, db: Session = Depends(get_db)):
    product = _product(db, product_id)
    return {"product_name": product.name, **rating_summary(db, product.id)}


@router.get("/products/{product_id:int}")
def product_reviews(product_id: int, rating: Optional[int] = Query(None, ge=1, le=5),
                    sort: Literal["newest", "highest", "lowest"] = "newest",
                    skip: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
                    db: Session = Depends(get_db)):
    product = _product(db, product_id)
    q = (db.query(Review, User.full_name).join(User, User.id == Review.user_id)
         .filter(Review.product_id == product.id, Review.status == "approved"))
    if rating:
        q = q.filter(Review.rating == rating)
    if sort == "highest":
        q = q.order_by(Review.rating.desc(), Review.id.desc())
    elif sort == "lowest":
        q = q.order_by(Review.rating.asc(), Review.id.desc())
    else:
        q = q.order_by(Review.id.desc())
    reviews = []
    for r, name in q.offset(skip).limit(limit).all():
        # only first name on the public page
        reviews.append({"id": r.id, "rating": r.rating, "title": r.title, "comment": r.comment,
                        "reviewer": (name or "Customer").split()[0], "verified_purchase": True,
                        "created_at": r.created_at})
    return {"summary": rating_summary(db, product.id), "reviews": reviews}


# ---------------- customer side ----------------

@router.get("/products/{product_id:int}/can-review")
def can_review(product_id: int, user: User = Depends(customer_only), db: Session = Depends(get_db)):
    product = _product(db, product_id)
    existing = db.query(Review).filter(Review.product_id == product.id, Review.user_id == user.id).first()
    if existing:
        return {"can_review": False, "reason": "You already reviewed this product, edit your review instead",
                "review_id": existing.id}
    order = _purchase(db, user.id, product.id)
    if not order:
        return {"can_review": False, "reason": "Only customers who received this product can review it"}
    return {"can_review": True, "order_id": order.id}


@router.post("", response_model=ReviewOut, status_code=201)
def create_review(data: ReviewCreate, user: User = Depends(customer_only), db: Session = Depends(get_db)):
    product = _product(db, data.product_id)
    # one review per customer per product
    if db.query(Review).filter(Review.product_id == product.id, Review.user_id == user.id).first():
        raise HTTPException(409, "You already reviewed this product, edit your review instead")
    order = _purchase(db, user.id, product.id, data.order_id)
    if not order:
        if data.order_id:
            raise HTTPException(403, "This order is not delivered or does not have this product")
        raise HTTPException(403, "Only customers who received this product can review it")
    if not _clean(data.title) and not _clean(data.comment):
        raise HTTPException(400, "Please write a title or a comment with your rating")

    review = Review(product_id=product.id, user_id=user.id, order_id=order.id, rating=data.rating,
                    title=_clean(data.title), comment=_clean(data.comment),
                    status="approved" if AUTO_APPROVE else "pending", created_at=utcnow())
    db.add(review)
    try:
        db.flush()
    except IntegrityError:
        # double click - unique key (product_id, user_id) stops the second one
        db.rollback()
        raise HTTPException(409, "You already reviewed this product")
    audit(db, user, "review_created", "review", review.id, None, {"product": product.id, "rating": data.rating})
    db.commit()
    db.refresh(review)
    return review


@router.get("/my", response_model=List[ReviewOut])
def my_reviews(user: User = Depends(customer_only), db: Session = Depends(get_db)):
    return db.query(Review).filter(Review.user_id == user.id).order_by(Review.id.desc()).all()


@router.put("/{review_id:int}", response_model=ReviewOut)
def edit_review(review_id: int, data: ReviewUpdate, user: User = Depends(customer_only),
                db: Session = Depends(get_db)):
    review = _own_review(db, review_id, user)
    if review.status == "removed":
        raise HTTPException(400, "This review was removed by a moderator and can't be edited")
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(400, "Nothing to update")
    old = {"rating": review.rating, "title": review.title, "comment": review.comment, "status": review.status}
    if "rating" in changes and changes["rating"] is not None:
        review.rating = changes["rating"]
    if "title" in changes:
        review.title = _clean(changes["title"])
    if "comment" in changes:
        review.comment = _clean(changes["comment"])
    if not review.title and not review.comment:
        raise HTTPException(400, "Review needs a title or a comment")
    # edited text has to be checked again
    review.status = "approved" if AUTO_APPROVE else "pending"
    review.moderated_by = None
    review.moderation_note = None
    audit(db, user, "review_updated", "review", review.id, old, changes)
    db.commit()
    db.refresh(review)
    return review


@router.delete("/{review_id:int}", status_code=204)
def delete_my_review(review_id: int, user: User = Depends(customer_only), db: Session = Depends(get_db)):
    review = _own_review(db, review_id, user)
    if review.status == "removed":
        raise HTTPException(400, "This review was removed by a moderator")
    audit(db, user, "review_deleted", "review", review.id, {"rating": review.rating, "comment": review.comment}, None)
    db.delete(review)
    db.commit()


# ---------------- moderation ----------------

@router.get("", response_model=List[ReviewOut])
def list_reviews(status: Optional[str] = None, product_id: Optional[int] = None,
                 rating: Optional[int] = Query(None, ge=1, le=5),
                 skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                 user: User = Depends(moderators), db: Session = Depends(get_db)):
    q = db.query(Review)
    if status:
        q = q.filter(Review.status == status)
    if product_id:
        q = q.filter(Review.product_id == product_id)
    if rating:
        q = q.filter(Review.rating == rating)
    return q.order_by(Review.id.desc()).offset(skip).limit(limit).all()


@router.get("/{review_id:int}", response_model=ReviewOut)
def get_review(review_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    review = get_or_404(db, Review, review_id, "Review")
    if is_customer(user):
        if review.user_id != user.id:
            raise HTTPException(404, "Review not found")
    elif user.role_name not in (SUPER_ADMIN, ADMIN, "product_manager", "support_agent"):
        raise HTTPException(403, "You don't have permission for this action")
    return review


@router.post("/{review_id:int}/moderate", response_model=ReviewOut)
def moderate_review(review_id: int, data: ReviewModerate, user: User = Depends(moderators),
                    db: Session = Depends(get_db)):
    review = db.query(Review).filter(Review.id == review_id).with_for_update().first()
    if not review:
        raise HTTPException(404, "Review not found")
    new_status = NEW_STATUS[data.action]
    if review.status == new_status:
        raise HTTPException(400, f"Review is already {new_status}")
    note = _clean(data.note)
    if data.action != "approve" and (not note or len(note) < 5):
        raise HTTPException(400, f"Please give a reason (min 5 characters) to {data.action} a review")

    old = review.status
    review.status = new_status
    review.moderated_by = user.id
    review.moderation_note = note
    audit(db, user, f"review_{new_status}", "review", review.id, {"status": old},
          {"status": new_status, "note": note})
    db.commit()
    db.refresh(review)
    return review

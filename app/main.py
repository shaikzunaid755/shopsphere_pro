import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.routers import auth, users, registration, profile, addresses, categories, brands, products, variants, product_files, attributes
app = FastAPI(
    title="ShopSphere Pro API",
    description="E-commerce backend - FastAPI + MySQL + SQLAlchemy. 50 modules, JWT auth and role based access.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # shorter error list than fastapi default, easier to read in postman
    errors = []
    for e in exc.errors():
        field = ".".join(str(x) for x in e["loc"] if x != "body")
        errors.append({"field": field, "message": e["msg"]})
    return JSONResponse(status_code=422, content={"detail": "Validation failed", "errors": errors})


@app.exception_handler(ValueError)
async def value_error(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})

routers = [
    auth.router,
    users.router,
    registration.router,
    profile.router,
    addresses.router,
    categories.router,
    brands.router,
    products.router,
    variants.router,
    product_files.router,
    attributes.router,
]
for r in routers:
    app.include_router(r)


@app.get("/")
def root():
    return {"message": "ShopSphere Pro API is running", "docs": "/docs"}

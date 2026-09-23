"""Main FastAPI Application"""
from fastapi import FastAPI, HTTPException, status, Depends, Query, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pydantic import BaseModel, Field
import uvicorn
import os
import io
import json
import pandas as pd

from .config import settings
from .database import (
    connect_to_postgres, close_postgres_connection, db, ObjectId,
    UserDB, ProductDB, OrderDB, TransactionLogDB, InventoryDB, DashboardDB,
    get_provider_scopes
)
from .security import (
    create_access_token, get_current_user, duplicate_prevention,
    db_rate_limiter, validate_stock_availability, idempotency_handler
)
from .models import (
    UserCreate, User, ProductCreate, Product, ProductUpdate, OrderCreate, Order, LoginRequest,
    UserRole, OrderStatus, PaymentStatus, RestockRequest, PaymentRequest
)
from .data_loader import load_excel_data, seed_database
from typing import Optional, List
import uuid
import bcrypt

COMMISSION_RATE = 0.08
VAT_RATE = 0.07
TAX_LABEL = "VAT"
DISCOUNT_RULES = {
    "SAVE10": 0.10,
    "SAVE15": 0.15,
    "VIP20": 0.20,
}

def round_money(value: float) -> float:
    return round(value + 1e-9, 2)

def resolve_discount_rate(subtotal: float, discount_code: Optional[str]) -> tuple[Optional[str], float]:
    normalized_code = (discount_code or "").strip().upper() or None
    if normalized_code and normalized_code in DISCOUNT_RULES:
        return normalized_code, DISCOUNT_RULES[normalized_code]
    if subtotal >= 1000:
        return normalized_code, 0.12
    if subtotal >= 500:
        return normalized_code, 0.07
    if subtotal >= 200:
        return normalized_code, 0.03
    return normalized_code, 0.0

def calculate_order_pricing(items: List[dict], discount_code: Optional[str] = None) -> dict:
    subtotal = round_money(sum(item["quantity"] * item["price_at_purchase"] for item in items))
    normalized_code, discount_rate = resolve_discount_rate(subtotal, discount_code)
    discount_amount = round_money(subtotal * discount_rate)
    taxable_amount = round_money(max(subtotal - discount_amount, 0))
    commission_amount = round_money(taxable_amount * COMMISSION_RATE)
    tax_amount = round_money(taxable_amount * VAT_RATE)
    grand_total = round_money(taxable_amount + tax_amount)
    provider_net_amount = round_money(taxable_amount - commission_amount)

    return {
        "currency": "USD",
        "subtotal": subtotal,
        "discount_code": normalized_code,
        "discount_rate": discount_rate,
        "discount_amount": discount_amount,
        "taxable_amount": taxable_amount,
        "commission_rate": COMMISSION_RATE,
        "commission_amount": commission_amount,
        "tax_label": TAX_LABEL,
        "tax_rate": VAT_RATE,
        "tax_amount": tax_amount,
        "grand_total": grand_total,
        "provider_net_amount": provider_net_amount,
        "platform_fee_amount": commission_amount,
    }

def build_invoice_metadata() -> dict:
    issued_at = datetime.utcnow()
    return {
        "invoice_number": f"INV-{issued_at.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}",
        "issued_at": issued_at,
        "currency": "USD",
    }

class AdminProfilePayload(BaseModel):
    name: str
    email: str
    role: str = "user"
    phone: Optional[str] = ""
    address: Optional[str] = ""

class AdminProductPayload(BaseModel):
    name: str
    sku: str
    category: str
    price: float
    stock: int = 0
    featured: int = 1
    description: Optional[str] = ""
    provider_id: Optional[str] = None

class AdminOrderItemPayload(BaseModel):
    productId: str
    name: Optional[str] = ""
    quantity: int = Field(gt=0)
    price: float = Field(ge=0)

class AdminOrderPayload(BaseModel):
    profile_id: Optional[str] = None
    user_name: str
    status: str = "Pending"
    total: float = Field(ge=0)
    items: List[AdminOrderItemPayload] = Field(default_factory=list)
    notes: Optional[str] = ""

class AdminCheckoutPayload(BaseModel):
    profile_id: str
    items: List[AdminOrderItemPayload] = Field(default_factory=list)
    notes: Optional[str] = ""

def parse_object_id(value: str, field_name: str) -> ObjectId:
    try:
        return ObjectId(str(value))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field_name}"
        )

def normalize_order_status(value: Optional[str]) -> str:
    normalized = (value or "pending").strip().lower()
    allowed_statuses = {"pending", "confirmed", "shipped", "completed", "cancelled", "packed"}
    return normalized if normalized in allowed_statuses else "pending"

def serialize_profile(user: dict) -> dict:
    return {
        "id": str(user["_id"]),
        "name": user.get("username") or user.get("name") or user.get("email", "").split("@")[0],
        "email": user.get("email", ""),
        "role": str(user.get("role", "user")).title(),
        "phone": user.get("phone", ""),
        "address": user.get("address", ""),
    }

def serialize_product(product: dict) -> dict:
    return {
        "id": str(product["_id"]),
        "name": product.get("name", ""),
        "sku": product.get("sku", ""),
        "category": product.get("category", ""),
        "price": float(product.get("price", 0) or 0),
        "stock": int(product.get("stock", 0) or 0),
        "featured": int(product.get("featured", 1) or 0),
        "description": product.get("description", "") or "",
        "provider_id": product.get("provider_id"),
    }

def serialize_order(order: dict, user_lookup: dict, product_lookup: dict) -> dict:
    items = []
    for item in order.get("items", []):
        product_id = str(item.get("product_id", ""))
        product = product_lookup.get(product_id, {})
        items.append({
            "productId": product_id,
            "name": item.get("name") or product.get("name", "Unknown product"),
            "quantity": int(item.get("quantity", 0) or 0),
            "price": float(item.get("price_at_purchase", 0) or 0),
        })

    user_id = order.get("user_id")
    profile = user_lookup.get(str(user_id)) if user_id else None

    return {
        "id": str(order["_id"]),
        "profile_id": str(user_id) if user_id else None,
        "user_name": order.get("user_name") or (profile.get("name") if profile else "Unknown user"),
        "status": normalize_order_status(order.get("status")).title(),
        "total": float(order.get("total_amount", 0) or 0),
        "items": items,
        "notes": order.get("notes", "") or "",
    }

async def require_admin_user(current_user: str) -> dict:
    user = await UserDB.get_user_by_id(current_user)
    if not user or user.get("role") != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return user

async def enforce_admin_access(current_user: str) -> dict:
    await db_rate_limiter.check_circuit_breaker()
    if not await db_rate_limiter.check_user_rate(current_user):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded"
        )
    return await require_admin_user(current_user)

async def build_admin_studio_snapshot() -> dict:
    users = await db.db["users"].find({}).sort("created_at", -1).to_list(None)
    products = await db.db["products"].find({}).sort("updated_at", -1).to_list(None)
    orders = await db.db["orders"].find({}).sort("created_at", -1).to_list(None)

    serialized_profiles = [serialize_profile(user) for user in users]
    user_lookup = {profile["id"]: profile for profile in serialized_profiles}
    product_lookup = {str(product["_id"]): product for product in products}

    return {
        "profiles": serialized_profiles,
        "products": [serialize_product(product) for product in products],
        "orders": [serialize_order(order, user_lookup, product_lookup) for order in orders],
    }

def normalize_export_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, ObjectId):
        return str(value)
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, (list, dict)):
        return json.dumps(value, default=str, ensure_ascii=False)
    return value

def flatten_export_document(document: dict) -> dict:
    return {
        ("id" if key == "_id" else key): normalize_export_value(value)
        for key, value in document.items()
    }

def build_order_item_rows(orders: list[dict]) -> list[dict]:
    rows = []
    for order in orders:
        order_id = str(order.get("_id", ""))
        for index, item in enumerate(order.get("items", []), start=1):
            rows.append({
                "order_id": order_id,
                "line": index,
                "product_id": item.get("product_id", ""),
                "name": item.get("name", ""),
                "quantity": item.get("quantity", 0),
                "price_at_purchase": item.get("price_at_purchase", 0),
                "line_total": float(item.get("quantity", 0) or 0) * float(item.get("price_at_purchase", 0) or 0),
            })
    return rows

def dataframe_from_documents(documents: list[dict]) -> pd.DataFrame:
    if not documents:
        return pd.DataFrame([{"message": "No records"}])
    return pd.DataFrame([flatten_export_document(document) for document in documents])

async def create_excel_export(collections: dict[str, list[dict]]) -> io.BytesIO:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for sheet_name, documents in collections.items():
            safe_sheet_name = sheet_name[:31]
            dataframe_from_documents(documents).to_excel(writer, sheet_name=safe_sheet_name, index=False)

        if "orders" in collections:
            pd.DataFrame(build_order_item_rows(collections["orders"]) or [{"message": "No order items"}]).to_excel(
                writer,
                sheet_name="order_items",
                index=False,
            )

    buffer.seek(0)
    return buffer

def excel_download_response(buffer: io.BytesIO, filename: str) -> StreamingResponse:
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

# ===================== LIFESPAN =====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await connect_to_postgres()
    print("🚀 FastAPI Server Started")
    
    # Auto-load Excel data on startup
    try:
        # Check if products already exist
        existing_products = await db.db["products"].count_documents({})
        if existing_products == 0:
            print("📊 No products found in database, loading from Excel...")
            
            # Try multiple possible paths (from /app working directory)
            possible_paths = [
                "backend/Adidas US Sales Datasets.xlsx",        # In Docker
                "Adidas US Sales Datasets.xlsx",                # In backend/ directory
                "../Adidas US Sales Datasets.xlsx",
                "../../Adidas US Sales Datasets.xlsx",
            ]
            
            excel_path = None
            for path in possible_paths:
                print(f"  🔍 Checking: {path}")
                if os.path.exists(path):
                    excel_path = path
                    print(f"  ✓ Found!")
                    break
            
            if excel_path:
                print(f"  📥 Loading from: {excel_path}")
                products = await load_excel_data(excel_path)
                if products:
                    result = await db.db["products"].insert_many(products)
                    print(f"✅ Loaded {len(result.inserted_ids)} products from Excel!")
                else:
                    print(f"❌ No products extracted from Excel")
            else:
                print(f"❌ Excel file not found")
                print(f"   CWD: {os.getcwd()}")
        else:
            print(f"✅ Database already has {existing_products} products")
    except Exception as e:
        import traceback
        print(f"❌ Error auto-loading Excel: {str(e)}")
        traceback.print_exc()
    
    yield
    # Shutdown
    await close_postgres_connection()
    print("🛑 FastAPI Server Stopped")

# ===================== CREATE APP =====================

app = FastAPI(
    title="E-Commerce Admin/Provider/User API",
    description="Multi-vendor e-commerce system with stock protection & rate limiting",
    version="1.0.0",
    lifespan=lifespan
)

# ===================== CORS =====================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===================== AUTHENTICATION ROUTES =====================

@app.post("/api/auth/register")
async def register(user: UserCreate):
    """Register new user"""
    # Check if email exists
    existing = await UserDB.get_user_by_email(user.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Hash password
    hashed_pw = bcrypt.hashpw(user.password.encode(), bcrypt.gensalt())
    
    user_data = {
        "email": user.email,
        "username": user.username,
        "password_hash": hashed_pw.decode(),
        "role": user.role,
        "is_active": True
    }
    
    user_id = await UserDB.create_user(user_data)
    
    # Create access token
    access_token = create_access_token({"sub": user_id})
    
    return {
        "user_id": user_id,
        "access_token": access_token,
        "token_type": "bearer"
    }

@app.post("/api/auth/login")
async def login(credentials: LoginRequest):
    """Login user"""
    print(f"🔍 Login attempt: email={credentials.email}")
    
    user = await UserDB.get_user_by_email(credentials.email)
    print(f"  User found: {bool(user)}")
    
    if not user:
        print(f"  ❌ User not found")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials"
        )
    
    try:
        password_hash = user["password_hash"]
        is_valid = bcrypt.checkpw(
            credentials.password.encode(), 
            password_hash.encode()
        )
        print(f"  Password valid: {is_valid}")
        
        if not is_valid:
            print(f"  ❌ Password mismatch")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials"
            )
    except Exception as e:
        print(f"  ❌ Password check error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials"
        )
    
    access_token = create_access_token({"sub": str(user["_id"])})
    print(f"  ✅ Login successful, token created")
    
    return {
        "user_id": str(user["_id"]),
        "access_token": access_token,
        "token_type": "bearer"
    }

# ===================== ADMIN DASHBOARD ROUTES =====================

@app.get("/api/admin/dashboard")
async def admin_dashboard(current_user: str = Depends(get_current_user)):
    """Get admin dashboard (SKU summary + sales overview)"""
    await db_rate_limiter.check_circuit_breaker()
    
    if not await db_rate_limiter.check_user_rate(current_user):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded"
        )
    
    # Get all products (admin can see all)
    from .database import db
    products = await db.db["products"].find({}).to_list(None)
    
    # Calculate totals
    total_products = len(products)
    total_stock = sum(p.get("stock", 0) for p in products)
    
    # Get all orders
    orders = await db.db["orders"].find({}).to_list(None)
    total_revenue = sum(o.get("total_amount", 0) for o in orders)
    total_orders = len(orders)
    
    # SKU Summary
    sku_summary = [
        {
            "sku": p.get("sku"),
            "product_name": p.get("name"),
            "provider": p.get("provider_id"),
            "stock": p.get("stock", 0),
            "price": p.get("price"),
            "total_value": p.get("stock", 0) * p.get("price", 0)
        }
        for p in products
    ]
    
    return {
        "total_products": total_products,
        "total_stock": total_stock,
        "total_revenue": total_revenue,
        "total_orders": total_orders,
        "sku_summary": sku_summary
    }

@app.get("/api/admin/inventory-status")
async def admin_inventory_status(current_user: str = Depends(get_current_user)):
    """Get inventory status with alerts"""
    from .database import db
    
    products = await db.db["products"].find({}).to_list(None)
    
    # Categorize products
    critical_stock = [p for p in products if p.get("stock", 0) == 0]
    low_stock = [p for p in products if 0 < p.get("stock", 0) < 5]
    good_stock = [p for p in products if p.get("stock", 0) >= 5]
    
    return {
        "critical_stock": {
            "count": len(critical_stock),
            "items": critical_stock
        },
        "low_stock": {
            "count": len(low_stock),
            "items": low_stock
        },
        "good_stock": {
            "count": len(good_stock)
        }
    }

@app.get("/api/admin/studio/bootstrap")
async def admin_studio_bootstrap(current_user: str = Depends(get_current_user)):
    """Admin studio bootstrap data backed by MongoDB."""
    await enforce_admin_access(current_user)
    return await build_admin_studio_snapshot()

@app.get("/api/admin/export-excel")
async def admin_export_excel(current_user: str = Depends(get_current_user)):
    """Download all database collections as an Excel workbook."""
    await enforce_admin_access(current_user)

    collection_rows = await db.pool.fetch(
        "SELECT DISTINCT collection FROM app_documents ORDER BY collection"
    )
    collections = {}
    for row in collection_rows:
        collection_name = row["collection"]
        collections[collection_name] = await db.db[collection_name].find({}).to_list(None)

    if not collections:
        collections = {"empty": []}

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    buffer = await create_excel_export(collections)
    return excel_download_response(buffer, f"export_all_data_{timestamp}.xlsx")

@app.post("/api/admin/studio/profiles")
async def admin_create_profile(
    profile: AdminProfilePayload,
    current_user: str = Depends(get_current_user)
):
    """Create a new profile in the users collection."""
    await enforce_admin_access(current_user)

    existing_user = await db.db["users"].find_one({"email": profile.email})
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already exists"
        )

    password_hash = bcrypt.hashpw(uuid.uuid4().hex.encode(), bcrypt.gensalt()).decode()
    user_data = {
        "email": profile.email.strip(),
        "username": profile.name.strip(),
        "password_hash": password_hash,
        "role": profile.role.strip().lower() or "user",
        "phone": (profile.phone or "").strip(),
        "address": (profile.address or "").strip(),
        "is_active": True,
        "managed_by_admin_studio": True,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }
    result = await db.db["users"].insert_one(user_data)
    created_user = await db.db["users"].find_one({"_id": result.inserted_id})
    return {"profile": serialize_profile(created_user)}

@app.put("/api/admin/studio/profiles/{profile_id}")
async def admin_update_profile(
    profile_id: str,
    profile: AdminProfilePayload,
    current_user: str = Depends(get_current_user)
):
    """Update a profile in the users collection."""
    await enforce_admin_access(current_user)
    profile_object_id = parse_object_id(profile_id, "profile_id")

    existing_user = await db.db["users"].find_one({"email": profile.email, "_id": {"$ne": profile_object_id}})
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already exists"
        )

    result = await db.db["users"].update_one(
        {"_id": profile_object_id},
        {"$set": {
            "email": profile.email.strip(),
            "username": profile.name.strip(),
            "role": profile.role.strip().lower() or "user",
            "phone": (profile.phone or "").strip(),
            "address": (profile.address or "").strip(),
            "updated_at": datetime.utcnow(),
        }}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")

    updated_user = await db.db["users"].find_one({"_id": profile_object_id})
    return {"profile": serialize_profile(updated_user)}

@app.delete("/api/admin/studio/profiles/{profile_id}")
async def admin_delete_profile(
    profile_id: str,
    current_user: str = Depends(get_current_user)
):
    """Delete a profile from the users collection."""
    await enforce_admin_access(current_user)
    if profile_id == current_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot delete the current admin profile"
        )

    result = await db.db["users"].delete_one({"_id": parse_object_id(profile_id, "profile_id")})
    if result.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    return {"status": "deleted"}

@app.post("/api/admin/studio/products")
async def admin_create_product(
    product: AdminProductPayload,
    current_user: str = Depends(get_current_user)
):
    """Create a product for the admin studio."""
    await enforce_admin_access(current_user)

    existing_product = await ProductDB.get_product_by_sku(product.sku)
    if existing_product:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SKU already exists"
        )

    product_data = {
        "name": product.name.strip(),
        "sku": product.sku.strip(),
        "category": product.category.strip(),
        "price": float(product.price),
        "stock": int(product.stock),
        "featured": int(product.featured),
        "description": (product.description or "").strip(),
        "provider_id": product.provider_id or current_user,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "stock_history": [],
    }
    result = await db.db["products"].insert_one(product_data)
    created_product = await db.db["products"].find_one({"_id": result.inserted_id})
    return {"product": serialize_product(created_product)}

@app.put("/api/admin/studio/products/{product_id}")
async def admin_update_product(
    product_id: str,
    product: AdminProductPayload,
    current_user: str = Depends(get_current_user)
):
    """Update a product for the admin studio."""
    await enforce_admin_access(current_user)
    product_object_id = parse_object_id(product_id, "product_id")

    existing_product = await db.db["products"].find_one({"sku": product.sku, "_id": {"$ne": product_object_id}})
    if existing_product:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SKU already exists"
        )

    result = await db.db["products"].update_one(
        {"_id": product_object_id},
        {"$set": {
            "name": product.name.strip(),
            "sku": product.sku.strip(),
            "category": product.category.strip(),
            "price": float(product.price),
            "stock": int(product.stock),
            "featured": int(product.featured),
            "description": (product.description or "").strip(),
            "provider_id": product.provider_id or current_user,
            "updated_at": datetime.utcnow(),
        }}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    updated_product = await db.db["products"].find_one({"_id": product_object_id})
    return {"product": serialize_product(updated_product)}

@app.delete("/api/admin/studio/products/{product_id}")
async def admin_delete_product(
    product_id: str,
    current_user: str = Depends(get_current_user)
):
    """Delete a product from the admin studio."""
    await enforce_admin_access(current_user)
    result = await db.db["products"].delete_one({"_id": parse_object_id(product_id, "product_id")})
    if result.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    return {"status": "deleted"}

@app.post("/api/admin/studio/orders")
async def admin_create_order(
    order: AdminOrderPayload,
    current_user: str = Depends(get_current_user)
):
    """Create an order without inventory mutation."""
    await enforce_admin_access(current_user)

    order_items = [
        {
            "product_id": item.productId,
            "name": item.name or "",
            "quantity": int(item.quantity),
            "price_at_purchase": float(item.price),
        }
        for item in order.items
    ]

    provider_id = None
    if order_items:
        first_product = await ProductDB.get_product_by_id(order_items[0]["product_id"])
        provider_id = first_product.get("provider_id") if first_product else None

    order_data = {
        "user_id": order.profile_id,
        "user_name": order.user_name.strip(),
        "provider_id": provider_id,
        "items": order_items,
        "status": normalize_order_status(order.status),
        "payment_status": PaymentStatus.PENDING,
        "total_amount": float(order.total),
        "notes": (order.notes or "").strip(),
        "shipping_address": "",
        "payment_method": "admin_manual",
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }
    result = await db.db["orders"].insert_one(order_data)
    created_order = await db.db["orders"].find_one({"_id": result.inserted_id})
    snapshot = await build_admin_studio_snapshot()
    order_lookup = {entry["id"]: entry for entry in snapshot["orders"]}
    return {"order": order_lookup[str(created_order["_id"])]}

@app.put("/api/admin/studio/orders/{order_id}")
async def admin_update_order(
    order_id: str,
    order: AdminOrderPayload,
    current_user: str = Depends(get_current_user)
):
    """Update an order without inventory mutation."""
    await enforce_admin_access(current_user)
    order_object_id = parse_object_id(order_id, "order_id")

    order_items = [
        {
            "product_id": item.productId,
            "name": item.name or "",
            "quantity": int(item.quantity),
            "price_at_purchase": float(item.price),
        }
        for item in order.items
    ]

    provider_id = None
    if order_items:
        first_product = await ProductDB.get_product_by_id(order_items[0]["product_id"])
        provider_id = first_product.get("provider_id") if first_product else None

    result = await db.db["orders"].update_one(
        {"_id": order_object_id},
        {"$set": {
            "user_id": order.profile_id,
            "user_name": order.user_name.strip(),
            "provider_id": provider_id,
            "items": order_items,
            "status": normalize_order_status(order.status),
            "total_amount": float(order.total),
            "notes": (order.notes or "").strip(),
            "updated_at": datetime.utcnow(),
        }}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    snapshot = await build_admin_studio_snapshot()
    order_lookup = {entry["id"]: entry for entry in snapshot["orders"]}
    return {"order": order_lookup[order_id]}

@app.delete("/api/admin/studio/orders/{order_id}")
async def admin_delete_order(
    order_id: str,
    current_user: str = Depends(get_current_user)
):
    """Delete an order from the admin studio."""
    await enforce_admin_access(current_user)
    result = await db.db["orders"].delete_one({"_id": parse_object_id(order_id, "order_id")})
    if result.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return {"status": "deleted"}

@app.post("/api/admin/studio/checkout")
async def admin_checkout(
    payload: AdminCheckoutPayload,
    current_user: str = Depends(get_current_user)
):
    """Create a real order and decrement inventory using MongoDB."""
    await enforce_admin_access(current_user)

    if not payload.items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cart is empty"
        )

    profile = await UserDB.get_user_by_id(payload.profile_id)
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found"
        )

    order_items = []
    provider_id = None
    for item in payload.items:
        product = await ProductDB.get_product_by_id(item.productId)
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product not found: {item.productId}"
            )
        if product.get("stock", 0) < item.quantity:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Insufficient stock for {product.get('name', item.productId)}"
            )

        provider_id = provider_id or product.get("provider_id")
        order_items.append({
            "product_id": item.productId,
            "name": product.get("name", item.name or ""),
            "quantity": int(item.quantity),
            "price_at_purchase": float(product.get("price", item.price)),
        })

    pricing = calculate_order_pricing(order_items)
    billing = build_invoice_metadata()
    order_data = {
        "user_id": payload.profile_id,
        "user_name": profile.get("username", ""),
        "provider_id": provider_id,
        "items": order_items,
        "status": OrderStatus.PENDING,
        "payment_status": PaymentStatus.PENDING,
        "total_amount": pricing["grand_total"],
        "notes": (payload.notes or "").strip(),
        "shipping_address": profile.get("address", ""),
        "payment_method": "admin_checkout",
        "pricing": pricing,
        "billing": billing,
        "created_by_admin": current_user,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }

    result = await db.db["orders"].insert_one(order_data)
    order_id = str(result.inserted_id)

    for item in order_items:
        stock_result = await ProductDB.update_product_stock(
            product_id=item["product_id"],
            quantity_change=-item["quantity"],
            reason=f"Admin checkout {order_id}"
        )
        if isinstance(stock_result, dict) and stock_result.get("error"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=stock_result["error"]
            )

    snapshot = await build_admin_studio_snapshot()
    order_lookup = {entry["id"]: entry for entry in snapshot["orders"]}
    return {"order": order_lookup[order_id]}

# ===================== PROVIDER ROUTES (Add Products & Services) =====================

@app.post("/api/provider/products")
async def provider_create_product(
    product: ProductCreate,
    current_user: str = Depends(get_current_user)
):
    """Provider: Create new product"""
    await db_rate_limiter.check_circuit_breaker()
    
    if not await db_rate_limiter.check_user_rate(current_user):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS)
    
    # Check if SKU exists
    existing = await ProductDB.get_product_by_sku(product.sku)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SKU already exists"
        )
    
    product_data = product.dict()
    product_data["provider_id"] = current_user
    
    product_id = await ProductDB.create_product(product_data)
    
    return {
        "product_id": product_id,
        "message": "Product created successfully"
    }

@app.get("/api/provider/products")
async def provider_get_products(current_user: str = Depends(get_current_user)):
    """Provider: Get own products"""
    products = await ProductDB.get_products_by_provider(current_user)
    return {"products": products}

@app.get("/api/provider/export-excel")
async def provider_export_excel(current_user: str = Depends(get_current_user)):
    """Download provider-scoped products and orders as an Excel workbook."""
    provider_scopes = await get_provider_scopes(current_user)
    products = await db.db["products"].find({"provider_id": {"$in": provider_scopes}}).to_list(None)
    orders = await db.db["orders"].find({"provider_id": {"$in": provider_scopes}}).to_list(None)

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    buffer = await create_excel_export({
        "products": products,
        "orders": orders,
    })
    return excel_download_response(buffer, f"provider_export_{timestamp}.xlsx")

@app.put("/api/provider/products/{product_id}")
async def provider_update_product(
    product_id: str,
    product: ProductUpdate,
    current_user: str = Depends(get_current_user)
):
    """Provider: Update own product"""
    await db_rate_limiter.check_circuit_breaker()

    if not await db_rate_limiter.check_user_rate(current_user):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded"
        )

    provider_scopes = await get_provider_scopes(current_user)
    existing_product = await ProductDB.get_product_by_id(product_id)
    if not existing_product or existing_product.get("provider_id") not in provider_scopes:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found"
        )

    if product.sku:
        sku_owner = await ProductDB.get_product_by_sku(product.sku)
        if sku_owner and str(sku_owner["_id"]) != product_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="SKU already exists"
            )

    updated = await ProductDB.update_product(product_id, current_user, product.dict())
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found"
        )

    return {"message": "Product updated successfully"}

@app.delete("/api/provider/products/{product_id}")
async def provider_delete_product(
    product_id: str,
    current_user: str = Depends(get_current_user)
):
    """Provider: Delete own product"""
    await db_rate_limiter.check_circuit_breaker()

    if not await db_rate_limiter.check_user_rate(current_user):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded"
        )

    provider_scopes = await get_provider_scopes(current_user)
    existing_product = await ProductDB.get_product_by_id(product_id)
    if not existing_product or existing_product.get("provider_id") not in provider_scopes:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found"
        )

    deleted = await ProductDB.delete_product(product_id, current_user)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found"
        )

    return {"message": "Product deleted successfully"}

@app.put("/api/provider/products/{product_id}/stock")
async def provider_restock_product(
    product_id: str,
    restock: RestockRequest,
    current_user: str = Depends(get_current_user)
):
    """Provider: Add stock to product"""
    await db_rate_limiter.check_circuit_breaker()

    if not await db_rate_limiter.check_user_rate(current_user):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded"
        )
    
    result = await ProductDB.update_product_stock(
        product_id=product_id,
        quantity_change=restock.quantity,
        reason=restock.reason or "Manual restock",
        provider_id=current_user
    )

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found"
        )
    
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    
    return {"message": f"Added {restock.quantity} units", "result": result}

@app.get("/api/provider/dashboard")
async def provider_dashboard(current_user: str = Depends(get_current_user)):
    """Provider: Get own dashboard"""
    dashboard = await DashboardDB.get_provider_dashboard(current_user)
    return dashboard

# ===================== USER SHOPPING ROUTES =====================

@app.get("/api/user/products")
async def user_browse_products(category: Optional[str] = None):
    """User: Browse products"""
    from .database import db
    
    query = {}
    if category:
        query["category"] = category
    
    products = await db.db["products"].find(query).to_list(None)
    
    # Convert ObjectId to string for JSON serialization
    for product in products:
        product["_id"] = str(product["_id"])
    
    return {"products": products}

@app.get("/api/user/search")
async def user_search_products(q: Optional[str] = None, category: Optional[str] = None):
    """User: Search products by name, SKU, or description"""
    from .database import db
    
    query = {}
    
    # Build search query
    if q:
        # Search in name, SKU, and description
        query["$or"] = [
            {"name": {"$regex": q, "$options": "i"}},
            {"sku": {"$regex": q, "$options": "i"}},
            {"description": {"$regex": q, "$options": "i"}}
        ]
    
    if category:
        query["category"] = category
    
    products = await db.db["products"].find(query).to_list(None)
    
    # Convert ObjectId to string for JSON serialization
    for product in products:
        product["_id"] = str(product["_id"])
    
    return {
        "query": q,
        "category": category,
        "count": len(products),
        "products": products
    }

@app.get("/api/user/products/{product_id}")
async def user_get_product(product_id: str):
    """User: Get product details"""
    product = await ProductDB.get_product_by_id(product_id)
    
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found"
        )
    
    return product

@app.post("/api/user/orders/preview")
async def user_preview_order(
    order: OrderCreate,
    current_user: str = Depends(get_current_user)
):
    """Preview billing summary before creating an order"""
    pricing = calculate_order_pricing(
        [item.dict() for item in order.items],
        order.discount_code
    )
    return {
        "pricing": pricing,
        "billing": {
            "currency": pricing["currency"],
            "tax_label": pricing["tax_label"],
        }
    }

@app.post("/api/user/orders")
async def user_create_order(
    order: OrderCreate,
    idempotency_key: Optional[str] = None,
    current_user: str = Depends(get_current_user)
):
    """
    User: Create order (with duplicate purchase prevention)
    
    ป้องกัน:
    1. Stock validation (ไม่ขายเกินจำนวน)
    2. Idempotency key (ถ้า request ซ้ำกันจะ return ผลเดิม)
    3. Rate limiting (ไม่ให้ซื้อเร็วเกินไป)
    """
    
    # === STEP 1: Check rate limiting & circuit breaker ===
    await db_rate_limiter.check_circuit_breaker()
    
    if not await db_rate_limiter.check_user_rate(current_user):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests - please wait before placing another order"
        )
    
    # === STEP 2: Generate idempotency key if not provided ===
    if not idempotency_key:
        idempotency_key = str(uuid.uuid4())
    
    # === STEP 3: Check if this request was already processed ===
    existing_result = await idempotency_handler.get_result(idempotency_key)
    if existing_result:
        return existing_result
    
    # === STEP 4: Check duplicate purchase from transaction log ===
    duplicate = await TransactionLogDB.check_duplicate_purchase(idempotency_key, current_user)
    if duplicate:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Duplicate purchase detected"
        )
    
    # === STEP 5: Check purchase rate (same product) ===
    for item in order.items:
        allowed = await duplicate_prevention.check_purchase_rate(
            current_user, item.product_id
        )
        if not allowed:
            await TransactionLogDB.log_transaction(
                user_id=current_user,
                product_id=item.product_id,
                quantity=item.quantity,
                idempotency_key=idempotency_key,
                status="failed",
                error_message="Purchase rate limit exceeded"
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Purchasing same product too quickly"
            )
    
    # === STEP 6: Validate stock for all items ===
    for item in order.items:
        if not await validate_stock_availability(item.product_id, item.quantity):
            await TransactionLogDB.log_transaction(
                user_id=current_user,
                product_id=item.product_id,
                quantity=item.quantity,
                idempotency_key=idempotency_key,
                status="failed",
                error_message="Insufficient stock"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Insufficient stock for product {item.product_id}"
            )
    
    # === STEP 7: Calculate total and create order ===
    try:
        pricing = calculate_order_pricing(
            [item.dict() for item in order.items],
            order.discount_code
        )
        billing = build_invoice_metadata()
        total_amount = pricing["grand_total"]
        
        # Get provider_id from first product
        first_product = await ProductDB.get_product_by_id(order.items[0].product_id)
        provider_id = first_product.get("provider_id")
        
        order_data = {
            "user_id": current_user,
            "provider_id": provider_id,
            "items": [item.dict() for item in order.items],
            "status": OrderStatus.PENDING,
            "payment_status": PaymentStatus.PENDING,
            "total_amount": total_amount,
            "shipping_address": order.shipping_address,
            "payment_method": order.payment_method,
            "discount_code": order.discount_code,
            "pricing": pricing,
            "billing": billing,
            "idempotency_key": idempotency_key
        }
        
        order_id = await OrderDB.create_order(order_data)
        
        # === STEP 8: Deduct stock ===
        for item in order.items:
            result = await ProductDB.update_product_stock(
                product_id=item.product_id,
                quantity_change=-item.quantity,
                reason=f"Order {order_id}"
            )
            
            if isinstance(result, dict) and "error" in result:
                raise Exception(f"Stock update failed: {result['error']}")
        
        # === STEP 9: Log successful transaction ===
        await TransactionLogDB.log_transaction(
            user_id=current_user,
            product_id=order.items[0].product_id,
            quantity=order.items[0].quantity,
            idempotency_key=idempotency_key,
            status="success"
        )
        
        result = {
            "order_id": order_id,
            "status": "created",
            "total_amount": total_amount,
            "pricing": pricing,
            "billing": billing,
            "idempotency_key": idempotency_key
        }
        
        # Store result for idempotency
        await idempotency_handler.store_result(idempotency_key, result)
        
        return result
        
    except Exception as e:
        await db_rate_limiter.record_db_error()
        await TransactionLogDB.log_transaction(
            user_id=current_user,
            product_id=order.items[0].product_id,
            quantity=order.items[0].quantity,
            idempotency_key=idempotency_key,
            status="failed",
            error_message=str(e)
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error processing order"
        )

@app.get("/api/user/orders")
async def user_get_orders(current_user: str = Depends(get_current_user)):
    """User: Get own orders"""
    orders = await OrderDB.get_orders_by_user(current_user)
    return {"orders": orders}

@app.get("/api/user/orders/{order_id}")
async def user_get_order_detail(
    order_id: str,
    current_user: str = Depends(get_current_user)
):
    """User: Get order details"""
    order = await OrderDB.get_order_by_id(order_id)
    
    if not order or order.get("user_id") != current_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found"
        )
    
    return order

@app.get("/api/user/orders/{order_id}/invoice")
async def get_order_invoice(
    order_id: str,
    current_user: str = Depends(get_current_user)
):
    """Get invoice and billing breakdown for a user's order"""
    order = await OrderDB.get_order_by_id(order_id)

    if not order or order.get("user_id") != current_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found"
        )

    return {
        "order_id": order_id,
        "status": order.get("status"),
        "payment_status": order.get("payment_status"),
        "billing": order.get("billing") or {},
        "pricing": order.get("pricing") or {
            "currency": "USD",
            "subtotal": order.get("total_amount", 0),
            "grand_total": order.get("total_amount", 0),
            "tax_label": TAX_LABEL,
        },
        "items": order.get("items", []),
        "shipping_address": order.get("shipping_address"),
        "payment_method": order.get("payment_method"),
    }
# ===================== PAYMENT ROUTES =====================

@app.post("/api/user/orders/{order_id}/payment")
async def process_payment(
    order_id: str,
    payment_info: PaymentRequest,
    current_user: str = Depends(get_current_user)
):
    """
    Process payment for order
    
    Payment validation:
    1. Check if order exists and belongs to user
    2. Validate credit card format
    3. Process payment (mock implementation)
    4. Update order status
    """
    try:
        # === Step 1: Get order ===
        order = await OrderDB.get_order_by_id(order_id)
        
        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found"
            )
        
        if order.get("user_id") != current_user:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Unauthorized access to order"
            )
        
        if order.get("payment_status") == "paid":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Order already paid"
            )
        
        # === Step 2: Validate card format ===
        if len(payment_info.card_number) < 16 or not payment_info.card_number.replace(" ", "").isdigit():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid card number"
            )
        
        if len(payment_info.cvv) != 3 or not payment_info.cvv.isdigit():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid CVV"
            )
        
        # === Step 3: Process payment (mock) ===
        # ⚠️ In production, use Stripe, PayPal, etc.
        payment_successful = True  # Mock: always success for demo
        
        if not payment_successful:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Payment declined. Please try another card."
            )
        
        # === Step 4: Update order status ===
        await db.db["orders"].update_one(
            {"_id": ObjectId(order_id)},
            {
                "$set": {
                    "payment_status": "paid",
                    "status": OrderStatus.CONFIRMED,
                    "updated_at": datetime.utcnow(),
                    "payment_date": datetime.utcnow(),
                    "card_last_four": payment_info.card_number[-4:]
                }
            }
        )
        
        return {
            "status": "success",
            "message": "Payment processed successfully",
            "order_id": order_id,
            "amount": (order.get("pricing") or {}).get("grand_total", order.get("total_amount")),
            "invoice_number": (order.get("billing") or {}).get("invoice_number"),
            "pricing": order.get("pricing"),
            "card_last_four": payment_info.card_number[-4:]
        }
    
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Payment processing error: {str(e)}"
        )

@app.get("/api/user/orders/{order_id}/payment-status")
async def get_payment_status(
    order_id: str,
    current_user: str = Depends(get_current_user)
):
    """Get payment status for order"""
    order = await OrderDB.get_order_by_id(order_id)
    
    if not order or order.get("user_id") != current_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found"
        )
    
    return {
        "order_id": order_id,
        "payment_status": order.get("payment_status"),
        "pricing": order.get("pricing"),
        "billing": order.get("billing"),
        "total_amount": order.get("total_amount"),
        "status": order.get("status")
    }

# ===================== HEALTH CHECK =====================

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "OK", "message": "E-Commerce API is running"}

@app.get("/api/status")
async def api_status():
    """Check database status and product count"""
    try:
        product_count = await db.db["products"].count_documents({})
        user_count = await db.db["users"].count_documents({})
        order_count = await db.db["orders"].count_documents({})
        
        return {
            "status": "OK",
            "database": "Connected",
            "products": product_count,
            "users": user_count,
            "orders": order_count
        }
    except Exception as e:
        return {
            "status": "Error",
            "database": "Disconnected",
            "error": str(e)
        }

# ===================== SEED DATA ENDPOINT =====================

@app.post("/api/admin/seed-excel")
async def admin_seed_excel(current_user: str = Depends(get_current_user)):
    """
    Admin: Seed database from Excel file
    Useful for reloading data or manual trigger
    """
    try:
        # Try multiple possible paths
        possible_paths = [
            "backend/Adidas US Sales Datasets.xlsx",
            "Adidas US Sales Datasets.xlsx",
            "../Adidas US Sales Datasets.xlsx",
            "../../Adidas US Sales Datasets.xlsx",
        ]
        
        excel_path = None
        for path in possible_paths:
            if os.path.exists(path):
                excel_path = path
                break
        
        if not excel_path:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Excel file not found. Tried: {', '.join(possible_paths)}"
            )
        
        # Load and insert
        products = await load_excel_data(excel_path)
        
        if not products:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No products loaded from Excel"
            )
        
        result = await db.db["products"].insert_many(products)
        
        return {
            "status": "success",
            "products_inserted": len(result.inserted_ids),
            "message": f"Successfully loaded {len(result.inserted_ids)} products from Excel"
        }
    
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error seeding data: {str(e)}"
        )

# ===================== STATIC FILES ROUTES =====================

frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
frontend_vue_dist_path = os.path.join(os.path.dirname(__file__), "..", "frontend-vue", "dist")
frontend_vue_assets_path = os.path.join(frontend_vue_dist_path, "assets")

if os.path.exists(frontend_vue_dist_path):
    app.mount(
        "/studio",
        StaticFiles(directory=frontend_vue_dist_path, html=True),
        name="flow_studio"
    )

if os.path.exists(frontend_vue_assets_path):
    app.mount(
        "/assets",
        StaticFiles(directory=frontend_vue_assets_path),
        name="flow_studio_assets"
    )

if os.path.exists(frontend_path):
    app.mount(
        "/frontend-assets",
        StaticFiles(directory=frontend_path),
        name="frontend_assets"
    )

@app.get("/")
async def serve_root():
    """Serve login page at root"""
    login_file = os.path.join(frontend_path, "login.html")
    if os.path.exists(login_file):
        return FileResponse(login_file, media_type="text/html")

    studio_file = os.path.join(frontend_vue_dist_path, "index.html")
    if os.path.exists(studio_file):
        return FileResponse(studio_file, media_type="text/html")

    raise HTTPException(status_code=404, detail="Not Found")

@app.get("/login")
async def serve_login_root():
    """Serve login page at /login"""
    login_file = os.path.join(frontend_path, "login.html")
    if os.path.exists(login_file):
        return FileResponse(login_file, media_type="text/html")
    raise HTTPException(status_code=404, detail="Not Found")

@app.get("/login.html")
async def serve_login():
    """Serve login page"""
    login_file = os.path.join(frontend_path, "login.html")
    if os.path.exists(login_file):
        return FileResponse(login_file, media_type="text/html")
    raise HTTPException(status_code=404, detail="Not Found")

@app.get("/user-store.html")
async def serve_user_store():
    """Serve user store page"""
    file = os.path.join(frontend_path, "user-store.html")
    if os.path.exists(file):
        return FileResponse(file, media_type="text/html")
    raise HTTPException(status_code=404, detail="Not Found")

@app.get("/admin-dashboard.html")
async def serve_admin_dashboard():
    """Serve admin dashboard"""
    file = os.path.join(frontend_path, "admin-dashboard.html")
    if os.path.exists(file):
        return FileResponse(file, media_type="text/html")
    raise HTTPException(status_code=404, detail="Not Found")

@app.get("/provider-panel.html")
async def serve_provider_panel():
    """Serve provider panel"""
    file = os.path.join(frontend_path, "provider-panel.html")
    if os.path.exists(file):
        return FileResponse(file, media_type="text/html")
    raise HTTPException(status_code=404, detail="Not Found")

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG
    )

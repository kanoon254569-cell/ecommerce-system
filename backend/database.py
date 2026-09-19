"""PostgreSQL-backed document storage and database operations."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
import json
import re
import uuid

import asyncpg

from .config import settings


class ObjectId(str):
    """Small compatibility type for the API's existing string ID contract."""

    def __new__(cls, value: str):
        normalized = str(value)
        if not re.fullmatch(r"[0-9a-fA-F]{24}", normalized):
            raise ValueError(f"Invalid object id: {value}")
        return str.__new__(cls, normalized)


def _new_id() -> str:
    return uuid.uuid4().hex[:24]


def _json_default(value: Any):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, ObjectId):
        return str(value)
    raise TypeError(f"Unsupported JSON value: {type(value)!r}")


def _restore_types(value: Any, key: str = ""):
    if isinstance(value, dict):
        return {item_key: _restore_types(item, item_key) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_restore_types(item, key) for item in value]
    if isinstance(value, str) and (key.endswith("_at") or key in {"timestamp", "payment_date"}):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return value


def _same(left: Any, right: Any) -> bool:
    return str(left) == str(right) if isinstance(left, (ObjectId, str)) or isinstance(right, (ObjectId, str)) else left == right


def _matches(document: dict, query: dict) -> bool:
    for field, expected in query.items():
        if field == "$or":
            if not any(_matches(document, option) for option in expected):
                return False
            continue

        actual = document.get(field)
        if isinstance(expected, dict):
            for operator, operand in expected.items():
                if operator == "$in" and not any(_same(actual, item) for item in operand):
                    return False
                if operator == "$ne" and _same(actual, operand):
                    return False
                if operator == "$gte" and (actual is None or actual < operand):
                    return False
                if operator == "$regex":
                    flags = re.IGNORECASE if expected.get("$options") == "i" else 0
                    if not re.search(str(operand), str(actual or ""), flags):
                        return False
                if operator == "$options":
                    continue
        elif not _same(actual, expected):
            return False
    return True


class PostgreSQLCursor:
    def __init__(self, collection, query: dict):
        self.collection = collection
        self.query = query
        self.sort_field = None
        self.sort_direction = 1

    def sort(self, field: str, direction: int = 1):
        self.sort_field = field
        self.sort_direction = direction
        return self

    async def to_list(self, length=None):
        documents = await self.collection._all()
        documents = [item for item in documents if _matches(item, self.query)]
        if self.sort_field:
            documents.sort(
                key=lambda item: item.get(self.sort_field),
                reverse=self.sort_direction < 0,
            )
        return documents if length is None else documents[:length]


class PostgreSQLCollection:
    def __init__(self, database, name: str):
        self.database = database
        self.name = name

    async def _all(self):
        rows = await self.database.pool.fetch(
            "SELECT document FROM app_documents WHERE collection = $1",
            self.name,
        )
        documents = []
        for row in rows:
            document = row["document"]
            if isinstance(document, str):
                document = json.loads(document)
            documents.append(_restore_types(document))
        return documents

    def find(self, query: Optional[dict] = None):
        return PostgreSQLCursor(self, query or {})

    async def find_one(self, query: Optional[dict] = None):
        for document in await self._all():
            if _matches(document, query or {}):
                return document
        return None

    async def count_documents(self, query: Optional[dict] = None):
        if not query:
            row = await self.database.pool.fetchrow(
                "SELECT COUNT(*) AS count FROM app_documents WHERE collection = $1",
                self.name,
            )
            return row["count"]
        return len([item for item in await self._all() if _matches(item, query)])

    async def insert_one(self, document: dict):
        document = dict(document)
        document.setdefault("_id", _new_id())
        await self.database.pool.execute(
            "INSERT INTO app_documents(collection, document) VALUES ($1, $2::jsonb)",
            self.name,
            json.dumps(document, default=_json_default),
        )
        return SimpleNamespace(inserted_id=document["_id"])

    async def insert_many(self, documents: List[dict]):
        inserted_ids = []
        async with self.database.pool.acquire() as connection:
            async with connection.transaction():
                for item in documents:
                    document = dict(item)
                    document.setdefault("_id", _new_id())
                    await connection.execute(
                        "INSERT INTO app_documents(collection, document) VALUES ($1, $2::jsonb)",
                        self.name,
                        json.dumps(document, default=_json_default),
                    )
                    inserted_ids.append(document["_id"])
        return SimpleNamespace(inserted_ids=inserted_ids)

    async def update_one(self, query: dict, changes: dict):
        document = await self.find_one(query)
        if not document:
            return SimpleNamespace(matched_count=0, modified_count=0)
        for field, value in changes.get("$set", {}).items():
            document[field] = value
        await self.database.pool.execute(
            "UPDATE app_documents SET document = $1::jsonb WHERE collection = $2 AND id = $3",
            json.dumps(document, default=_json_default),
            self.name,
            str(document["_id"]),
        )
        return SimpleNamespace(matched_count=1, modified_count=1)

    async def delete_one(self, query: dict):
        document = await self.find_one(query)
        if not document:
            return SimpleNamespace(deleted_count=0)
        result = await self.database.pool.execute(
            "DELETE FROM app_documents WHERE collection = $1 AND id = $2",
            self.name,
            str(document["_id"]),
        )
        return SimpleNamespace(deleted_count=int(result.split()[-1]))


class PostgreSQLDatabase:
    def __init__(self, pool):
        self.pool = pool

    def __getitem__(self, collection: str):
        return PostgreSQLCollection(self, collection)


class Database:
    pool = None
    db = None


db = Database()


async def connect_to_postgres():
    """Connect to PostgreSQL and create the document storage table."""
    db.pool = await asyncpg.create_pool(settings.DATABASE_URL, min_size=1, max_size=5)
    await db.pool.execute(
        """
        CREATE TABLE IF NOT EXISTS app_documents (
            collection TEXT NOT NULL,
            id TEXT GENERATED ALWAYS AS ((document->>'_id')) STORED,
            document JSONB NOT NULL,
            PRIMARY KEY (collection, id)
        )
        """
    )
    db.db = PostgreSQLDatabase(db.pool)
    print(f"✅ Connected to PostgreSQL: {settings.DATABASE_NAME}")


async def close_postgres_connection():
    if db.pool:
        await db.pool.close()
        print("❌ Disconnected from PostgreSQL")


connect_to_mongo = connect_to_postgres
close_mongo_connection = close_postgres_connection

# ===================== DATABASE OPERATIONS =====================

class UserDB:
    @staticmethod
    async def create_user(user_data: dict):
        """Create new user"""
        user_data["created_at"] = datetime.utcnow()
        user_data["updated_at"] = datetime.utcnow()
        result = await db.db["users"].insert_one(user_data)
        return str(result.inserted_id)
    
    @staticmethod
    async def get_user_by_email(email: str):
        """Get user by email"""
        return await db.db["users"].find_one({"email": email})
    
    @staticmethod
    async def get_user_by_id(user_id: str):
        """Get user by ID"""
        return await db.db["users"].find_one({"_id": ObjectId(user_id)})

async def get_provider_scopes(provider_id: str):
    """Resolve legacy provider ids that should be visible to this provider account."""
    scopes = [provider_id]
    user = await UserDB.get_user_by_id(provider_id)

    if user and user.get("role") == "provider":
        legacy_provider_emails = {
            "provider@ecommerce.local",
            "provider@example.com",
        }
        if user.get("email") in legacy_provider_emails:
            scopes.append("provider_001")

    return scopes

class ProductDB:
    @staticmethod
    async def update_product(product_id: str, provider_id: str, update_data: dict):
        provider_scopes = await get_provider_scopes(provider_id)
        clean_data = {k: v for k, v in update_data.items() if v is not None}
        clean_data["updated_at"] = datetime.utcnow()
        existing_product = await db.db["products"].find_one(
            {"_id": ObjectId(product_id), "provider_id": {"$in": provider_scopes}}
        )

        if not existing_product:
            return False

        result = await db.db["products"].update_one(
            {"_id": ObjectId(product_id), "provider_id": {"$in": provider_scopes}},
            {"$set": clean_data}
        )

        if "stock" in clean_data and clean_data["stock"] != existing_product.get("stock", 0):
            await InventoryDB.log_stock_change(
                product_id=product_id,
                provider_id=existing_product.get("provider_id"),
                old_stock=existing_product.get("stock", 0),
                new_stock=clean_data["stock"],
                quantity_changed=clean_data["stock"] - existing_product.get("stock", 0),
                reason="Manual edit from provider panel"
            )

        return result.matched_count > 0

    @staticmethod
    async def delete_product(product_id: str, provider_id: str):
        provider_scopes = await get_provider_scopes(provider_id)
        result = await db.db["products"].delete_one(
            {"_id": ObjectId(product_id), "provider_id": {"$in": provider_scopes}}
        )
        return result.deleted_count > 0
    @staticmethod
    async def create_product(product_data: dict):
        """Create new product"""
        product_data["created_at"] = datetime.utcnow()
        product_data["updated_at"] = datetime.utcnow()
        product_data["stock_history"] = []
        result = await db.db["products"].insert_one(product_data)
        return str(result.inserted_id)
    
    @staticmethod
    async def get_product_by_id(product_id: str):
        """Get product by ID"""
        return await db.db["products"].find_one({"_id": ObjectId(product_id)})
    
    @staticmethod
    async def get_product_by_sku(sku: str):
        """Get product by SKU"""
        return await db.db["products"].find_one({"sku": sku})
    
    @staticmethod
    async def get_products_by_provider(provider_id: str):
        """Get all products by provider"""
        provider_scopes = await get_provider_scopes(provider_id)
        products = await db.db["products"].find({"provider_id": {"$in": provider_scopes}}).to_list(None)
        for product in products:
            product["_id"] = str(product["_id"])
        return products
    
    @staticmethod
    async def update_product_stock(
        product_id: str,
        quantity_change: int,
        reason: str,
        provider_id: Optional[str] = None
    ):
        """
        Update product stock with logging
        quantity_change: positive (add), negative (remove)
        """
        query = {"_id": ObjectId(product_id)}
        if provider_id is not None:
            provider_scopes = await get_provider_scopes(provider_id)
            query["provider_id"] = {"$in": provider_scopes}

        product = await db.db["products"].find_one(query)
        
        if not product:
            return None
        
        old_stock = product["stock"]
        new_stock = old_stock + quantity_change
        
        if new_stock < 0:
            return {"error": "Stock cannot be negative", "code": "INSUFFICIENT_STOCK"}
        
        # Update stock
        updated = await db.db["products"].update_one(
            query,
            {"$set": {"stock": new_stock, "updated_at": datetime.utcnow()}}
        )
        
        # Log to inventory history
        await InventoryDB.log_stock_change(
            product_id=product_id,
            provider_id=product.get("provider_id"),
            old_stock=old_stock,
            new_stock=new_stock,
            quantity_changed=quantity_change,
            reason=reason
        )
        
        return {"old_stock": old_stock, "new_stock": new_stock, "success": True}

class OrderDB:
    @staticmethod
    async def create_order(order_data: dict):
        """Create new order with idempotency key"""
        
        # Generate idempotency key if not exists
        if not order_data.get("idempotency_key"):
            order_data["idempotency_key"] = str(uuid.uuid4())
        
        order_data["created_at"] = datetime.utcnow()
        order_data["updated_at"] = datetime.utcnow()
        
        result = await db.db["orders"].insert_one(order_data)
        return str(result.inserted_id)
    
    @staticmethod
    async def get_order_by_id(order_id: str):
        """Get order by ID"""
        return await db.db["orders"].find_one({"_id": ObjectId(order_id)})
    
    @staticmethod
    async def get_orders_by_user(user_id: str):
        """Get all orders by user"""
        return await db.db["orders"].find({"user_id": user_id}).to_list(None)
    
    @staticmethod
    async def get_orders_by_provider(provider_id: str):
        """Get all orders for a provider"""
        return await db.db["orders"].find({"provider_id": provider_id}).to_list(None)
    
    @staticmethod
    async def update_order_status(order_id: str, status: str):
        """Update order status"""
        await db.db["orders"].update_one(
            {"_id": ObjectId(order_id)},
            {"$set": {"status": status, "updated_at": datetime.utcnow()}}
        )

class TransactionLogDB:
    @staticmethod
    async def log_transaction(
        user_id: str,
        product_id: str,
        quantity: int,
        idempotency_key: str,
        status: str,
        error_message: Optional[str] = None
    ):
        """Log transaction for duplicate prevention"""
        log_data = {
            "user_id": user_id,
            "product_id": product_id,
            "quantity": quantity,
            "idempotency_key": idempotency_key,
            "timestamp": datetime.utcnow(),
            "status": status,
            "error_message": error_message
        }
        
        await db.db["transaction_logs"].insert_one(log_data)
    
    @staticmethod
    async def check_duplicate_purchase(idempotency_key: str, user_id: str):
        """
        Check if this purchase was already processed
        Returns: existing transaction if found, None otherwise
        """
        return await db.db["transaction_logs"].find_one({
            "idempotency_key": idempotency_key,
            "user_id": user_id,
            "status": "success"
        })
    
    @staticmethod
    async def get_user_purchases_last_minute(user_id: str):
        """Get user's purchases in the last minute (rate limiting check)"""
        one_minute_ago = datetime.utcnow() - timedelta(minutes=1)
        return await db.db["transaction_logs"].find({
            "user_id": user_id,
            "timestamp": {"$gte": one_minute_ago},
            "status": "success"
        }).to_list(None)

class InventoryDB:
    @staticmethod
    async def log_stock_change(
        product_id: str,
        provider_id: str,
        old_stock: int,
        new_stock: int,
        quantity_changed: int,
        reason: str,
        reference_id: Optional[str] = None
    ):
        """Log inventory changes"""
        log = {
            "product_id": product_id,
            "provider_id": provider_id,
            "action": "add" if quantity_changed > 0 else "remove",
            "quantity_changed": quantity_changed,
            "old_stock": old_stock,
            "new_stock": new_stock,
            "reference_id": reference_id,
            "timestamp": datetime.utcnow(),
            "reason": reason
        }
        
        await db.db["inventory_logs"].insert_one(log)
    
    @staticmethod
    async def get_inventory_history(product_id: str, days: int = 30):
        """Get inventory history for a product"""
        date_threshold = datetime.utcnow() - timedelta(days=days)
        return await db.db["inventory_logs"].find({
            "product_id": product_id,
            "timestamp": {"$gte": date_threshold}
        }).to_list(None)

class DashboardDB:
    @staticmethod
    async def get_provider_dashboard(provider_id: str):
        """Get dashboard summary for provider"""
        provider_scopes = await get_provider_scopes(provider_id)
        
        # Total products and stock
        products = await db.db["products"].find(
            {"provider_id": {"$in": provider_scopes}}
        ).to_list(None)
        
        total_products = len(products)
        total_stock = sum(p.get("stock", 0) for p in products)
        total_categories = len({
            (product.get("category") or "").strip()
            for product in products
            if (product.get("category") or "").strip()
        })
        
        # Low stock items (< 5)
        low_stock = [
            {
                "_id": str(product.get("_id")),
                "name": product.get("name"),
                "sku": product.get("sku"),
                "stock": product.get("stock", 0),
                "category": product.get("category"),
                "price": product.get("price", 0),
                "provider_id": product.get("provider_id"),
            }
            for product in products
            if product.get("stock", 0) < 5
        ]
        
        # Total revenue and orders
        orders = await db.db["orders"].find({
            "provider_id": {"$in": provider_scopes},
            "status": {"$ne": "cancelled"}
        }).to_list(None)
        
        total_revenue = sum(o.get("total_amount", 0) for o in orders)
        total_orders = len(orders)
        
        # Orders today
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        orders_today = len([o for o in orders if o.get("created_at", datetime.min) >= today_start])
        
        # Sales by product
        sales_by_product = []
        for product in products:
            product_sales = sum(
                sum(item.get("quantity", 0) for item in o.get("items", []) 
                    if item.get("product_id") == str(product.get("_id")))
                for o in orders
            )
            sales_by_product.append({
                "product": product.get("name"),
                "sales": product_sales,
                "revenue": product_sales * product.get("price", 0)
            })
        
        return {
            "total_products": total_products,
            "total_stock": total_stock,
            "total_categories": total_categories,
            "low_stock_items": low_stock,
            "total_revenue": total_revenue,
            "total_orders": total_orders,
            "orders_today": orders_today,
            "sales_by_product": sales_by_product
        }

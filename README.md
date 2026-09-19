# 🛍️ E-Commerce Multi-Vendor System

ระบบขายออนไลน์ครบวงจร พร้อมความปลอดภัยสูง ป้องกัน double purchase และ database flooding

## 📋 Features

### ✅ Admin Dashboard
- 📊 SKU Summary (รวมสต็อกทั้งหมด)
- 📈 Sales Overview (ยอดขายแบบรายวัน)
- 🚨 Inventory Alerts (เตือนสต็อกต่ำ)
- 📍 Inventory Status (สถานะคลังสินค้า)

### ✅ Provider Panel
- ➕ Add Products & Services
- 📦 Restock Management
- 📊 Personal Dashboard
- 📋 Order History

### ✅ User Store
- 🛒 Browse Products
- 💳 Place Orders
- 📜 Order History
- 🔒 Secure Checkout

### ✅ Security Features

#### 1️⃣ **Duplicate Purchase Prevention**
```
Mechanism:
- ใช้ Idempotency Key (request id)
- ถ้าส่ง request เดียวกันหลายครั้ง = result เหมือนเดิม
- Log ทุก transaction attempt
- ตรวจสอบ 1 นาที ห้ามซื้อสินค้าเดียวกันซ้ำ
```

#### 2️⃣ **Stock Validation**
```
ป้องกัน Overselling:
- ตรวจสอบสต็อกก่อนบันทึก order
- ขายได้แค่จำนวนที่มีอยู่
- Update stock ลดลงเมื่อมีการสั่งซื้อ
```

#### 3️⃣ **Rate Limiting (ป้องกัน DB Flooding)**
```
Strategies:
- 60 requests per minute per user
- 5 purchases per minute (same product)
- Circuit Breaker pattern (ถ้า DB error > 5 ครั้ง ปิดระบบชั่วคราว 30 วิ)
- Database connection pooling
```

#### 4️⃣ **Payment Safety**
```
- Payment status tracking
- Order status flow validation
- Transaction logging
```

## 🚀 Quick Start

### Prerequisites
- Python 3.8+
- PostgreSQL 14+
- pip

### Installation

```bash
# 1. Navigate to project directory
cd /Volumes/ketamine2jai/งาน/Web_Data/ecommerce-system

# 2. Run setup script
chmod +x setup.sh
./setup.sh

# 3. Create .env file
cp .env.example .env

# 4. Start PostgreSQL with Docker Compose
docker compose up -d postgres

# 5. Start FastAPI Server
cd backend
python -m uvicorn main:app --reload --port 8000
```

### Load Excel Data

```bash
# After starting PostgreSQL and server
python backend/data_loader.py '../Adidas US Sales Datasets.xlsx'

### Database Configuration

The application uses PostgreSQL. Set `DATABASE_URL` in `.env` or in your
deployment provider:

```ini
DATABASE_URL=postgresql://ecommerce:ecommerce@localhost:5432/ecommerce_db
DATABASE_NAME=ecommerce_db
SECRET_KEY=change-this-in-production
DEBUG=false
```

For Render, use the PostgreSQL **External Database URL** when connecting from
your local machine, or the **Internal Database URL** when the database and web
service are in the same Render workspace.

### Default Accounts

Run this after PostgreSQL is available to create or update the demo accounts:

```bash
python3 create_admin_user.py
```

```text
Admin:    admin@ecommerce.local / admin123
Provider: provider@ecommerce.local / Provider123
```

The script reads `DATABASE_URL` from the environment and updates existing
accounts safely.
```

## 📚 API Endpoints

### Authentication
```
POST   /api/auth/register          # Register new user
POST   /api/auth/login             # Login user
```

### Admin Routes
```
GET    /api/admin/dashboard        # Admin dashboard (SKU + Sales)
GET    /api/admin/inventory-status # Inventory alerts
```

### Provider Routes
```
POST   /api/provider/products      # Add new product
PUT    /api/provider/products/{id}/stock  # Restock
GET    /api/provider/dashboard     # Provider dashboard
```

### User Routes
```
GET    /api/user/products          # Browse products
GET    /api/user/products/{id}     # Product details
POST   /api/user/orders            # Create order (with idempotency)
GET    /api/user/orders            # Get user's orders
GET    /api/user/orders/{id}       # Order details
```

### Health Check
```
GET    /health                     # Server status
```

## 🔐 Request Headers

```json
// For protected endpoints
{
  "Authorization": "Bearer <your_jwt_token>"
}

// For idempotency (recommended for orders)
{
  "X-Idempotency-Key": "unique-key-123"
}
```

## 📊 Database Schema

### Collections
1. **users** - User accounts
2. **products** - Product catalog
3. **orders** - Customer orders
4. **transaction_logs** - All purchase attempts (for duplicate prevention)
5. **inventory_logs** - Stock change history

### Key Fields

#### Products
```json
{
  "_id": "product_123",
  "sku": "SKU-PRODUCT-NAME-001",
  "name": "Product Name",
  "price": 99.99,
  "stock": 100,
  "provider_id": "provider_001",
  "stock_history": []
}
```

#### Orders
```json
{
  "_id": "order_123",
  "user_id": "user_123",
  "provider_id": "provider_001",
  "items": [
    { "product_id": "...", "quantity": 2, "price_at_purchase": 99.99 }
  ],
  "status": "pending",
  "total_amount": 199.98,
  "idempotency_key": "uuid-123"
}
```

#### Transaction Logs
```json
{
  "_id": "transaction_123",
  "user_id": "user_123",
  "product_id": "product_123",
  "idempotency_key": "uuid-123",
  "status": "success",
  "timestamp": "2024-04-09T12:00:00Z"
}
```

## 🛡️ How Fraud Prevention Works

### Scenario: User tries to click "Buy" twice quickly

```
Request 1: POST /api/user/orders (idempotency_key: key-123)
  ✅ Check rate limit (OK)
  ✅ Check duplicate (not found)
  ✅ Validate stock (5 units available)
  ✅ Create order
  ✅ Deduct stock (5 - 1 = 4)
  ✅ Log transaction (success)
  ✅ Store idempotency result
  → Return order_id, total_amount

Request 2: POST /api/user/orders (idempotency_key: key-123) [sent 1 second later]
  ✅ Check rate limit (OK)
  ❌ Check duplicate (found in last minute)
  → Return: "Error: Purchase rate limit exceeded"

Alternative: If user sends same request (idempotency_key: key-123)
  ✅ Check idempotency cache
  → Return cached result (same order_id, amount)
```

### Scenario: Request flooding / DDoS

```
User sends 100 requests per second:
  1-60 requests: ✅ Processed
  61+ requests: ❌ Rate limit exceeded (429)
  
If DB errors detected (5+ failures):
  🚨 Circuit breaker opens
  → Return 503 Service Unavailable
  ⏱️ Wait 30 seconds
  → Circuit breaker closes, retry
```

### Scenario: Try to buy more than available stock

```
Product has 5 units, user tries to buy 10:
  ❌ Stock validation fails
  → Return: "Insufficient stock for product"
  → Order NOT created
  → Stock NOT deducted
```

## 📈 Monitoring & Logging

### Stock History
```python
# View stock changes for a product
GET /api/admin/inventory-logs?product_id=123&days=30
```

### Transaction History
```python
# Admin can view transaction attempts
GET /api/admin/transaction-logs
```

## 🔧 Configuration

Edit `.env` file to adjust:

```ini
# Rate limiting
REQUESTS_PER_MINUTE=60              # Total requests
PURCHASE_LIMIT_PER_MINUTE=5         # Same product purchases

# Security
SECRET_KEY=change-this!             # JWT secret
ALGORITHM=HS256

# Database
DATABASE_URL=postgresql://ecommerce:ecommerce@localhost:5432/ecommerce_db
DATABASE_NAME=ecommerce_db
```

## 🧪 Testing

### Test Duplicate Purchase Prevention
```bash
# Window 1: Start server
cd backend && python -m uvicorn main:app --reload

# Window 2: Run test script
python test_duplicate_prevention.py
```

## 🐛 Troubleshooting

### PostgreSQL Connection Error
```
Error: Failed to connect to PostgreSQL
Solution:
  1. Start PostgreSQL: docker compose up -d postgres
  2. Check DATABASE_URL in .env
  3. Verify PostgreSQL port (default: 5432)
```

### Rate Limit Too Strict
```
Adjust in .env:
REQUESTS_PER_MINUTE=120
PURCHASE_LIMIT_PER_MINUTE=10
```

### Stock Goes Negative
```
This shouldn't happen due to validation, but if it does:
- Check transaction logs
- Manually reset stock: db.products.update_one({'_id': _id}, {'$set': {'stock': 0}})
```

## 📝 Next Steps

1. Deploy to production server (AWS/Heroku)
2. Add payment gateway integration (Stripe/PayPal)
3. Implement email notifications
4. Create React/Vue frontends for all panels
5. Add comprehensive logging and monitoring
6. Setup backup strategy for PostgreSQL

## 📞 Support

For issues or questions, check:
- API Docs: http://localhost:8000/docs
- Transaction Logs: Database table `transaction_logs`
- Error Messages: Check terminal output

---

**System Status**: Ready for local testing ✅

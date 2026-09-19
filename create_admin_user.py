"""Create the demo admin, provider, and regular users in PostgreSQL."""
import asyncio
import json
import os
import uuid
from datetime import datetime

import asyncpg
import bcrypt
from dotenv import load_dotenv

load_dotenv()


def new_id():
    return uuid.uuid4().hex[:24]


async def create_user(connection, email, username, password, role):
    existing = await connection.fetchval(
        """
        SELECT document FROM app_documents
        WHERE collection = 'users' AND document->>'email' = $1
        """,
        email,
    )
    if existing:
        print(f"Already exists: {email}")
        return

    now = datetime.utcnow().isoformat()
    document = {
        "_id": new_id(),
        "email": email,
        "username": username,
        "password_hash": bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
        "role": role,
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }
    await connection.execute(
        "INSERT INTO app_documents(collection, document) VALUES ('users', $1::jsonb)",
        json.dumps(document),
    )
    print(f"Created {role}: {email} / {password}")


async def main():
    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql://ecommerce:ecommerce@localhost:5432/ecommerce_db",
    )
    pool = await asyncpg.create_pool(database_url)
    try:
        async with pool.acquire() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS app_documents (
                    collection TEXT NOT NULL,
                    id TEXT GENERATED ALWAYS AS ((document->>'_id')) STORED,
                    document JSONB NOT NULL,
                    PRIMARY KEY (collection, id)
                )
                """
            )
            await create_user(connection, "admin@example.com", "admin", "admin123", "admin")
            await create_user(connection, "provider@ecommerce.local", "provider", "Provider@123456", "provider")
            await create_user(connection, "user@ecommerce.local", "user", "User@123456", "user")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
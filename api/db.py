import os
import random
import string
from datetime import datetime, timedelta

import aiosqlite

DB_PATH = os.getenv("DB_PATH", "/app/data/articles.db")


async def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL UNIQUE,
                type TEXT NOT NULL CHECK(type IN ('telegram', 'browser')),
                group_id INTEGER NOT NULL REFERENCES groups(id),
                created_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                url TEXT NOT NULL,
                title TEXT NOT NULL,
                content_html TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS link_codes (
                code TEXT PRIMARY KEY,
                group_id INTEGER NOT NULL,
                expires_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS share_codes (
                code TEXT PRIMARY KEY,
                article_id INTEGER NOT NULL,
                owner_user_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_articles_user_id ON articles(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_users_group_id ON users(group_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_users_user_id ON users(user_id)")
        await db.commit()

    await _migrate()


async def _migrate() -> None:
    """Додає group_id до існуючих users без групи."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("PRAGMA table_info(users)") as cursor:
            columns = {row[1] for row in await cursor.fetchall()}

        if "group_id" not in columns:
            await db.execute("ALTER TABLE users ADD COLUMN group_id INTEGER")
            async with db.execute("SELECT id FROM users WHERE group_id IS NULL") as cursor:
                users = await cursor.fetchall()
            for (uid,) in users:
                cursor = await db.execute(
                    "INSERT INTO groups (created_at) VALUES (?)",
                    (datetime.utcnow().isoformat(),),
                )
                await db.execute(
                    "UPDATE users SET group_id = ? WHERE id = ?",
                    (cursor.lastrowid, uid),
                )
            await db.commit()


async def get_or_create_user(user_id: str, user_type: str) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)

        now = datetime.utcnow().isoformat()
        group_cursor = await db.execute("INSERT INTO groups (created_at) VALUES (?)", (now,))
        group_id = group_cursor.lastrowid
        await db.execute(
            "INSERT INTO users (user_id, type, group_id, created_at) VALUES (?, ?, ?, ?)",
            (user_id, user_type, group_id, now),
        )
        await db.commit()

        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            return dict(await cursor.fetchone())


async def save_article(user_id: str, url: str, title: str, content_html: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO articles (user_id, url, title, content_html, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, url, title, content_html, datetime.utcnow().isoformat()),
        )
        await db.commit()
        return cursor.lastrowid


async def get_article(article_id: int, user_id: str) -> dict | None:
    """Повертає статтю якщо вона належить до тієї ж групи що й user_id."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT a.* FROM articles a
            JOIN users u ON u.user_id = a.user_id
            WHERE a.id = ?
              AND u.group_id = (SELECT group_id FROM users WHERE user_id = ?)
            """,
            (article_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_user_history(user_id: str, limit: int = 10, offset: int = 0) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT a.id, a.title, a.url, a.created_at, u.type as source
            FROM articles a
            JOIN users u ON u.user_id = a.user_id
            WHERE u.group_id = (SELECT group_id FROM users WHERE user_id = ?)
            ORDER BY a.created_at DESC LIMIT ? OFFSET ?
            """,
            (user_id, limit, offset),
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]


async def count_user_articles(user_id: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT COUNT(*) FROM articles a
            JOIN users u ON u.user_id = a.user_id
            WHERE u.group_id = (SELECT group_id FROM users WHERE user_id = ?)
            """,
            (user_id,),
        ) as cursor:
            return (await cursor.fetchone())[0]


async def search_articles(user_id: str, query: str, limit: int = 10) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT a.id, a.title, a.url, a.created_at, u.type as source
            FROM articles a
            JOIN users u ON u.user_id = a.user_id
            WHERE u.group_id = (SELECT group_id FROM users WHERE user_id = ?)
              AND a.title LIKE ?
            ORDER BY a.created_at DESC LIMIT ?
            """,
            (user_id, f"%{query}%", limit),
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]


async def cleanup_expired_codes() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM link_codes WHERE expires_at < ?",
            (datetime.utcnow().isoformat(),),
        )
        await db.commit()
        return cursor.rowcount


async def create_share_code(article_id: int, user_id: str) -> str:
    article = await get_article(article_id, user_id)
    if not article:
        raise ValueError("article_not_found")

    code = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO share_codes (code, article_id, owner_user_id, created_at) VALUES (?, ?, ?, ?)",
            (code, article_id, user_id, datetime.utcnow().isoformat()),
        )
        await db.commit()
    return code


async def revoke_share_code(code: str, user_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM share_codes WHERE code = ? AND owner_user_id = ?",
            (code, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def claim_share_code(code: str, user_id: str, user_type: str) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT s.article_id, a.url, a.title, a.content_html "
            "FROM share_codes s JOIN articles a ON a.id = s.article_id "
            "WHERE s.code = ?",
            (code,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                raise ValueError("invalid_code")
            row = dict(row)

        await get_or_create_user(user_id, user_type)
        new_id = await save_article(user_id, row["url"], row["title"], row["content_html"])

        await db.execute("DELETE FROM share_codes WHERE code = ?", (code,))
        await db.commit()

    return {"id": new_id, "title": row["title"], "url": row["url"]}


async def create_link_code(user_id: str) -> str:
    code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    expires_at = (datetime.utcnow() + timedelta(minutes=10)).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT group_id FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                raise ValueError("user_not_found")
            group_id = row[0]

        await db.execute("DELETE FROM link_codes WHERE group_id = ?", (group_id,))
        await db.execute(
            "INSERT INTO link_codes (code, group_id, expires_at) VALUES (?, ?, ?)",
            (code, group_id, expires_at),
        )
        await db.commit()

    return code


async def preview_link(code: str, user_id: str, user_type: str) -> dict:
    """Повертає інформацію про злиття без змін у БД."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT group_id, expires_at FROM link_codes WHERE code = ?", (code,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                raise ValueError("invalid_code")
            source_group_id, expires_at = row
            if datetime.utcnow().isoformat() > expires_at:
                raise ValueError("expired_code")

        await get_or_create_user(user_id, user_type)

        async with db.execute("SELECT group_id FROM users WHERE user_id = ?", (user_id,)) as cursor:
            target_group_id = (await cursor.fetchone())[0]

        same_group = source_group_id == target_group_id

        async def count_users(group_id):
            async with db.execute(
                "SELECT COUNT(*) FROM users WHERE group_id = ?", (group_id,)
            ) as c:
                return (await c.fetchone())[0]

        async def count_articles(group_id):
            async with db.execute(
                "SELECT COUNT(*) FROM articles a JOIN users u ON u.user_id = a.user_id WHERE u.group_id = ?",
                (group_id,),
            ) as c:
                return (await c.fetchone())[0]

        source_users = await count_users(source_group_id)
        source_articles = await count_articles(source_group_id)

        if same_group:
            return {
                "already_same_group": True,
                "total_users": source_users,
                "total_articles": source_articles,
            }

        target_users = await count_users(target_group_id)
        target_articles = await count_articles(target_group_id)

        return {
            "already_same_group": False,
            "code_group_users": source_users,
            "code_group_articles": source_articles,
            "your_group_users": target_users,
            "your_group_articles": target_articles,
            "total_users": source_users + target_users,
            "total_articles": source_articles + target_articles,
        }


async def confirm_link(code: str, user_id: str, user_type: str) -> int:
    """Зливає групу коду з групою user_id. Повертає кількість об'єднаних юзерів."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT group_id, expires_at FROM link_codes WHERE code = ?", (code,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                raise ValueError("invalid_code")
            source_group_id, expires_at = row
            if datetime.utcnow().isoformat() > expires_at:
                raise ValueError("expired_code")

        await get_or_create_user(user_id, user_type)

        async with db.execute("SELECT group_id FROM users WHERE user_id = ?", (user_id,)) as cursor:
            target_group_id = (await cursor.fetchone())[0]

        if source_group_id == target_group_id:
            raise ValueError("same_group")

        # Переносимо всіх юзерів з target_group до source_group
        await db.execute(
            "UPDATE users SET group_id = ? WHERE group_id = ?",
            (source_group_id, target_group_id),
        )
        await db.execute("DELETE FROM groups WHERE id = ?", (target_group_id,))
        await db.execute("DELETE FROM link_codes WHERE code = ?", (code,))
        await db.commit()

        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE group_id = ?", (source_group_id,)
        ) as cursor:
            return (await cursor.fetchone())[0]

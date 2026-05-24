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
                lang TEXT NOT NULL DEFAULT 'en',
                username TEXT,
                is_banned INTEGER NOT NULL DEFAULT 0,
                last_active_at TEXT,
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
            CREATE TABLE IF NOT EXISTS failed_urls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                url TEXT NOT NULL,
                error TEXT NOT NULL,
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
        await db.execute("CREATE INDEX IF NOT EXISTS idx_articles_url ON articles(url)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_users_group_id ON users(group_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_users_user_id ON users(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_failed_urls_created ON failed_urls(created_at)")
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

        if "lang" not in columns:
            await db.execute("ALTER TABLE users ADD COLUMN lang TEXT NOT NULL DEFAULT 'en'")
            await db.commit()

        if "username" not in columns:
            await db.execute("ALTER TABLE users ADD COLUMN username TEXT")
            await db.commit()

        if "is_banned" not in columns:
            await db.execute("ALTER TABLE users ADD COLUMN is_banned INTEGER NOT NULL DEFAULT 0")
            await db.commit()

        if "last_active_at" not in columns:
            await db.execute("ALTER TABLE users ADD COLUMN last_active_at TEXT")
            await db.commit()


async def get_user_lang(user_id: str) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT lang FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else "en"


async def set_user_lang(user_id: str, lang: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET lang = ? WHERE user_id = ?", (lang, user_id))
        await db.commit()


async def get_or_create_user(user_id: str, user_type: str, lang: str = "en") -> dict:
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
            "INSERT INTO users (user_id, type, group_id, lang, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, user_type, group_id, lang, now),
        )
        await db.commit()

        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            return dict(await cursor.fetchone())


async def delete_article(article_id: int, user_id: str) -> bool:
    """Видаляє статтю якщо вона в тій самій групі що й user_id."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            DELETE FROM articles WHERE id = ?
              AND user_id IN (
                SELECT user_id FROM users
                WHERE group_id = (SELECT group_id FROM users WHERE user_id = ?)
              )
            """,
            (article_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_article_by_url(user_id: str, url: str) -> dict | None:
    """Повертає існуючу статтю з тим самим URL у групі user_id, якщо є."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT a.id, a.title, a.url, a.created_at FROM articles a
            JOIN users u ON u.user_id = a.user_id
            WHERE a.url = ?
              AND u.group_id = (SELECT group_id FROM users WHERE user_id = ?)
            LIMIT 1
            """,
            (url, user_id),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


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


async def get_article_any(article_id: int) -> dict | None:
    """Стаття за id без перевірки групи — лише для адмін-доступу."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM articles WHERE id = ?", (article_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def delete_article_any(article_id: int) -> bool:
    """Видаляє статтю за id без перевірки групи — лише для адмін-доступу."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM articles WHERE id = ?", (article_id,))
        await db.commit()
        return cursor.rowcount > 0


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
            ORDER BY a.created_at DESC
            """,
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
    q = query.lower()
    return [dict(r) for r in rows if q in r["title"].lower()][:limit]


async def touch_user(user_id: str, username: str | None = None) -> None:
    """Оновлює last_active_at (і username, якщо переданий) при активності юзера."""
    async with aiosqlite.connect(DB_PATH) as db:
        if username is not None:
            await db.execute(
                "UPDATE users SET last_active_at = ?, username = ? WHERE user_id = ?",
                (datetime.utcnow().isoformat(), username, user_id),
            )
        else:
            await db.execute(
                "UPDATE users SET last_active_at = ? WHERE user_id = ?",
                (datetime.utcnow().isoformat(), user_id),
            )
        await db.commit()


async def is_user_banned(user_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT is_banned FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return bool(row[0]) if row else False


async def set_user_banned(user_id: str, banned: bool) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE users SET is_banned = ? WHERE user_id = ?",
            (1 if banned else 0, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def count_users() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            return (await cursor.fetchone())[0]


async def list_users(limit: int = 10, offset: int = 0) -> list[dict]:
    """Список юзерів з кількістю статей у їхній групі, найактивніші зверху."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT u.user_id, u.type, u.group_id, u.lang, u.username,
                   u.is_banned, u.last_active_at, u.created_at,
                   (SELECT COUNT(*) FROM articles a WHERE a.user_id = u.user_id) AS article_count
            FROM users u
            ORDER BY COALESCE(u.last_active_at, u.created_at) DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]


async def get_admin_stats() -> dict:
    """Зведена статистика для адмін-панелі."""
    week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async def scalar(sql: str, params: tuple = ()) -> int:
            async with db.execute(sql, params) as c:
                return (await c.fetchone())[0]

        return {
            "total_users": await scalar("SELECT COUNT(*) FROM users"),
            "telegram_users": await scalar("SELECT COUNT(*) FROM users WHERE type = 'telegram'"),
            "browser_users": await scalar("SELECT COUNT(*) FROM users WHERE type = 'browser'"),
            "banned_users": await scalar("SELECT COUNT(*) FROM users WHERE is_banned = 1"),
            "active_7d": await scalar(
                "SELECT COUNT(*) FROM users WHERE last_active_at >= ?", (week_ago,)
            ),
            "total_articles": await scalar("SELECT COUNT(*) FROM articles"),
            "total_groups": await scalar("SELECT COUNT(*) FROM groups"),
            "failed_urls": await scalar("SELECT COUNT(*) FROM failed_urls"),
        }


async def log_failed_url(user_id: str, url: str, error: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO failed_urls (user_id, url, error, created_at) VALUES (?, ?, ?, ?)",
            (user_id, url, error, datetime.utcnow().isoformat()),
        )
        await db.commit()


async def count_failed_urls() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM failed_urls") as cursor:
            return (await cursor.fetchone())[0]


async def list_failed_urls(limit: int = 10, offset: int = 0) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, user_id, url, error, created_at FROM failed_urls
            ORDER BY created_at DESC LIMIT ? OFFSET ?
            """,
            (limit, offset),
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

        async with db.execute(
            """SELECT a.id FROM articles a
               JOIN users u ON u.user_id = a.user_id
               WHERE a.url = ? AND u.group_id = (SELECT group_id FROM users WHERE user_id = ?)""",
            (row["url"], user_id),
        ) as c:
            existing = await c.fetchone()

        if existing:
            new_id = existing[0]
        else:
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

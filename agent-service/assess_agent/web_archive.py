"""联网检索结果归档库。

按用户工作空间隔离；同一 URL 只保留一份正文（内容哈希变化时清空入库标记），
同一组检索指令按批次缓存，命中且未过期时直接复用，不再产生搜索请求。

归档条目同时可以转换成 Evidence，注入本次报告的 Corpus，使智能体可引用。
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import sqlite3
from threading import RLock

from .errors import AgentError
from .schemas import Evidence
from .web_contracts import clean_text

CHUNK_SIZE = 2200
MAX_EVIDENCE_CHUNKS = 12


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def public_entry(row) -> dict:
    """返回给上层（含 Java）的条目；正文只在需要时单独取。"""
    return {
        "id": row["id"], "url": row["url"], "title": row["title"],
        "siteName": row["site_name"], "host": row["host"], "school": row["school"],
        "site": row["site"], "topic": row["topic"], "topicLabel": row["topic_label"],
        "query": row["query"], "queryKey": row["query_key"], "category": row["category"],
        "snippet": row["snippet"], "contentChars": len(row["content"]),
        "source": row["source"], "authority": row["authority"],
        "rerankScore": row["rerank_score"], "publishedAt": row["published_at"],
        "fetchedAt": row["fetched_at"], "materialId": row["material_id"],
    }


def full_entry(row) -> dict:
    return {**public_entry(row), "content": row["content"]}


def entry_evidence(entry: dict, category: str = "REFERENCE") -> list[Evidence]:
    """把一份归档网页切成可引用证据。来源信息保留在证据内部，不进入正文。"""
    ident = entry.get("id") or "W-" + sha256(entry.get("url", "").encode()).hexdigest()[:16]
    title = clean_text(entry.get("title", ""), 200) or "学校官网公开信息"
    host = clean_text(entry.get("host", ""), 120)
    url = clean_text(entry.get("url", ""), 500)
    content = clean_text(entry.get("content", ""), CHUNK_SIZE * MAX_EVIDENCE_CHUNKS)
    if not content:
        content = clean_text(entry.get("snippet", ""), CHUNK_SIZE)
    if not content:
        return []
    header = f"【学校官网公开信息｜{title}｜{host}｜{url}｜归档 {ident}】\n"
    material_id = "web-" + ident.removeprefix("W-")
    category = category if category in ("DATA", "SPEECH", "REFERENCE") else "REFERENCE"
    output = []
    for index, offset in enumerate(range(0, len(content), CHUNK_SIZE), 1):
        part = content[offset:offset + CHUNK_SIZE]
        if not part.strip():
            continue
        location = f"官网 {host} · {title} · 片段 {index}"
        eid = "W-" + sha256(f"{ident}|{index}|{part}".encode()).hexdigest()[:16]
        output.append(Evidence(id=eid, material_id=material_id, name=title, category=category,
                               location=location, text=header + part, computed=False, parent_ids=[]))
    return output


class WebArchive:
    def __init__(self, path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        with closing(self.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""CREATE TABLE IF NOT EXISTS web_pages (
                id TEXT PRIMARY KEY, url TEXT NOT NULL, url_hash TEXT NOT NULL UNIQUE,
                content_hash TEXT NOT NULL, title TEXT NOT NULL, site_name TEXT NOT NULL DEFAULT '',
                host TEXT NOT NULL, school TEXT NOT NULL, site TEXT NOT NULL DEFAULT '',
                topic TEXT NOT NULL, topic_label TEXT NOT NULL DEFAULT '',
                query TEXT NOT NULL DEFAULT '', query_key TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT 'REFERENCE', snippet TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'search',
                authority INTEGER, rerank_score REAL, published_at TEXT,
                fetched_at TEXT NOT NULL, material_id TEXT NOT NULL DEFAULT '')""")
            connection.execute("""CREATE TABLE IF NOT EXISTS web_batches (
                query_key TEXT PRIMARY KEY, last_run TEXT NOT NULL, entry_count INTEGER NOT NULL)""")
            connection.execute("""CREATE TABLE IF NOT EXISTS web_batch_pages (
                query_key TEXT NOT NULL, entry_id TEXT NOT NULL,
                PRIMARY KEY (query_key, entry_id))""")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_web_pages_school ON web_pages(school)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_web_pages_material ON web_pages(material_id)")
            connection.commit()

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def save(self, page: dict) -> dict:
        """写入或更新一份网页。返回带 id / materialId / contentChanged 的条目。"""
        url = clean_text(page.get("url", ""), 8192)
        content = clean_text(page.get("content", ""), page.get("content_limit", 40000))
        if not url or not content.strip():
            raise AgentError("WEB_ARCHIVE_EMPTY", "归档内容为空，已跳过")
        url_hash = sha256(url.encode()).hexdigest()
        content_hash = sha256(content.encode()).hexdigest()
        ident = "W-" + url_hash[:16]
        fetched_at = now_iso()
        values = {
            "id": ident, "url": url, "url_hash": url_hash, "content_hash": content_hash,
            "title": clean_text(page.get("title", ""), 300) or "学校官网公开信息",
            "site_name": clean_text(page.get("site_name", ""), 200),
            "host": clean_text(page.get("host", ""), 200),
            "school": clean_text(page.get("school", ""), 100),
            "site": clean_text(page.get("site", ""), 200),
            "topic": clean_text(page.get("topic", ""), 60),
            "topic_label": clean_text(page.get("topic_label", ""), 100),
            "query": clean_text(page.get("query", ""), 300),
            "query_key": clean_text(page.get("query_key", ""), 40),
            "category": (page.get("category") if page.get("category") in ("DATA", "SPEECH", "REFERENCE") else "REFERENCE"),
            "snippet": clean_text(page.get("snippet", ""), 800),
            "content": content,
            "source": clean_text(page.get("source", "search"), 20) or "search",
            "authority": page.get("authority"), "rerank_score": page.get("rerank_score"),
            "published_at": clean_text(page.get("published_at", ""), 60) or None,
            "fetched_at": fetched_at,
        }
        with self._lock, closing(self.connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT content_hash, material_id FROM web_pages WHERE url_hash=?", (url_hash,)).fetchone()
            changed = bool(existing and existing["content_hash"] != content_hash)
            if existing:
                # Content changes invalidate the previously materialized file.
                material_id = "" if changed else existing["material_id"]
                connection.execute("""UPDATE web_pages SET content_hash=?, title=?, site_name=?, host=?,
                    school=?, site=?, topic=?, topic_label=?, query=?, query_key=?, category=?,
                    snippet=?, content=?, source=?, authority=?, rerank_score=?, published_at=?,
                    fetched_at=?, material_id=? WHERE url_hash=?""",
                    (content_hash, values["title"], values["site_name"], values["host"], values["school"],
                     values["site"], values["topic"], values["topic_label"], values["query"], values["query_key"],
                     values["category"], values["snippet"], content, values["source"], values["authority"],
                     values["rerank_score"], values["published_at"], fetched_at, material_id, url_hash))
            else:
                connection.execute("""INSERT INTO web_pages
                    (id,url,url_hash,content_hash,title,site_name,host,school,site,topic,topic_label,
                     query,query_key,category,snippet,content,source,authority,rerank_score,
                     published_at,fetched_at,material_id)
                    VALUES (:id,:url,:url_hash,:content_hash,:title,:site_name,:host,:school,:site,
                     :topic,:topic_label,:query,:query_key,:category,:snippet,:content,:source,
                     :authority,:rerank_score,:published_at,:fetched_at,'')""", values)
            row = connection.execute("SELECT * FROM web_pages WHERE url_hash=?", (url_hash,)).fetchone()
        return {**full_entry(row), "contentChanged": changed}

    def record_batch(self, query_key: str, entry_ids: list[str]):
        if not query_key:
            return
        with self._lock, closing(self.connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""INSERT INTO web_batches (query_key,last_run,entry_count) VALUES (?,?,?)
                ON CONFLICT(query_key) DO UPDATE SET last_run=excluded.last_run, entry_count=excluded.entry_count""",
                (query_key, now_iso(), len(entry_ids)))
            connection.execute("DELETE FROM web_batch_pages WHERE query_key=?", (query_key,))
            connection.executemany("INSERT OR IGNORE INTO web_batch_pages (query_key,entry_id) VALUES (?,?)",
                                   [(query_key, ident) for ident in dict.fromkeys(entry_ids)])
            connection.commit()

    def recent(self, query_key: str, days: int) -> list[dict]:
        """命中未过期的检索批次时返回归档正文，避免重复联网与重复计费。"""
        if not query_key or days <= 0:
            return []
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with closing(self.connect()) as connection:
            batch = connection.execute("SELECT last_run FROM web_batches WHERE query_key=?", (query_key,)).fetchone()
            if not batch or batch["last_run"] < cutoff:
                return []
            rows = connection.execute("""SELECT p.* FROM web_pages p
                JOIN web_batch_pages b ON b.entry_id=p.id WHERE b.query_key=?
                ORDER BY COALESCE(p.authority,0) DESC, COALESCE(p.rerank_score,0) DESC, p.fetched_at DESC""",
                (query_key,)).fetchall()
        return [full_entry(row) for row in rows]

    def get(self, entry_id: str) -> dict:
        with closing(self.connect()) as connection:
            row = connection.execute("SELECT * FROM web_pages WHERE id=?", (entry_id,)).fetchone()
        if not row:
            raise AgentError("WEB_ARCHIVE_NOT_FOUND", "归档条目不存在", 404)
        return full_entry(row)

    def list(self, school: str = "", topic: str = "", only_pending: bool = False, limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit), 200))
        clauses, params = [], []
        if school:
            clauses.append("school LIKE ?")
            params.append(f"%{school}%")
        if topic:
            clauses.append("topic = ?")
            params.append(topic)
        if only_pending:
            clauses.append("material_id = ''")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with closing(self.connect()) as connection:
            rows = connection.execute(f"SELECT * FROM web_pages{where} ORDER BY fetched_at DESC LIMIT ?",
                                      (*params, limit)).fetchall()
        return [public_entry(row) for row in rows]

    def pending(self, limit: int = 50) -> list[dict]:
        with closing(self.connect()) as connection:
            rows = connection.execute("SELECT * FROM web_pages WHERE material_id='' ORDER BY fetched_at DESC LIMIT ?",
                                      (max(1, min(int(limit), 200)),)).fetchall()
        return [full_entry(row) for row in rows]

    def mark_materialized(self, items: list[dict]):
        if not items:
            return
        with self._lock, closing(self.connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany("UPDATE web_pages SET material_id=? WHERE id=?",
                                   [(str(item.get("materialId", "")), str(item.get("entryId", "")))
                                    for item in items if item.get("entryId")])
            connection.commit()

    def delete(self, entry_id: str):
        with self._lock, closing(self.connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            deleted = connection.execute("DELETE FROM web_pages WHERE id=?", (entry_id,)).rowcount
            connection.execute("DELETE FROM web_batch_pages WHERE entry_id=?", (entry_id,))
            connection.commit()
        if not deleted:
            raise AgentError("WEB_ARCHIVE_NOT_FOUND", "归档条目不存在", 404)

    def stats(self) -> dict:
        with closing(self.connect()) as connection:
            total = connection.execute("SELECT COUNT(*) FROM web_pages").fetchone()[0]
            pending = connection.execute("SELECT COUNT(*) FROM web_pages WHERE material_id=''").fetchone()[0]
            schools = [row[0] for row in connection.execute(
                "SELECT DISTINCT school FROM web_pages WHERE school<>'' ORDER BY school LIMIT 20")]
            last = connection.execute("SELECT MAX(fetched_at) FROM web_pages").fetchone()[0]
        return {"total": total, "pending": pending, "schools": schools, "lastFetchedAt": last}

"""Storage manager for memdio memories.

Architecture — everything in one SQLite database:
  - FLAC audio blobs (lossless compressed)
  - FTS5 full-text search (word-level matching)
  - sqlite-vector embeddings (semantic similarity, COSINE distance)
  - Contradiction detection (superseded memories)
  - Dual timestamps (document_date + event_date)
  - Memory relations (extends, updates)
  - Proper indexes on all query paths

No FAISS. No separate index files. One .db file per user.
"""

import io
import json
import logging
import os
import re
import sqlite3
import uuid
from datetime import datetime, timedelta

import numpy as np

from memdio.core.validators import (
    validate_content,
    validate_memory_id,
    validate_query,
    validate_tags,
)

logger = logging.getLogger(__name__)

VECTOR_DIM = 384  # BAAI/bge-small-en-v1.5 output dimension

# Contradiction detection threshold — cosine similarity above this = likely same topic
SIMILARITY_THRESHOLD_CONTRADICTION = 0.85
# Relation detection — similarity in this range = extends
SIMILARITY_THRESHOLD_EXTENDS = 0.50


def _signal_to_flac_blob(signal: np.ndarray, sample_rate: int) -> bytes:
    """Encode signal as FLAC bytes in memory."""
    import soundfile as sf

    buf = io.BytesIO()
    sf.write(buf, signal, sample_rate, format="FLAC")
    return buf.getvalue()


def _flac_blob_to_signal(blob: bytes) -> tuple[np.ndarray, int]:
    """Decode FLAC bytes back to signal + sample_rate."""
    import soundfile as sf

    return sf.read(io.BytesIO(blob))


def _load_vector_extension(conn):
    """Load sqlite-vector extension if available."""
    try:
        import sqlite_vector
        ext_dir = os.path.join(os.path.dirname(sqlite_vector.__file__), "binaries", "vector")
        conn.enable_load_extension(True)
        conn.load_extension(ext_dir)
        return True
    except Exception as e:
        logger.debug("sqlite-vector not available: %s", e)
        return False


# --- Date extraction patterns ---

_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_RE_ABSOLUTE_DATE = re.compile(
    r'(?:(?P<month_name>' + '|'.join(_MONTH_NAMES.keys()) + r')\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?,?\s*(?P<year>\d{4}))'
    r'|(?:(?P<y2>\d{4})[/-](?P<m2>\d{1,2})[/-](?P<d2>\d{1,2}))'
    r'|(?:(?P<m3>\d{1,2})/(?P<d3>\d{1,2})/(?P<y3>\d{4}))',
    re.IGNORECASE,
)

_RE_RELATIVE_DATE = re.compile(
    r'(?P<ref>yesterday|last\s+(?:week|month|year)'
    r'|(?:(?P<num>\d+|two|three|four|five|six|seven|eight|nine|ten)\s+'
    r'(?P<unit>days?|weeks?|months?|years?)\s+ago))',
    re.IGNORECASE,
)

_WORD_NUMS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


def _extract_event_date(content: str, document_date: str | None = None) -> str | None:
    """Extract the most prominent event date from content.

    Handles absolute dates (March 15, 2025) and relative dates (last month, 2 weeks ago)
    resolved against document_date.

    Returns ISO date string (YYYY-MM-DD) or None.
    """
    # Try absolute dates first
    m = _RE_ABSOLUTE_DATE.search(content)
    if m:
        try:
            if m.group("month_name"):
                month = _MONTH_NAMES[m.group("month_name").lower()]
                day = int(m.group("day"))
                year = int(m.group("year"))
            elif m.group("y2"):
                year, month, day = int(m.group("y2")), int(m.group("m2")), int(m.group("d2"))
            else:
                month, day, year = int(m.group("m3")), int(m.group("d3")), int(m.group("y3"))
            return datetime(year, month, day).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            pass

    # Try relative dates (need document_date as anchor)
    if not document_date:
        return None

    # Parse document_date — handles "2023/05/30 (Tue) 19:30" and ISO formats
    anchor = None
    for fmt in ("%Y/%m/%d (%a) %H:%M", "%Y/%m/%d", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            anchor = datetime.strptime(document_date.split(" (")[0] if "(" in document_date else document_date.split("T")[0], fmt.split(" (")[0].split("T")[0])
            break
        except ValueError:
            continue
    if not anchor:
        # Try just the date portion
        date_part = document_date.split(" ")[0].split("(")[0].strip()
        for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
            try:
                anchor = datetime.strptime(date_part, fmt)
                break
            except ValueError:
                continue
    if not anchor:
        return None

    m = _RE_RELATIVE_DATE.search(content)
    if m:
        ref = m.group("ref").lower().strip()
        if ref == "yesterday":
            return (anchor - timedelta(days=1)).strftime("%Y-%m-%d")
        elif "last week" in ref:
            return (anchor - timedelta(weeks=1)).strftime("%Y-%m-%d")
        elif "last month" in ref:
            month = anchor.month - 1 or 12
            year = anchor.year if anchor.month > 1 else anchor.year - 1
            return datetime(year, month, min(anchor.day, 28)).strftime("%Y-%m-%d")
        elif "last year" in ref:
            return datetime(anchor.year - 1, anchor.month, min(anchor.day, 28)).strftime("%Y-%m-%d")
        elif m.group("num") and m.group("unit"):
            num_str = m.group("num").lower()
            num = _WORD_NUMS.get(num_str, None)
            if num is None:
                try:
                    num = int(num_str)
                except ValueError:
                    return None
            unit = m.group("unit").lower().rstrip("s")
            if unit == "day":
                return (anchor - timedelta(days=num)).strftime("%Y-%m-%d")
            elif unit == "week":
                return (anchor - timedelta(weeks=num)).strftime("%Y-%m-%d")
            elif unit == "month":
                month = anchor.month - num
                year = anchor.year
                while month < 1:
                    month += 12
                    year -= 1
                return datetime(year, month, min(anchor.day, 28)).strftime("%Y-%m-%d")
            elif unit == "year":
                return datetime(anchor.year - num, anchor.month, min(anchor.day, 28)).strftime("%Y-%m-%d")

    return None


class StorageManager:
    """SQLite + FLAC + FTS5 + sqlite-vector — one DB, zero external files."""

    def __init__(self, base_path=None):
        if base_path is None:
            base_path = os.path.expanduser("~/memdio")
        self.base_path = base_path
        self.db_path = os.path.join(base_path, "index.db")

        os.makedirs(base_path, exist_ok=True)

        self._conn = sqlite3.connect(self.db_path, timeout=10)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA cache_size=-8000")
        self._conn.execute("PRAGMA mmap_size=67108864")
        self._conn.execute("PRAGMA foreign_keys=ON")

        self._has_vector = _load_vector_extension(self._conn)
        self._embedder = None
        self._init_db()

    def _init_db(self):
        c = self._conn.cursor()

        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memories'")
        table_exists = c.fetchone() is not None

        if table_exists:
            c.execute("PRAGMA table_info(memories)")
            columns = [row[1] for row in c.fetchall()]

            if "flac_blob" not in columns:
                c.execute("ALTER TABLE memories ADD COLUMN flac_blob BLOB")
                logger.info("Migrated: added flac_blob column")

            if "embedding" not in columns and self._has_vector:
                c.execute("ALTER TABLE memories ADD COLUMN embedding BLOB")
                logger.info("Migrated: added embedding column")

            # --- New columns for contradiction detection ---
            if "superseded_by" not in columns:
                c.execute("ALTER TABLE memories ADD COLUMN superseded_by TEXT")
                logger.info("Migrated: added superseded_by column")
            if "is_current" not in columns:
                c.execute("ALTER TABLE memories ADD COLUMN is_current INTEGER DEFAULT 1")
                logger.info("Migrated: added is_current column")

            # --- New columns for dual timestamps ---
            if "document_date" not in columns:
                c.execute("ALTER TABLE memories ADD COLUMN document_date TEXT")
                logger.info("Migrated: added document_date column")
            if "event_date" not in columns:
                c.execute("ALTER TABLE memories ADD COLUMN event_date TEXT")
                logger.info("Migrated: added event_date column")

            if "filename" in columns:
                new_cols = "id, content, tags, encoder_version, flac_blob, created_at"
                c.execute(f"""
                    CREATE TABLE IF NOT EXISTS memories_new (
                        id TEXT PRIMARY KEY,
                        content TEXT NOT NULL,
                        tags TEXT,
                        encoder_version INTEGER DEFAULT 2,
                        flac_blob BLOB,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        {",' embedding BLOB'" if self._has_vector else ""}
                    )
                """.replace("'", ""))
                c.execute(f"""
                    INSERT OR IGNORE INTO memories_new ({new_cols})
                    SELECT {new_cols} FROM memories
                """)
                c.execute("DROP TABLE memories")
                c.execute("ALTER TABLE memories_new RENAME TO memories")
                logger.info("Migrated: rebuilt table, dropped filename column")
        else:
            c.execute(f"""
                CREATE TABLE memories (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    tags TEXT,
                    encoder_version INTEGER DEFAULT 2,
                    flac_blob BLOB,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    superseded_by TEXT,
                    is_current INTEGER DEFAULT 1,
                    document_date TEXT,
                    event_date TEXT
                    {",' embedding BLOB'" if self._has_vector else ""}
                )
            """.replace("'", ""))

        c.execute("CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_memories_tags ON memories(tags)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_memories_current ON memories(is_current)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_memories_event_date ON memories(event_date)")

        # FTS5 for word-level search
        c.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                id UNINDEXED, content, tags, content=memories, content_rowid=rowid
            )
        """)
        c.execute("INSERT OR IGNORE INTO memories_fts(memories_fts) VALUES('rebuild')")

        # Memory relations table
        c.execute("""
            CREATE TABLE IF NOT EXISTS memory_relations (
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                relation_type TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (source_id, target_id),
                FOREIGN KEY (source_id) REFERENCES memories(id),
                FOREIGN KEY (target_id) REFERENCES memories(id)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_relations_source ON memory_relations(source_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_relations_target ON memory_relations(target_id)")

        # sqlite-vector init
        if self._has_vector:
            try:
                c.execute("PRAGMA table_info(memories)")
                cols = [row[1] for row in c.fetchall()]
                if "embedding" not in cols:
                    c.execute("ALTER TABLE memories ADD COLUMN embedding BLOB")
                self._conn.execute(
                    f"SELECT vector_init('memories', 'embedding', 'dimension={VECTOR_DIM},type=FLOAT32,distance=COSINE')"
                )
                logger.info("sqlite-vector initialized (dim=%d, COSINE)", VECTOR_DIM)
            except Exception as e:
                logger.warning("sqlite-vector init failed: %s", e)
                self._has_vector = False

        self._conn.commit()

    def _get_embedder(self):
        """Lazy-load FastEmbed ONNX model — no PyTorch, 284MB RAM."""
        if self._embedder is None:
            try:
                from fastembed import TextEmbedding

                self._embedder = TextEmbedding("BAAI/bge-small-en-v1.5")
                logger.info("Loaded embedding model: BAAI/bge-small-en-v1.5 (ONNX)")
            except Exception as e:
                self._has_vector = False
                logger.warning("Embedding model unavailable, disabling semantic features: %s", e)
                raise RuntimeError("embedding model unavailable") from e
        return self._embedder

    def _embed(self, text: str) -> list[float]:
        model = self._get_embedder()
        vec = list(model.embed([text]))[0]
        return vec.tolist()

    def _find_similar(self, embedding: list[float], threshold: float, limit: int = 5, exclude_id: str | None = None) -> list[dict]:
        """Find memories with cosine similarity above threshold."""
        if not self._has_vector:
            return []

        query_vec = json.dumps(embedding)
        rows = self._conn.execute(
            """SELECT m.id, m.content, m.created_at, v.distance
                FROM vector_full_scan('memories', 'embedding', ?, ?) v
                JOIN memories m ON m.rowid = v.rowid
                WHERE m.is_current = 1""",
            (query_vec, limit + 5),  # fetch extra to account for filtering
        ).fetchall()

        results = []
        for r in rows:
            similarity = 1.0 - r[3]
            if similarity >= threshold and r[0] != exclude_id:
                results.append({
                    "id": r[0], "content": r[1], "created_at": r[2],
                    "similarity": similarity,
                })
                if len(results) >= limit:
                    break
        return results

    def _detect_contradictions(self, new_id: str, new_embedding: list[float], new_content: str):
        """Mark old memories as superseded if new memory contradicts them."""
        # Skip very short content — too generic to meaningfully contradict
        if len(new_content.strip()) < 50:
            return

        similar = self._find_similar(
            new_embedding, SIMILARITY_THRESHOLD_CONTRADICTION, limit=3, exclude_id=new_id,
        )
        for mem in similar:
            # High similarity but different content = update/contradiction
            if mem["content"].strip() != new_content.strip():
                self._conn.execute(
                    "UPDATE memories SET is_current = 0, superseded_by = ? WHERE id = ?",
                    (new_id, mem["id"]),
                )
                # Create 'updates' relation
                self._conn.execute(
                    "INSERT OR IGNORE INTO memory_relations (source_id, target_id, relation_type) VALUES (?, ?, 'updates')",
                    (new_id, mem["id"]),
                )
                logger.info("Memory %s supersedes %s", new_id, mem["id"])

    def _detect_relations(self, new_id: str, new_embedding: list[float]):
        """Detect 'extends' relations with moderately similar memories."""
        similar = self._find_similar(
            new_embedding, SIMILARITY_THRESHOLD_EXTENDS, limit=3, exclude_id=new_id,
        )
        for mem in similar:
            if mem["similarity"] < SIMILARITY_THRESHOLD_CONTRADICTION:
                # Moderate similarity = extends (not contradiction)
                self._conn.execute(
                    "INSERT OR IGNORE INTO memory_relations (source_id, target_id, relation_type) VALUES (?, ?, 'extends')",
                    (new_id, mem["id"]),
                )

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def store(self, content, tags=None, encoder_version=3, document_date=None, event_date=None):
        """Store content as FLAC audio blob + embedding + relations.

        Encoder v3 (multi-channel OFDM) is the only supported version for new memories.
        v1/v2 are deprecated — existing memories still decode but new stores use v3.
        """
        content = validate_content(content)
        tags = validate_tags(tags)

        if encoder_version != 3:
            logger.warning("Encoder v%d is deprecated, using v3 (multi-channel)", encoder_version)
            encoder_version = 3

        from memdio.core.multichannel import MultiChannelEncoder
        encoder = MultiChannelEncoder()

        signal = encoder.encode(content)
        flac_blob = _signal_to_flac_blob(signal, encoder.sample_rate)
        mem_id = str(uuid.uuid4())

        # Extract event_date from content if not provided
        if event_date is None and document_date:
            event_date = _extract_event_date(content, document_date)

        # Always compute embedding if vector extension available
        embedding = None
        if self._has_vector:
            try:
                embedding = self._embed(content)
            except RuntimeError:
                embedding = None
            if embedding is not None:
                self._conn.execute(
                    "INSERT INTO memories (id, content, tags, encoder_version, flac_blob, embedding, document_date, event_date) "
                    "VALUES (?,?,?,?,?,vector_as_f32(?),?,?)",
                    (mem_id, content, tags, encoder_version, flac_blob, json.dumps(embedding), document_date, event_date),
                )
            else:
                self._conn.execute(
                    "INSERT INTO memories (id, content, tags, encoder_version, flac_blob, document_date, event_date) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (mem_id, content, tags, encoder_version, flac_blob, document_date, event_date),
                )
        else:
            self._conn.execute(
                "INSERT INTO memories (id, content, tags, encoder_version, flac_blob, document_date, event_date) "
                "VALUES (?,?,?,?,?,?,?)",
                (mem_id, content, tags, encoder_version, flac_blob, document_date, event_date),
            )

        # Sync FTS
        rowid = self._conn.execute("SELECT rowid FROM memories WHERE id=?", (mem_id,)).fetchone()[0]
        self._conn.execute(
            "INSERT INTO memories_fts(rowid, id, content, tags) VALUES (?,?,?,?)",
            (rowid, mem_id, content, tags),
        )
        self._conn.commit()

        # Detect contradictions and relations (after commit so the new memory is visible)
        if embedding is not None:
            self._detect_contradictions(mem_id, embedding, content)
            self._detect_relations(mem_id, embedding)
            self._conn.commit()

        logger.info("Stored memory %s (%d bytes text, %d bytes FLAC)", mem_id, len(content), len(flac_blob))
        return mem_id

    def retrieve(self, mem_id):
        """Retrieve and decode by PRIMARY KEY — O(1)."""
        mem_id = validate_memory_id(mem_id)

        row = self._conn.execute(
            "SELECT flac_blob, encoder_version FROM memories WHERE id=?", (mem_id,),
        ).fetchone()

        if not row:
            raise KeyError(f"Memory {mem_id} not found")

        flac_blob, enc_ver = row[0], row[1] or 2

        if flac_blob is None:
            raise KeyError(f"Memory {mem_id} has no audio data")

        signal, sample_rate = _flac_blob_to_signal(flac_blob)

        if enc_ver == 3:
            from memdio.core.multichannel import MultiChannelEncoder
            encoder = MultiChannelEncoder(sample_rate=sample_rate)
        elif enc_ver == 2:
            from memdio.core.encoder import BinaryEncoder
            encoder = BinaryEncoder(sample_rate=sample_rate)
        else:
            from memdio.core.encoder import SimpleEncoder
            encoder = SimpleEncoder(sample_rate)

        return encoder.decode(signal)

    def search(self, query, include_superseded=False):
        """FTS5 word search — matches any word from the query in content AND tags."""
        query = validate_query(query)
        words = query.strip().split()
        if not words:
            return []
        fts_query = " OR ".join(f'"{w}"' for w in words)

        if include_superseded:
            sql = "SELECT m.id, m.content, m.created_at, m.tags FROM memories_fts f JOIN memories m ON f.id = m.id WHERE memories_fts MATCH ? ORDER BY rank LIMIT 100"
        else:
            sql = "SELECT m.id, m.content, m.created_at, m.tags FROM memories_fts f JOIN memories m ON f.id = m.id WHERE memories_fts MATCH ? AND m.is_current = 1 ORDER BY rank LIMIT 100"

        rows = self._conn.execute(sql, (fts_query,)).fetchall()
        return [{"id": r[0], "content": r[1], "created_at": r[2], "tags": r[3]} for r in rows]

    def search_by_tag(self, tag):
        """Search memories by exact tag match. Uses idx_memories_tags index."""
        tag = validate_query(tag).strip().lower()
        rows = self._conn.execute(
            "SELECT id, content, created_at, tags FROM memories WHERE LOWER(tags) LIKE ? AND is_current = 1 ORDER BY created_at DESC LIMIT 100",
            (f"%{tag}%",),
        ).fetchall()
        return [{"id": r[0], "content": r[1], "created_at": r[2], "tags": r[3]} for r in rows]

    def temporal_search(self, start_date: str, end_date: str, top_k: int = 10) -> list[dict]:
        """Search memories by event_date range (YYYY-MM-DD)."""
        rows = self._conn.execute(
            "SELECT id, content, created_at, tags, event_date, document_date "
            "FROM memories WHERE event_date BETWEEN ? AND ? AND is_current = 1 "
            "ORDER BY event_date DESC LIMIT ?",
            (start_date, end_date, top_k),
        ).fetchall()
        return [
            {"id": r[0], "content": r[1], "created_at": r[2], "tags": r[3],
             "event_date": r[4], "document_date": r[5]}
            for r in rows
        ]

    def get_related_memories(self, memory_ids: list[str], max_depth: int = 1) -> list[dict]:
        """Get memories related to the given IDs (one-hop graph expansion)."""
        if not memory_ids:
            return []

        placeholders = ",".join("?" * len(memory_ids))

        # Find related memories in both directions
        rows = self._conn.execute(
            f"""SELECT DISTINCT m.id, m.content, m.created_at, m.tags, r.relation_type
                FROM memory_relations r
                JOIN memories m ON (m.id = r.target_id OR m.id = r.source_id)
                WHERE (r.source_id IN ({placeholders}) OR r.target_id IN ({placeholders}))
                AND m.id NOT IN ({placeholders})
                AND m.is_current = 1""",
            memory_ids * 3,
        ).fetchall()

        return [
            {"id": r[0], "content": r[1], "created_at": r[2], "tags": r[3],
             "relation_type": r[4], "is_related": True}
            for r in rows
        ]

    def list_all(self, limit=100, offset=0):
        """List with pagination — uses idx_memories_created."""
        rows = self._conn.execute(
            "SELECT id, content, created_at FROM memories WHERE is_current = 1 ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [{"id": r[0], "content": r[1], "created_at": r[2]} for r in rows]

    def delete(self, mem_id):
        """Delete by PRIMARY KEY — O(1)."""
        mem_id = validate_memory_id(mem_id)
        row = self._conn.execute("SELECT rowid FROM memories WHERE id=?", (mem_id,)).fetchone()
        if not row:
            logger.info("Memory %s already deleted or not found", mem_id)
            return False
        self._conn.execute(
            "INSERT INTO memories_fts(memories_fts, rowid, id, content, tags) VALUES('delete', ?, ?, (SELECT content FROM memories WHERE id=?), (SELECT tags FROM memories WHERE id=?))",
            (row[0], mem_id, mem_id, mem_id),
        )
        # Clean up relations
        self._conn.execute("DELETE FROM memory_relations WHERE source_id = ? OR target_id = ?", (mem_id, mem_id))
        self._conn.execute("DELETE FROM memories WHERE id=?", (mem_id,))
        self._conn.commit()
        logger.info("Deleted memory %s", mem_id)
        return True

    def get_info(self, mem_id):
        """Get metadata by PRIMARY KEY — O(1)."""
        mem_id = validate_memory_id(mem_id)
        row = self._conn.execute(
            "SELECT id, LENGTH(content), created_at, tags, encoder_version, LENGTH(flac_blob), "
            "is_current, superseded_by, document_date, event_date FROM memories WHERE id=?",
            (mem_id,),
        ).fetchone()
        if not row:
            raise KeyError(f"Memory {mem_id} not found")

        return {
            "id": row[0],
            "content_length": row[1],
            "created_at": row[2],
            "tags": row[3],
            "encoder_version": row[4] or 2,
            "flac_bytes": row[5] or 0,
            "storage": "flac_blob",
            "is_current": bool(row[6]),
            "superseded_by": row[7],
            "document_date": row[8],
            "event_date": row[9],
        }

    def export_flac(self, mem_id, output_path=None):
        """Export a memory as a FLAC file."""
        mem_id = validate_memory_id(mem_id)
        row = self._conn.execute("SELECT flac_blob FROM memories WHERE id=?", (mem_id,)).fetchone()
        if not row or not row[0]:
            raise KeyError(f"Memory {mem_id} not found")

        if output_path is None:
            os.makedirs(os.path.join(self.base_path, "exports"), exist_ok=True)
            output_path = os.path.join(self.base_path, "exports", f"{mem_id}.flac")

        with open(output_path, "wb") as f:
            f.write(row[0])
        return output_path

    def export_wav(self, mem_id, output_path=None):
        """Export a memory as a WAV file."""
        import soundfile as sf

        mem_id = validate_memory_id(mem_id)
        row = self._conn.execute("SELECT flac_blob FROM memories WHERE id=?", (mem_id,)).fetchone()
        if not row or not row[0]:
            raise KeyError(f"Memory {mem_id} not found")

        signal, sample_rate = _flac_blob_to_signal(row[0])

        if output_path is None:
            os.makedirs(os.path.join(self.base_path, "exports"), exist_ok=True)
            output_path = os.path.join(self.base_path, "exports", f"{mem_id}.wav")

        sf.write(output_path, signal, sample_rate, format="WAV", subtype="FLOAT")
        return output_path

    def semantic_search(self, query: str, top_k: int = 5, include_superseded: bool = False) -> list[dict]:
        """Semantic search using sqlite-vector (no FAISS, no sync bugs)."""
        query = validate_query(query)

        if not self._has_vector:
            raise RuntimeError("sqlite-vector extension not available")

        # Embed query
        try:
            query_vec = json.dumps(self._embed(query))
        except RuntimeError as e:
            raise RuntimeError("sqlite-vector extension not available") from e

        # Backfill/re-embed: rows without embeddings OR stale embeddings from old model
        meta_table = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memdio_meta'"
        ).fetchone()
        current_model = "BAAI/bge-small-en-v1.5"
        needs_reembed = False

        if not meta_table:
            self._conn.execute("CREATE TABLE memdio_meta (key TEXT PRIMARY KEY, value TEXT)")
            self._conn.execute("INSERT INTO memdio_meta VALUES ('embed_model', ?)", (current_model,))
            self._conn.commit()
            needs_reembed = True
        else:
            row = self._conn.execute("SELECT value FROM memdio_meta WHERE key='embed_model'").fetchone()
            if not row or row[0] != current_model:
                self._conn.execute("INSERT OR REPLACE INTO memdio_meta VALUES ('embed_model', ?)", (current_model,))
                self._conn.commit()
                needs_reembed = True

        if needs_reembed:
            rows_without = self._conn.execute("SELECT id, content FROM memories").fetchall()
        else:
            rows_without = self._conn.execute(
                "SELECT id, content FROM memories WHERE embedding IS NULL"
            ).fetchall()
        if rows_without:
            for row_id, row_content in rows_without:
                vec = self._embed(row_content)
                self._conn.execute(
                    "UPDATE memories SET embedding = vector_as_f32(?) WHERE id = ?",
                    (json.dumps(vec), row_id),
                )
            self._conn.commit()
            logger.info("Backfilled %d embeddings", len(rows_without))

        # Vector similarity search
        current_filter = "" if include_superseded else "AND m.is_current = 1"
        rows = self._conn.execute(
            f"""SELECT m.id, m.content, m.created_at, v.distance
                FROM vector_full_scan('memories', 'embedding', ?, ?) v
                JOIN memories m ON m.rowid = v.rowid
                {current_filter}""",
            (query_vec, top_k + 10),  # fetch extra for filtering
        ).fetchall()

        results = []
        for r in rows:
            results.append({"id": r[0], "content": r[1], "created_at": r[2], "score": 1.0 - r[3]})
            if len(results) >= top_k:
                break

        return results

    def stats(self):
        """Aggregate stats — single query."""
        row = self._conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(LENGTH(content)),0), COALESCE(SUM(LENGTH(flac_blob)),0) FROM memories"
        ).fetchone()
        superseded = self._conn.execute("SELECT COUNT(*) FROM memories WHERE is_current = 0").fetchone()[0]
        relations = self._conn.execute("SELECT COUNT(*) FROM memory_relations").fetchone()[0]
        return {
            "total_memories": row[0],
            "total_content_bytes": row[1],
            "total_flac_bytes": row[2],
            "superseded_memories": superseded,
            "total_relations": relations,
        }

"""
SetuBid Public Procurement Deduplication Engine
================================================
Implements:
1. Signal extraction and boilerplate/noise suppression
2. Word-level 3-gram shingling & Containment/Jaccard similarity
3. MinHash signature generation (tunable K=128)
4. Locality-Sensitive Hashing (LSH) with band partitioning (b=32, r=4)
5. SQLite relational database storage and B-tree index access paths
6. Boilerplate/DF mitigation to prevent bucket explosion
7. Persistent Opportunity Card ID assignment (Bookmark Invariant)
"""

import os
import re
import time
import sqlite3
import numpy as np
import pandas as pd
from collections import defaultdict, Counter

# Universal hashing parameters for MinHash: h_i(x) = (A_i * x + B_i) % P
MERSENNE_PRIME = (1 << 31) - 1

class SetuBidDeduplicator:
    def __init__(self, k_signatures=128, bands=32, rows=4, seed=42):
        assert bands * rows == k_signatures, "bands * rows must equal k_signatures"
        self.K = k_signatures
        self.b = bands
        self.r = rows
        self.seed = seed
        
        # Initialize deterministic hash coefficients
        rng = np.random.RandomState(seed)
        self.A = rng.randint(1, MERSENNE_PRIME, size=self.K, dtype=np.int64)
        self.B = rng.randint(0, MERSENNE_PRIME, size=self.K, dtype=np.int64)
        
        self.stop_shingles = set()

    @staticmethod
    def clean_text_naive(title, body):
        """Baseline naive text: lowercase and whitespace normalization without stripping noise."""
        text = f"{title} {body}".lower()
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def clean_text_engineered(title, body):
        """
        Engineered text:
        1. Strips portal boilerplate preambles (P001-P006 nodal blocks).
        2. Strips portal footer disclaimers.
        3. Strips arbitrary portal reference numbers (unshared noise).
        4. Strips dates (which shift across portals and corrigenda).
        5. Normalizes title prefixes (NIT, e-Tender, Corrigendum, bracketed codes).
        """
        # 1. Clean Title
        t = title if isinstance(title, str) else ""
        t = re.sub(r"^(?:tender\s+notice\s*:\s*(?:corrigendum\s*-\s*)?|nit\s+for\s+|e-tender\s*-\s*(?:corrigendum\s*-\s*)?|corrigendum\s*-\s*)", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*\[[\w\-/]+\]\s*$", "", t)
        
        # 2. Clean Body
        b = body if isinstance(body, str) else ""
        # Strip nodal_b preamble
        if "===============================================================================" in b:
            b = b.split("===============================================================================")[-1]
        # Strip nodal_a preamble
        if "NOTICE DETAILS FOLLOW" in b:
            b = b.split("NOTICE DETAILS FOLLOW")[-1]
        # Strip nodal_a footer disclaimer
        if "Disclaimer: this entry is reproduced" in b:
            b = b.split("Disclaimer: this entry is reproduced")[0]
            
        # Strip portal reference numbers
        b = re.sub(r"tender\s+reference\s+number:\s*[\w\-/]+[\.\n]", " ", b, flags=re.IGNORECASE)
        # Strip dates
        b = re.sub(r"(?:publication of notice|last date and time|date of opening)[^\n\.]*[\.\n]", " ", b, flags=re.IGNORECASE)
        b = re.sub(r"\b\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}\b", " ", b)
        
        combined = f"{t} {b}".lower()
        combined = re.sub(r"[^\w\s]", " ", combined)
        combined = re.sub(r"\s+", " ", combined).strip()
        return combined

    @staticmethod
    def get_shingles(text, k=3, unit="word"):
        """Extract word k-grams or character k-grams."""
        if unit == "word":
            words = text.split()
            if len(words) < k:
                return set(words)
            return set(" ".join(words[i:i+k]) for i in range(len(words) - k + 1))
        elif unit == "char":
            if len(text) < k:
                return {text}
            return set(text[i:i+k] for i in range(len(text) - k + 1))
        else:
            raise ValueError(f"Unknown unit: {unit}")

    def fit_stop_shingles(self, shingles_corpus, max_df_pct=0.10):
        """Fit stop-shingles by document frequency threshold (Mitigation for bucket explosion)."""
        counts = Counter()
        for sh in shingles_corpus:
            counts.update(sh)
        limit = int(max_df_pct * len(shingles_corpus))
        self.stop_shingles = set(s for s, c in counts.items() if c > limit)
        return self.stop_shingles

    def compute_minhash(self, shingles):
        """Vectorized MinHash signature calculation."""
        # Filter stop-shingles if fitted
        if self.stop_shingles:
            filtered = shingles - self.stop_shingles
            if filtered:
                shingles = filtered

        if not shingles:
            return np.zeros(self.K, dtype=np.uint32)

        # Hash shingles into 31-bit integers
        hashes = np.array([abs(hash(s)) % MERSENNE_PRIME for s in shingles], dtype=np.int64)
        # MinHash: sig[i] = min((A[i]*x + B[i]) % P)
        sig = np.min((self.A[:, None] * hashes[None, :] + self.B[:, None]) % MERSENNE_PRIME, axis=1)
        return sig.astype(np.uint32)

    @staticmethod
    def jaccard_similarity(s1, s2):
        if not s1 or not s2:
            return 0.0
        return len(s1 & s2) / len(s1 | s2)

    @staticmethod
    def containment_similarity(s1, s2):
        """
        Containment / Overlap coefficient: |A & B| / min(|A|, |B|).
        Essential for asymmetric pairs caused by portal truncation.
        """
        if not s1 or not s2:
            return 0.0
        return len(s1 & s2) / min(len(s1), len(s2))

    @staticmethod
    def minhash_similarity(sig1, sig2):
        """Estimated Jaccard similarity from MinHash signatures."""
        return np.mean(sig1 == sig2)


class SetuBidDatabase:
    """Relational SQLite storage and index manager."""
    def __init__(self, db_path="setubid.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA journal_mode = WAL;")
        self.conn.execute("PRAGMA synchronous = NORMAL;")
        self.create_schema()

    def create_schema(self):
        with self.conn:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS notices (
                    notice_id TEXT PRIMARY KEY,
                    portal_id TEXT NOT NULL,
                    published_at TEXT,
                    estimated_value INTEGER,
                    closing_date TEXT,
                    title TEXT,
                    clean_text TEXT
                );

                CREATE TABLE IF NOT EXISTS minhash_signatures (
                    notice_id TEXT PRIMARY KEY,
                    signature BLOB NOT NULL,
                    FOREIGN KEY(notice_id) REFERENCES notices(notice_id)
                );

                CREATE TABLE IF NOT EXISTS lsh_buckets (
                    band_id INTEGER NOT NULL,
                    bucket_hash INTEGER NOT NULL,
                    notice_id TEXT NOT NULL,
                    FOREIGN KEY(notice_id) REFERENCES notices(notice_id)
                );

                CREATE TABLE IF NOT EXISTS opportunity_cards (
                    card_id TEXT PRIMARY KEY,
                    canonical_notice_id TEXT NOT NULL,
                    estimated_value INTEGER,
                    created_at TEXT NOT NULL,
                    last_updated_at TEXT NOT NULL,
                    active INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS notice_to_card (
                    notice_id TEXT PRIMARY KEY,
                    card_id TEXT NOT NULL,
                    added_at TEXT NOT NULL,
                    FOREIGN KEY(notice_id) REFERENCES notices(notice_id),
                    FOREIGN KEY(card_id) REFERENCES opportunity_cards(card_id)
                );

                CREATE TABLE IF NOT EXISTS card_redirects (
                    old_card_id TEXT PRIMARY KEY,
                    new_card_id TEXT NOT NULL,
                    merged_at TEXT NOT NULL
                );
            """)

    def create_lsh_index(self):
        """Create composite B-tree index on (band_id, bucket_hash, notice_id)."""
        with self.conn:
            self.conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_lsh_lookup 
                ON lsh_buckets (band_id, bucket_hash, notice_id);
            """)

    def drop_lsh_index(self):
        """Drop index to force full table scan for performance measurement."""
        with self.conn:
            self.conn.execute("DROP INDEX IF EXISTS idx_lsh_lookup;")

    def query_candidates_for_notice(self, band_hashes, notice_id):
        """Retrieve candidate notice IDs matching any of the notice's band hashes."""
        cur = self.conn.cursor()
        candidates = set()
        for band_id, b_hash in enumerate(band_hashes):
            cur.execute("""
                SELECT notice_id FROM lsh_buckets 
                WHERE band_id = ? AND bucket_hash = ? AND notice_id != ?;
            """, (band_id, int(b_hash), notice_id))
            for row in cur.fetchall():
                candidates.add(row[0])
        return candidates

    def explain_candidate_query(self, band_id, b_hash):
        cur = self.conn.cursor()
        cur.execute("""
            EXPLAIN QUERY PLAN
            SELECT notice_id FROM lsh_buckets 
            WHERE band_id = ? AND bucket_hash = ?;
        """, (band_id, int(b_hash)))
        return cur.fetchall()

    def close(self):
        self.conn.close()

"""Hybrid retrieval: BM25 (lexical) + dense vectors (Qdrant or numpy), fused with RRF.
 
Every hit reports its BM25 rank, its dense rank and the fused score so the supervisor's
trace can show WHY a passage was retrieved. Embedders:
  ollama:<model>   semantic embeddings from a local Ollama model (CPU by default)
  hashing-512      deterministic feature-hashing fallback; lexical, NOT semantic.
                   Used when Ollama is unavailable and always labelled as such.
"""
import hashlib
import json
import math
import re
import logging
import uuid
from collections import Counter
from pathlib import Path
 
import numpy as np
 
log = logging.getLogger(__name__)
_TOKEN = re.compile(r"[a-z0-9]+")
STOP = set("a an and are as at be by for from has have i in is it its me my of on or our so that the "
           "this to was we what when which who why with you your do does did can could should would "
           "about than then them they there these those how into over under more most less".split())
 
 
def tokenize(text):
    return [t for t in _TOKEN.findall(text.lower()) if t not in STOP]
 
 
class BM25:
    def __init__(self, docs, k1=1.4, b=0.75):
        self.k1, self.b = k1, b
        self.toks = [tokenize(d) for d in docs]
        self.len = [len(t) for t in self.toks]
        self.avg = (sum(self.len) / len(self.len)) if self.len else 0.0
        self.tf = [Counter(t) for t in self.toks]
        df = Counter(w for t in self.toks for w in set(t))
        n = len(self.toks)
        self.idf = {w: math.log(1 + (n - f + 0.5) / (f + 0.5)) for w, f in df.items()}
 
    def scores(self, query):
        q = tokenize(query)
        out = np.zeros(len(self.toks))
        for i, tf in enumerate(self.tf):
            s = 0.0
            for w in q:
                f = tf.get(w)
                if f:
                    s += self.idf[w] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / (self.avg or 1)))
            out[i] = s
        return out
 
 
# ---- embedders ---------------------------------------------------------------------
class HashingEmbedder:
    dim = 512
    name = "hashing-512"
    semantic = False
 
    def __init__(self):
        self._slot = {}  # feature -> (column, sign); hashing is the slow part on a large corpus
 
    def _feature(self, f):
        x = self._slot.get(f)
        if x is None:
            h = int(hashlib.md5(f.encode()).hexdigest(), 16)
            x = self._slot[f] = (h % self.dim, 1.0 if (h >> 64) & 1 else -1.0)
        return x
 
    def embed(self, texts, kind="document"):
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, t in enumerate(texts):
            toks = tokenize(t)
            feats = toks + [a + "_" + b for a, b in zip(toks, toks[1:])]
            for f in feats:
                col, sign = self._feature(f)
                out[row, col] += sign
            n = np.linalg.norm(out[row])
            if n > 0:
                out[row] /= n
        return out
 
 
class OllamaEmbedder:
    semantic = True
 
    def __init__(self, llm, model):
        self.llm = llm
        self.model = model
        self.name = f"ollama:{model}"
        self.dim = len(self.embed(["probe"])[0])
 
    def _prefix(self, kind):
        # nomic-embed-text expects task prefixes; other models ignore them harmlessly
        if "nomic" in self.model:
            return "search_query: " if kind == "query" else "search_document: "
        return ""
 
    def embed(self, texts, kind="document"):
        vecs = []
        for i in range(0, len(texts), 32):
            batch = [self._prefix(kind) + t for t in texts[i:i + 32]]
            vecs.extend(self.llm.embed(batch))
            if len(texts) > 500 and (i // 32) % 50 == 49:
                log.info("embedded %d / %d passages", len(vecs), len(texts))
        v = np.asarray(vecs, dtype=np.float32)
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)
 
 
# ---- vector stores -----------------------------------------------------------------
class NumpyIndex:
    backend = "numpy"
 
    def __init__(self):
        self.ids, self.mat = [], None
 
    def rebuild(self, ids, vectors):
        self.ids, self.mat = list(ids), np.asarray(vectors, dtype=np.float32)
 
    def search(self, vec, k, allowed=None):
        if self.mat is None or not len(self.ids):
            return []
        sims = self.mat @ np.asarray(vec, dtype=np.float32)
        order = np.argsort(-sims)
        out = []
        for i in order:
            if allowed is None or self.ids[i] in allowed:
                out.append((self.ids[i], float(sims[i])))
            if len(out) >= k:
                break
        return out
 
 
class QdrantIndex:
    backend = "qdrant"
 
    def __init__(self, location, path, collection):
        from qdrant_client import QdrantClient
        if location == "local":
            path.mkdir(parents=True, exist_ok=True)
            self.client = QdrantClient(path=str(path))
        else:
            self.client = QdrantClient(url=location)
        self.collection = collection
        import atexit
        atexit.register(self.close)
 
    @staticmethod
    def _pid(chunk_id):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))
 
    def exists(self):
        return self.client.collection_exists(self.collection)
 
    def rebuild(self, ids, vectors):
        from qdrant_client.models import Distance, PointStruct, VectorParams
        if self.client.collection_exists(self.collection):
            self.client.delete_collection(self.collection)
        self.client.create_collection(self.collection, vectors_config=VectorParams(
            size=int(np.asarray(vectors).shape[1]), distance=Distance.COSINE))
        pts = [PointStruct(id=self._pid(i), vector=list(map(float, v)), payload={"chunk_id": i})
               for i, v in zip(ids, vectors)]
        for j in range(0, len(pts), 256):
            self.client.upsert(self.collection, pts[j:j + 256])
 
    def search(self, vec, k, allowed=None):
        from qdrant_client.models import FieldCondition, Filter, MatchAny
        flt = None
        if allowed is not None:
            flt = Filter(must=[FieldCondition(key="chunk_id", match=MatchAny(any=sorted(allowed)))])
        res = self.client.query_points(self.collection, query=list(map(float, vec)), limit=k,
                                       query_filter=flt, with_payload=True)
        return [(p.payload["chunk_id"], float(p.score)) for p in res.points]
 
    def close(self):
        self.client.close()
 
 
# ---- hybrid retriever ----------------------------------------------------------------
class HybridRetriever:
    RRF_K = 60
 
    def __init__(self, embedder, index, meta_path):
        self.embedder = embedder
        self.index = index
        self.meta_path = Path(meta_path)
        self.chunks, self.by_id, self.bm25 = [], {}, None
 
    @property
    def description(self):
        return {"embedder": self.embedder.name, "semantic": self.embedder.semantic,
                "vector_backend": self.index.backend, "chunks": len(self.chunks),
                "fusion": f"reciprocal rank fusion (k={self.RRF_K}) of BM25 and dense cosine"}
 
    def build(self, chunks):
        self.chunks = list(chunks)
        self.by_id = {c["id"]: c for c in self.chunks}
        self.bm25 = BM25([c["title"] + " " + c["text"] for c in self.chunks])
        ids = [c["id"] for c in self.chunks]
        fp = hashlib.sha256(json.dumps([[c["id"], c["text"]] for c in self.chunks]).encode()).hexdigest()[:16]
        meta = {"fingerprint": fp, "embedder": self.embedder.name, "backend": self.index.backend}
        old = json.loads(self.meta_path.read_text()) if self.meta_path.exists() else None
        if isinstance(self.index, QdrantIndex) and old == meta and self.index.exists():
            return "reused"
        texts = [c["title"] + ". " + c["text"] for c in self.chunks]
        vecs = self._embed_cached(texts) if ids else np.zeros((0, 1))
        self.index.rebuild(ids, vecs)
        self.meta_path.write_text(json.dumps(meta))
        return "rebuilt"
 
    def _embed_cached(self, texts):
        """Embed with a disk cache keyed by (embedder, text), so a restart or an added document
        only embeds text that was never embedded before. The hashing embedder needs no cache."""
        if not self.embedder.semantic:
            return self.embedder.embed(texts)
        safe = re.sub(r"[^a-z0-9]+", "_", self.embedder.name.lower())
        path = self.meta_path.with_name(f"embed_cache_{safe}.npz")
        cache = {}
        if path.exists():
            try:
                z = np.load(path)
                cache = dict(zip(z["keys"].tolist(), z["vecs"]))
            except Exception:
                cache = {}
        keys = [hashlib.sha1(t.encode()).hexdigest() for t in texts]
        todo = [i for i, k in enumerate(keys) if k not in cache]
        if todo:
            log.info("embedding %d of %d passages with %s (%d already cached)",
                     len(todo), len(texts), self.embedder.name, len(texts) - len(todo))
            new = self.embedder.embed([texts[i] for i in todo])
            for i, v in zip(todo, new):
                cache[keys[i]] = v
            live = set(keys)
            ks = [k for k in cache if k in live]
            tmp = path.with_suffix(".tmp.npz")
            np.savez(tmp, keys=np.array(ks), vecs=np.stack([cache[k] for k in ks]).astype(np.float32))
            tmp.replace(path)
        return np.stack([cache[k] for k in keys]).astype(np.float32)
 
    def search(self, query, k=5, kinds=None):
        if not self.chunks or not query.strip():
            return []
        allowed = None if not kinds else {c["id"] for c in self.chunks if c["kind"] in kinds}
        bm = self.bm25.scores(query)
        lex = [(self.chunks[i]["id"], float(bm[i])) for i in np.argsort(-bm)
               if bm[i] > 0 and (allowed is None or self.chunks[i]["id"] in allowed)][:50]
        qv = self.embedder.embed([query], kind="query")[0]
        dense = self.index.search(qv, 50, allowed)
        fused = {}
        for rank, (cid, s) in enumerate(lex, 1):
            fused.setdefault(cid, {"bm25_rank": None, "bm25": 0.0, "dense_rank": None, "dense": 0.0, "rrf": 0.0})
            fused[cid].update(bm25_rank=rank, bm25=round(s, 3))
            fused[cid]["rrf"] += 1.0 / (self.RRF_K + rank)
        for rank, (cid, s) in enumerate(dense, 1):
            fused.setdefault(cid, {"bm25_rank": None, "bm25": 0.0, "dense_rank": None, "dense": 0.0, "rrf": 0.0})
            fused[cid].update(dense_rank=rank, dense=round(s, 3))
            fused[cid]["rrf"] += 1.0 / (self.RRF_K + rank)
        top = sorted(fused.items(), key=lambda kv: (-kv[1]["rrf"], kv[0]))[:k]
        return [{**{x: self.by_id[cid][x] for x in ("id", "doc_id", "kind", "title", "text")},
                 "meta": self.by_id[cid].get("meta", {}), **{kk: (round(v, 5) if kk == "rrf" else v)
                                                             for kk, v in sc.items()}}
                for cid, sc in top if cid in self.by_id]
 

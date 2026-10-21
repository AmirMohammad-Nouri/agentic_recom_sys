"""Knowledge layer: food knowledge graph (Neo4j or in-process) and hybrid retrieval."""
import logging

from .graph import MemoryGraph
from .retrieval import HashingEmbedder, HybridRetriever, NumpyIndex, OllamaEmbedder, QdrantIndex

log = logging.getLogger("foodrec.knowledge")


def make_graph(settings):
    """Return (graph, note). Falls back to the in-process graph when Neo4j is not configured
    or not reachable; the note says which backend is active and why."""
    if settings.neo4j_uri:
        try:
            from .neo4j_graph import Neo4jGraph
            g = Neo4jGraph(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password,
                           settings.neo4j_database)
            return g, f"Neo4j at {settings.neo4j_uri}"
        except Exception as e:  # noqa: BLE001
            log.warning("Neo4j unavailable (%s); using in-process graph", e)
            return MemoryGraph(), f"in-process graph (Neo4j unavailable: {type(e).__name__})"
    return MemoryGraph(), "in-process graph (NEO4J_URI not set)"


def make_retriever(settings, llm):
    embedder, note = HashingEmbedder(), "hashing embeddings (lexical fallback)"
    if llm is not None and llm.status().get("embed_model_ready"):
        try:
            embedder = OllamaEmbedder(llm, settings.embed_model)
            note = f"Ollama embeddings ({settings.embed_model}, {'CPU' if settings.embed_on_cpu else 'GPU'})"
        except Exception as e:  # noqa: BLE001
            log.warning("embedding model unavailable (%s); using hashing embeddings", e)
    index = NumpyIndex()
    if settings.qdrant != "memory":
        try:
            safe = embedder.name.replace(":", "_").replace("/", "_")
            index = QdrantIndex(settings.qdrant, settings.qdrant_path, f"foodrec_{safe}")
            note += f"; Qdrant ({settings.qdrant})"
        except Exception as e:  # noqa: BLE001
            log.warning("Qdrant unavailable (%s); using numpy index", e)
            note += f"; numpy index (Qdrant unavailable: {type(e).__name__})"
    else:
        note += "; numpy index"
    return HybridRetriever(embedder, index, settings.data_dir / f"index_meta_{index.backend}.json"), note

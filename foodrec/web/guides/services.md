# Knowledge services

Both services are optional; the app falls back to in-process versions that behave the same.

- **Neo4j** (`neo4j_uri`): the knowledge graph of recipes, ingredients, users, interactions and supervisor decisions. Empty = the in-process graph. Start one with `docker compose up -d neo4j`, set `neo4j_uri` to `bolt://localhost:7687` and the password in the environment (`NEO4J_PASSWORD`, never in this file).
- **Vector store** (`qdrant`): `local` (embedded Qdrant on disk, in the data folder), `memory` (a numpy index, rebuilt at start) or a Qdrant server URL.

`null` here means "use the environment" (`NEO4J_URI`, `FOODREC_QDRANT`), so a `.env` per machine keeps working. Changes need a restart.

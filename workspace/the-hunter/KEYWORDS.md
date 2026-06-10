# Keywords — Scan Root Queries

## Requêtes racines (12 roots)

Appliquées sur Indeed (scraper Playwright) et LinkedIn avec filtre global `remote=true`.

```
AI
agent
agentic
GenAI
automation
LLM
RAG
ML
full stack
n8n
Python
developer
```

## Stratégie de requête

Chaque root est soumise séparément pour maximiser la couverture.
Dédupliquer par SHA-256 (url + title + company) avant tout traitement.

## Alias de compétences (Layer 2)

Utilisés dans le calcul de match rate pour normaliser le vocabulaire des offres :

| Terme offre | Alias reconnus |
|------------|----------------|
| Vector DB | Qdrant, Pinecone, pgvector, Weaviate, Chroma |
| GenAI | LLM, Large Language Model, Foundation Model |
| Agentic AI | Agent, AI Agent, Multi-agent, Autonomous AI |
| Workflow automation | n8n, Make, Zapier, Airflow, Prefect |
| MLOps | Model deployment, ML pipeline, Model serving |
| RAG | Retrieval Augmented Generation, Semantic search |
| Full stack | Frontend + Backend, React + FastAPI, Next.js + Python |
| AI Engineer | AI Developer, ML Engineer, Applied AI |

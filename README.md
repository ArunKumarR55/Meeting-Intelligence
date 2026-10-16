# Autonomous Meeting Intelligence Platform (AMIP)

AMIP is a privacy-first meeting intelligence application developed by a four-person team. This repository is organized as a modular monorepo with separate backend and frontend domains.

## Project areas

- `backend/` — FastAPI, SQLAlchemy, and SQLite service structure.
- `frontend/` — React and Vite user interface structure.
- `docs/` — architecture, module, and operations documentation, including [live meeting assistant setup](docs/operations/live-meeting-agent.md).
- `scripts/` — cross-platform development and maintenance script area.
- `data/` — local-only development data area; never commit meeting recordings or sensitive data.

## Application modules

1. Audio ingestion and security
2. LLM analytics and structured extraction
3. Behavioral analytics
4. Corporate memory and retrieval-augmented generation
5. Workflows, notifications, and integrations
6. Voice and interactive UI

The backend and frontend are implemented as separate domains in this monorepo. Manual audio upload remains available alongside the local Playwright meeting assistant.

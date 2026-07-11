# Docker Compose Local Development Workflow

## Overview

This directory contains a `docker-compose.yml` for integrated local development of the full stack: frontend, backend (choice of Node.js or Python), and coordinated startup.

## Quick Start

### Prerequisites

- Docker and Docker Compose installed
- `.env` file in the repository root with LLM API keys (see `.env.compose` for template)

### Start the Stack (Node.js Backend)

```bash
docker compose --profile nodejs up --build
```

### Start the Stack (Python Backend)

```bash
docker compose --profile python up --build
```

### Stop the Stack

```bash
docker compose down
```

## Environment Configuration

### .env File

Copy `.env.compose` and customize with your API keys:

```bash
cp .env.compose .env
# Edit .env with your actual API keys
```

Required variables:

- `OPENAI_API_KEY`: OpenAI API key (if using OpenAI provider)
- `GEMINI_API_KEY`: Google Gemini API key (if using Gemini provider)
- `LLM_PROVIDER`: `openai` or `gemini`
- `CORS_ALLOWED_ORIGINS`: Set to `http://localhost` for local development

### Override Environment via Compose

To override variables without modifying `.env`:

```bash
docker compose --profile nodejs -e LLM_PROVIDER=gemini up
```

## Services

### Frontend (Always Running)

- **Port**: 80
- **URL**: http://localhost
- **Health Check**: GET /
- **Build Context**: `./frontend`
- **Build Time**: ~15 seconds

### Backend — Node.js (Profile: `nodejs`)

- **Port**: 8000
- **URL**: http://localhost:8000
- **Health Check**: GET /api/health
- **Build Context**: `./backend-nodejs`
- **Build Time**: ~30 seconds
- **Runtime**: Node.js with Express

### Backend — Python (Profile: `python`)

- **Port**: 8000
- **URL**: http://localhost:8000
- **Health Check**: GET /api/health
- **Build Context**: `./backend-python`
- **Build Time**: ~60 seconds
- **Runtime**: Python with FastAPI

## Smoke Tests

Verify the integrated stack is working:

```bash
./scripts/smoke-tests.sh
```

The script checks:

1. Frontend reachability on port 80
2. Backend `/api/health` endpoint
3. Backend `/api/chat` endpoint
4. CORS headers and cross-origin requests

## Troubleshooting

### Port Already in Use

If you see `port is already allocated`:

```bash
# Find and kill the process
lsof -i :80
lsof -i :8000
kill -9 <PID>

# Or use docker to stop
docker compose down -v
```

### Frontend Can't Reach Backend

Check CORS configuration:

- Ensure `CORS_ALLOWED_ORIGINS` includes `http://localhost`
- Frontend build arg `VITE_API_BASE_URL` should be `http://localhost:8000`

### Backend Health Check Failing

Check logs:

```bash
docker compose logs backend-nodejs
# or
docker compose logs backend-python
```

Ensure API keys are set in `.env` and match the selected `LLM_PROVIDER`.

## Development Workflow

1. **Modify app code** locally in `frontend/`, `backend-nodejs/`, or `backend-python/`
2. **Rebuild images** without stopping:
   ```bash
   docker compose --profile nodejs up --build
   ```
3. **View logs**:
   ```bash
   docker compose logs -f frontend
   docker compose logs -f backend-nodejs  # or backend-python
   ```
4. **Test end-to-end**:
   ```bash
   ./scripts/smoke-tests.sh
   # or manually: open http://localhost in browser and send chat message
   ```

## Notes

- Compose file uses profiles to ensure only one backend runs at a time
- Frontend depends on backend health check but allows optional dependency (will start even if backend fails)
- Non-root users run in containers for production-like security
- Build toolchains (npm, tsc, pip, uv) are isolated to builder stages; runtime images are minimal

## Advanced: Compose Configuration

### Override Individual Service

To run only the backend:

```bash
docker compose --profile nodejs up backend-nodejs
```

### Force Rebuild Without Cache

```bash
docker compose --profile nodejs build --no-cache
```

### Remove Volumes and Networks

```bash
docker compose down -v
```

### Environment File Precedence

Docker Compose automatically loads variables from `.env` in the project directory.
To use `.env.compose`, either copy it to `.env` or run `docker compose --env-file .env.compose ...`.
CLI `-e` flags (and explicit `environment:` entries) override values from the env file.

## See Also

- [Backend Node.js README](./backend-nodejs/README.md)
- [Backend Python README](./backend-python/README.md)
- [Frontend README](./frontend/README.md)

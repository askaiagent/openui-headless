# OpenUI Codebase Analysis

## Project Overview
OpenUI is an open-source tool designed to accelerate UI component creation. It allows users to describe UI components using natural language and see them rendered live. It supports converting HTML to various frameworks like React, Svelte, and Web Components.

## Architecture

### Backend
The backend is a Python-based service built with **FastAPI**. It acts as a proxy between the frontend and various LLM providers.

- **Core Server**: `backend/openui/server.py` handles API requests, session management, and routing.
- **LLM Integrations**:
    - **OpenAI**: Direct integration via `openai` library.
    - **Ollama**: Integration for local models via `ollama` library.
    - **LiteLLM**: A universal wrapper used to support a wide array of models, including **Gemini**, **Anthropic (Claude)**, **Groq**, and others.
- **Database**: Uses **Peewee ORM** for managing users, usage, votes, and components (`backend/openui/db/models.py`).
- **Session Management**: Implements a custom session store (`backend/openui/session.py`).
- **Storage**: Simple file-based storage for sharing components (`backend/openui/util/storage.py`).

### Frontend
The frontend is a Single Page Application (SPA) built with **React**, **Vite**, and **Tailwind CSS**.

- **State Management**: Uses **Jotai** (atoms in `frontend/src/state/atoms`).
- **Key Components**:
    - `Chat.tsx`: The main interface for interacting with the AI.
    - `CodeEditor.tsx` & `CodeViewer.tsx`: For viewing and editing the generated code.
    - `Settings.tsx`: For configuring LLM providers and models.
    - `HtmlAnnotator.tsx`: For interacting with the rendered HTML.
- **API Client**: Communicates with the backend via `frontend/src/api/openui.ts`.

## Configuration & Environment Variables

The application relies on environment variables to authenticate with LLM providers:

| Provider | Environment Variable | Description |
| :--- | :--- | :--- |
| OpenAI | `OPENAI_API_KEY` | API key for OpenAI models |
| Gemini | `GEMINI_API_KEY` | API key for Google Gemini models (via LiteLLM) |
| Anthropic | `ANTHROPIC_API_KEY` | API key for Claude models (via LiteLLM) |
| Groq | `GROQ_API_KEY` | API key for Groq models |
| Cohere | `COHERE_API_KEY` | API key for Cohere models (via LiteLLM) |
| Mistral | `MISTRAL_API_KEY` | API key for Mistral models (via LiteLLM) |
| Ollama | `OLLAMA_HOST` | Host and port for Ollama (default: `http://127.0.0.1:11434`) |
| Custom | `OPENAI_COMPATIBLE_ENDPOINT` | Endpoint for OpenAI-compatible APIs |
| Custom | `OPENAI_COMPATIBLE_API_KEY` | API key for OpenAI-compatible APIs |

## Running Locally

### Backend
1. Navigate to the backend directory: `cd backend`
2. Install dependencies: `uv sync --frozen --extra litellm` (or `pip install .[litellm]`)
3. Activate virtual environment: `source .venv/bin/activate`
4. Set required API keys (e.g., `export GEMINI_API_KEY=your_key`)
5. Start the server: `python -m openui` (use `--dev` for live reload)

### Frontend
1. Navigate to the frontend directory: `cd frontend`
2. Install dependencies: `pnpm install`
3. Start the development server: `pnpm run dev`

## Deployment
- **Docker**: A `Dockerfile` is provided in the `backend` directory.
- **Docker Compose**: A `docker-compose.yaml` file is available in the root for orchestrating the backend and Ollama.

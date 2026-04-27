from fastapi import APIRouter, Request, HTTPException, Security, Depends
from fastapi.security.api_key import APIKeyHeader
from fastapi.responses import StreamingResponse, JSONResponse
from .db.models import User, ApiKey, Project, ProjectMessage, Usage
from .headless import HeadlessService, extract_html_and_metadata
from .models import count_tokens
from .openai import openai_stream_generator
from .ollama import ollama_stream_generator, openai_to_ollama
from . import config
from .logs import logger
import uuid
import datetime
import json
import asyncio
from typing import Optional
from openai import AsyncOpenAI
from ollama import AsyncClient


def wrap_in_full_html(html: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        body {{ font-family: 'Inter', sans-serif; margin: 0; padding: 0; min-height: 100vh; }}
    </style>
</head>
<body>
    {html}
</body>
</html>"""


router = APIRouter(prefix="/headless", tags=["headless"])
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Re-initialize clients to avoid circular imports or relying on server.py's globals if possible
# although server.py exports them. Let's try to use the ones from server if available.
# To keep it clean, we'll re-initialize them here using the same config.
openai_client = AsyncOpenAI(
    base_url=config.OPENAI_BASE_URL, api_key=config.OPENAI_API_KEY
)
ollama_client = AsyncClient()
ollama_openai_client = AsyncOpenAI(base_url=config.OLLAMA_HOST + "/v1", api_key="xxx")

if config.GROQ_API_KEY:
    groq_client = AsyncOpenAI(
        base_url=config.GROQ_BASE_URL, api_key=config.GROQ_API_KEY
    )
else:
    groq_client = None


async def get_user_from_api_key(api_key: str = Security(api_key_header)):
    # For now, we return a default user to bypass API key requirements as requested
    user = User.get_or_none(User.username == "testuser")
    if not user:
        user_id = uuid.uuid4()
        user = User.create(
            id=user_id.bytes, username="testuser", created_at=datetime.datetime.now()
        )
    return user


@router.post("/projects")
async def create_headless_project(
    request: Request, user: User = Depends(get_user_from_api_key)
):
    data = await request.json()
    print(f"\n>>> Headless project creation request: {json.dumps(data, indent=2)}\n")
    logger.info("Headless project creation request: %s", json.dumps(data, indent=2))
    prompt = data.get("prompt")
    model = data.get("model", "gemini-3-flash-preview")
    stream = data.get("stream", False)
    body_only = data.get("body_only", False)
    image_url = data.get("image_url")

    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required")

    project = HeadlessService.create_project(user)
    HeadlessService.add_message(project.id, "user", prompt, image_url=image_url)

    return await process_generation(user, project, model, stream, body_only=body_only)


@router.post("/projects/{project_id}/iterate")
async def iterate_headless_project(
    project_id: str, request: Request, user: User = Depends(get_user_from_api_key)
):
    project = HeadlessService.get_project(project_id)
    if not project or project.user_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    data = await request.json()
    print(
        f"\n>>> Headless project iteration request (ID: {project_id}): {json.dumps(data, indent=2)}\n"
    )
    logger.info(
        "Headless project iteration request (ID: %s): %s",
        project_id,
        json.dumps(data, indent=2),
    )
    prompt = data.get("prompt")
    model = data.get("model", "gemini-3-flash-preview")
    stream = data.get("stream", False)
    body_only = data.get("body_only", False)
    image_url = data.get("image_url")

    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required")

    HeadlessService.add_message(project.id, "user", prompt, image_url=image_url)

    return await process_generation(user, project, model, stream, body_only=body_only)


@router.get("/projects/{project_id}")
async def get_headless_project(
    project_id: str,
    body_only: bool = False,
    user: User = Depends(get_user_from_api_key),
):
    project = HeadlessService.get_project(project_id)
    if not project or project.user_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    messages = (
        ProjectMessage.select()
        .where(ProjectMessage.project == project)
        .order_by(ProjectMessage.created_at)
    )

    return {
        "id": str(project.id),
        "name": project.name,
        "emoji": project.emoji,
        "created_at": project.created_at.isoformat(),
        "updated_at": project.updated_at.isoformat(),
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "html": m.html
                if body_only or not m.html
                else wrap_in_full_html(m.html),
                "image_url": m.image_url,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ],
    }


@router.delete("/projects/{project_id}/clear")
async def clear_headless_project(
    project_id: str, user: User = Depends(get_user_from_api_key)
):
    project = HeadlessService.get_project(project_id)
    if not project or project.user_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    # Delete all messages associated with the project
    ProjectMessage.delete().where(ProjectMessage.project == project).execute()

    # Reset project metadata to "scratch" state
    project.name = None
    project.emoji = None
    project.updated_at = datetime.datetime.now()
    project.save()

    return {"status": "success", "message": "Project history cleared"}


from .dummy import dummy_stream_generator, GOOD_DUMMY_RESPONSE


async def headless_dummy_stream_generator(dummy_input, input_tokens, user_id, project):
    output_tokens = 0
    full_text = ""
    # We call the existing dummy_stream_generator which yields SSE strings
    # We need to parse them back to collect full_text
    async for line in dummy_stream_generator(dummy_input):
        yield line
        if line.startswith("data: ") and not line.strip() == "data: [DONE]":
            try:
                data = json.loads(line[6:])
                content = data["choices"][0]["delta"].get("content", "")
                full_text += content
                output_tokens += 1
            except (json.JSONDecodeError, KeyError, IndexError):
                pass

    # Finalize
    html, name, emoji = extract_html_and_metadata(full_text)
    HeadlessService.add_message(project.id, "assistant", full_text, html=html)

    project.name = name or project.name
    project.emoji = emoji or project.emoji
    project.updated_at = datetime.datetime.now()
    project.save()

    Usage.update_tokens(
        user_id=user_id, input_tokens=input_tokens, output_tokens=output_tokens
    )


async def process_generation(user, project, model, stream, body_only=False):
    logger.info(
        "[Headless] Starting process_generation for project: %s, model: %s, stream: %s",
        project.id,
        model,
        stream,
    )
    print(
        f"[Headless] Starting process_generation for project: {project.id}, model: {model}, stream: {stream}"
    )
    messages = HeadlessService.get_conversation_history(project)
    input_tokens = count_tokens(messages)
    logger.debug("[Headless] Input tokens: %d", input_tokens)
    print(f"[Headless] Input tokens: {input_tokens}")

    if model.startswith("dummy"):
        if stream:
            # dummy_stream_generator expects an 'input' dict with 'model'
            dummy_input = {"model": model}
            return StreamingResponse(
                headless_dummy_stream_generator(
                    dummy_input, input_tokens, str(user.id), project
                ),
                media_type="text/event-stream",
            )
        else:
            full_text = (
                GOOD_DUMMY_RESPONSE
                if model == "dummy/good"
                else "This is a bad dummy response."
            )
            html, name, emoji = extract_html_and_metadata(full_text)
            HeadlessService.add_message(project.id, "assistant", full_text, html=html)

            project.name = name or project.name
            project.emoji = emoji or project.emoji
            project.updated_at = datetime.datetime.now()
            project.save()

            return {
                "project_id": str(project.id),
                "html": html if body_only else wrap_in_full_html(html),
                "name": name,
                "emoji": emoji,
                "raw_response": full_text,
                "metadata": {"tokens": 100},
            }

    # Generic generation logic
    try:
        if model.startswith("gpt"):
            client = openai_client
            multiplier = 20 if "gpt-4" in model else 1
        elif model.startswith("gemini") or "gemini" in model:
            # Import from server or re-initialize to avoid circularity
            from .server import litellm as litellm_client

            client = litellm_client
            multiplier = 1
        elif model.startswith("groq/"):
            client = groq_client
            model = model.replace("groq/", "")
            multiplier = 1
        else:
            # Fallback to OpenAI for now or handle Ollama
            client = openai_client
            multiplier = 1

        if stream:
            logger.info(
                "[Headless] Starting streaming generation with model: %s", model
            )
            print(f"[Headless] Starting streaming generation with model: {model}")
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                max_tokens=16384 - input_tokens - 20,
            )
            return StreamingResponse(
                headless_stream_generator(
                    response, input_tokens, str(user.id), project, multiplier
                ),
                media_type="text/event-stream",
            )
        else:
            logger.info(
                "[Headless] Starting non-streaming generation with model: %s", model
            )
            print(f"[Headless] Starting non-streaming generation with model: {model}")
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                stream=False,
                max_tokens=16384 - input_tokens - 20,
            )
            logger.info("[Headless] LLM generation complete")
            print("[Headless] LLM generation complete")
            full_text = response.choices[0].message.content
            html, name, emoji = extract_html_and_metadata(full_text)
            HeadlessService.add_message(project.id, "assistant", full_text, html=html)

            if not project.name and name:
                logger.debug(
                    "[Headless] Updating project name: %s, emoji: %s", name, emoji
                )
                print(f"[Headless] Updating project name: {name}, emoji: {emoji}")
                project.name = name
                project.emoji = emoji
                project.save()

            project.updated_at = datetime.datetime.now()
            project.save()

            logger.debug("[Headless] Updating usage tokens")
            print("[Headless] Updating usage tokens")
            Usage.update_tokens(
                user_id=str(user.id),
                input_tokens=input_tokens * multiplier,
                output_tokens=response.usage.completion_tokens * multiplier,
            )

            logger.info(
                "[Headless] process_generation complete for project: %s", project.id
            )
            print(f"[Headless] process_generation complete for project: {project.id}")
            return {
                "project_id": str(project.id),
                "html": html if body_only else wrap_in_full_html(html),
                "name": name,
                "emoji": emoji,
                "raw_response": full_text,
                "metadata": {"tokens": response.usage.total_tokens},
            }
    except Exception as e:
        logger.exception("Headless generation error: %s", e)
        print(f"[Headless] Headless generation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def headless_stream_generator(
    subscription, input_tokens, user_id, project, multiplier
):
    logger.info("[Headless] Starting stream generator for project: %s", project.id)
    print(f"[Headless] Starting stream generator for project: {project.id}")
    output_tokens = 0
    full_text = ""
    async for chunk in subscription:
        output_tokens += 1
        delta = (
            chunk.choices[0].delta.content
            if chunk.choices and chunk.choices[0].delta.content
            else ""
        )
        full_text += delta
        yield f"data: {json.dumps(chunk.model_dump(exclude_unset=True))}\n\n"

    logger.info("[Headless] Stream complete, processing result")
    print("[Headless] Stream complete, processing result")
    # Process the result
    html, name, emoji = extract_html_and_metadata(full_text)
    HeadlessService.add_message(project.id, "assistant", full_text, html=html)

    if not project.name and name:
        logger.debug("[Headless] Updating project name: %s, emoji: %s", name, emoji)
        print(f"[Headless] Updating project name: {name}, emoji: {emoji}")
        project.name = name
        project.emoji = emoji
        project.save()

    project.updated_at = datetime.datetime.now()
    project.save()

    logger.debug("[Headless] Updating usage tokens")
    print("[Headless] Updating usage tokens")
    Usage.update_tokens(
        user_id=user_id,
        input_tokens=input_tokens * multiplier,
        output_tokens=output_tokens * multiplier,
    )
    logger.info("[Headless] stream_generator complete for project: %s", project.id)
    print(f"[Headless] stream_generator complete for project: {project.id}")
    yield "data: [DONE]\n\n"

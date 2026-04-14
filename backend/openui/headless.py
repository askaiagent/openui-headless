import re
import yaml
import uuid
import datetime
from typing import Optional, Tuple, List, Dict, Any
from .db.models import Project, ProjectMessage, User
from .logs import logger

SYSTEM_PROMPT = """🎉 Greetings, TailwindCSS Virtuoso! 🌟

You've mastered the art of frontend design and TailwindCSS! Your mission is to transform detailed descriptions or compelling images into stunning HTML using the versatility of TailwindCSS. Ensure your creations are seamless in both dark and light modes! Your designs should be responsive and adaptable across all devices – be it desktop, tablet, or mobile.

*Design Guidelines:*
- Utilize placehold.co for placeholder images and descriptive alt text.
- For interactive elements, leverage modern ES6 JavaScript and native browser APIs for enhanced functionality.
- Inspired by shadcn, we provide the following colors which handle both light and dark mode:

```css
  --background
  --foreground
  --primary
	--border
  --input
  --ring
  --primary-foreground
  --secondary
  --secondary-foreground
  --accent
  --accent-foreground
  --destructive
  --destructive-foreground
  --muted
  --muted-foreground
  --card
  --card-foreground
  --popover
  --popover-foreground
```

Prefer using these colors when appropriate, for example:

```html
<button class="bg-secondary text-secondary-foreground hover:bg-secondary/80">Click me</button>
<span class="text-muted-foreground">This is muted text</span>
```

*Implementation Rules:*
- Only implement elements within the `<body>` tag, don't bother with `<html>` or `<head>` tags.
- Avoid using SVGs directly. Instead, use the `<img>` tag with a descriptive title as the alt attribute and add .svg to the placehold.co url, for example:

```html
<img aria-hidden="true" alt="magic-wand" src="/icons/24x24.svg?text=🪄" />
```

*Iterative Refinement Rules:*
- You are a precision engineer. When asked to modify existing code, you MUST perform surgical updates.
- PROTECT THE UNMODIFIED: Do not change any text, icons, emojis, or colors that were not explicitly mentioned in the request.
- PRESERVE LAYOUT: Keep the existing structure and navbars intact unless asked to restructure them.
- INCREMENTAL ADDITIONS: When asked to add a new section or page, integrate it seamlessly without rewriting or removing existing sections.
- You are FORBIDDEN from being "creative" with parts of the code you weren't asked to touch. Only change EXACTLY what is requested.
"""

FRONTMATTER_PROMPT_EXTRA = """

Always start your response with frontmatter wrapped in ---. Set name: with a 2 to 5 word description of the component. Set emoji: with an emoji for the component, i.e.:
---
name: Fancy Button
emoji: 🎉
---

"""

def extract_html_and_metadata(text: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    name = None
    emoji = None
    html = None
    
    # Try to find frontmatter
    frontmatter_match = re.search(r'^---(.*?)---', text, re.DOTALL)
    if frontmatter_match:
        try:
            metadata = yaml.safe_load(frontmatter_match.group(1))
            if isinstance(metadata, dict):
                name = metadata.get('name')
                emoji = metadata.get('emoji')
        except Exception:
            pass
        
    # Extract HTML
    # First priority: ```html blocks (handle unclosed blocks too)
    html_match = re.search(r'```html(.*?)(?:```|$)', text, re.DOTALL)
    if html_match:
        html = html_match.group(1).strip()
    else:
        # Second priority: any block that looks like HTML
        # Remove frontmatter first
        clean_text = re.sub(r'^---.*?---', '', text, flags=re.DOTALL).strip()
        # Look for code blocks if any (handle unclosed blocks too)
        code_block_match = re.search(r'```(?:html)?(.*?)(?:```|$)', clean_text, re.DOTALL)
        if code_block_match:
            html = code_block_match.group(1).strip()
        elif '<' in clean_text and '>' in clean_text:
            html = clean_text
            
    return html, name, emoji

class HeadlessService:
    @staticmethod
    def get_project(project_id: str) -> Optional[Project]:
        try:
            return Project.get_by_id(uuid.UUID(project_id).bytes)
        except (Project.DoesNotExist, ValueError):
            return None

    @staticmethod
    def create_project(user: User, name: Optional[str] = None) -> Project:
        p_uuid = uuid.uuid4()
        project = Project.create(
            id=p_uuid.bytes,
            user=user,
            name=name,
            created_at=datetime.datetime.now(),
            updated_at=datetime.datetime.now()
        )
        project.id = p_uuid
        return project

    @staticmethod
    def add_message(project_id: uuid.UUID, role: str, content: str, html: Optional[str] = None, image_url: Optional[str] = None):
        ProjectMessage.create(
            id=uuid.uuid4().bytes,
            project_id=project_id.bytes,
            role=role,
            content=content,
            html=html,
            image_url=image_url,
            created_at=datetime.datetime.now()
        )

    @staticmethod
    def get_conversation_history(project: Project) -> List[Dict[str, Any]]:
        messages = []
        # Current logic: Always start with system prompt
        messages.append({"role": "system", "content": SYSTEM_PROMPT + FRONTMATTER_PROMPT_EXTRA})
        
        db_messages = list(ProjectMessage.select().where(ProjectMessage.project == project).order_by(ProjectMessage.created_at))
        
        # Find the latest HTML to reinforce the context for iterations
        latest_html = None
        for msg in reversed(db_messages):
            if msg.role == 'assistant' and msg.html:
                latest_html = msg.html
                break

        for i, msg in enumerate(db_messages):
            if msg.role == 'user':
                content = msg.content
                # If we have previous HTML and this is the most recent user prompt, reinforce context
                if latest_html and i == len(db_messages) - 1:
                    content = f"Given the following HTML:\n\n{latest_html}\n\n{content}"

                if msg.image_url:
                    messages.append({
                        "role": "user",
                        "content": [
                            {"type": "text", "text": content},
                            {"type": "image_url", "image_url": {"url": msg.image_url}}
                        ]
                    })
                else:
                    messages.append({"role": "user", "content": content})
            else:
                messages.append({"role": "assistant", "content": msg.content})
        
        return messages

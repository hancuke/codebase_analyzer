from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Protocol

from .documents import DocumentResult, ResultKind


@dataclass(frozen=True)
class PromptBlock:
    """One caller-owned, ordered unit of authoring context."""

    name: str
    content: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("prompt block name must not be empty")


@dataclass(frozen=True)
class PromptMetadata:
    """Request-scoped metadata available to the system prompt template."""

    project: str
    language: str
    glossary: str = ""

    def __post_init__(self) -> None:
        if not self.project.strip():
            raise ValueError("prompt metadata project must not be empty")
        if not self.language.strip():
            raise ValueError("prompt metadata language must not be empty")


@dataclass(frozen=True)
class AuthorRequest:
    """All context required to produce one document fragment."""

    fragment_id: str
    blocks: tuple[PromptBlock, ...]
    metadata: PromptMetadata

    def __post_init__(self) -> None:
        names = tuple(block.name for block in self.blocks)
        if len(set(names)) != len(names):
            raise ValueError("prompt block names must be unique")


@dataclass(frozen=True)
class RenderedPrompt:
    """The two provider message roles produced from one authoring request."""

    system_prompt: str
    user_prompt: str


class PromptRenderer(Protocol):
    def render(self, request: AuthorRequest) -> RenderedPrompt:
        """Render provider system and user prompts."""
        ...


class LlmClient(Protocol):
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Generate Markdown from provider system and user prompts."""
        ...


class DocumentAuthor(Protocol):
    def author(self, request: AuthorRequest) -> DocumentResult:
        """Generate the requested fragment's desired state."""
        ...


@dataclass(frozen=True)
class FilePromptRenderer:
    """Render system and user prompts from separate caller-owned files."""

    system_prompt_path: Path
    user_prompt_path: Path

    def render(self, request: AuthorRequest) -> RenderedPrompt:
        contexts = "\n\n".join(
            f"<Context_{block.name}>\n{block.content}\n</Context_{block.name}>"
            for block in request.blocks
        )
        return RenderedPrompt(
            system_prompt=Template(
                self.system_prompt_path.read_text(encoding="utf-8")
            ).substitute(
                project=request.metadata.project,
                language=request.metadata.language,
                glossary=request.metadata.glossary,
            ),
            user_prompt=Template(
                self.user_prompt_path.read_text(encoding="utf-8")
            ).substitute(contexts=contexts),
        )


@dataclass(frozen=True)
class LlmDocumentAuthor:
    """Compose prompt rendering, LLM completion, and fragment-result validation."""

    renderer: PromptRenderer
    client: LlmClient

    def author(self, request: AuthorRequest) -> DocumentResult:
        prompt = self.renderer.render(request)
        markdown = _sanitize_markdown(prompt, self.client)
        return DocumentResult(
            fragment_id=request.fragment_id,
            kind=ResultKind.UPSERT,
            markdown=markdown,
        )


def _sanitize_markdown(prompt: RenderedPrompt, client: LlmClient) -> str:
    markdown = client.generate(prompt.system_prompt, prompt.user_prompt).strip()
    if markdown.startswith("```"):
        lines = markdown.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        markdown = "\n".join(lines).strip()
    return markdown

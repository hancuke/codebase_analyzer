from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from xml.etree import ElementTree

from document_updater import DocumentResult, ResultKind


@dataclass(frozen=True)
class PromptBlock:
    """One caller-owned, ordered unit of authoring context."""

    name: str
    content: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("prompt block name must not be empty")


@dataclass(frozen=True)
class AuthorRequest:
    """All context required to produce one document fragment."""

    fragment_id: str
    instructions: str
    blocks: tuple[PromptBlock, ...]

    def __post_init__(self) -> None:
        names = tuple(block.name for block in self.blocks)
        if len(set(names)) != len(names):
            raise ValueError("prompt block names must be unique")


class PromptRenderer(Protocol):
    def render(self, request: AuthorRequest) -> str:
        """Render one complete provider prompt."""
        ...


class LlmClient(Protocol):
    def generate(self, prompt: str) -> str:
        """Generate Markdown from one complete prompt."""
        ...


class DocumentAuthor(Protocol):
    def author(self, request: AuthorRequest) -> DocumentResult:
        """Generate the requested fragment's desired state."""
        ...


@dataclass(frozen=True)
class XmlPromptRenderer:
    """A deterministic default renderer for structured prompt blocks."""

    def render(self, request: AuthorRequest) -> str:
        root = ElementTree.Element("author_request")
        instructions = ElementTree.SubElement(root, "instructions")
        instructions.text = request.instructions
        blocks = ElementTree.SubElement(root, "blocks")
        for block in request.blocks:
            element = ElementTree.SubElement(blocks, "block", {"name": block.name})
            element.text = block.content
        ElementTree.indent(root, space="  ")
        return ElementTree.tostring(root, encoding="unicode") + "\n"


@dataclass(frozen=True)
class LlmDocumentAuthor:
    """Compose prompt rendering, LLM completion, and fragment-result validation."""

    renderer: PromptRenderer
    client: LlmClient

    def author(self, request: AuthorRequest) -> DocumentResult:
        markdown = _sanitize_markdown(self.renderer.render(request), self.client)
        return DocumentResult(
            fragment_id=request.fragment_id,
            kind=ResultKind.UPSERT,
            markdown=markdown,
        )


def _sanitize_markdown(prompt: str, client: LlmClient) -> str:
    markdown = client.generate(prompt).strip()
    if markdown.startswith("```"):
        lines = markdown.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        markdown = "\n".join(lines).strip()
    return markdown

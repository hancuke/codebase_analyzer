"""Reference LLM client abstraction and mock implementation for CodeGraph documentation.

This module illustrates how to connect `document_updater` to LLM backends:
1. `PromptRenderer` embeds XML reference data into a Markdown prompt template.
2. `LlmClient` abstracts external LLM providers (Mock, OpenAI, etc.).
3. `ResponseSanitizer` cleans markdown code fences and reserved markers.
4. `EntryDocumentAuthor` coordinates prompt rendering, LLM invocation, and output cleaning
   for a single `LlmEntryContext`, providing a drop-in `content_for` callable.
5. `MultiPromptEntryDocumentAuthor` runs multiple entry-scoped analyses and synthesizes
   their results into the same single-document callable contract.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable
from xml.etree import ElementTree

from document_updater.models import LlmEntryContext

# Default prompt template following the {{ reference_data }} convention
DEFAULT_ENTRY_PROMPT_TEMPLATE = """\
You are an expert technical documentation author.
Analyze the following entry point code context and change details, then write a concise,
accurate Markdown documentation section for this entry point.

Guidelines:
- Output only the documentation body (do not repeat level-1 or level-2 headers).
- Describe the primary purpose, invocation behavior, and key dependencies.
- For updates, highlight relevant changes based on the function diffs.
- Do NOT include any '<!-- codegraph:' comments or markdown code fences enclosing the whole answer.

{{ reference_data }}
"""

DEFAULT_SYSTEM_PROMPT = (
    "You are a precise technical writer specializing in codebase architecture "
    "and function-level documentation."
)

DEFAULT_SYNTHESIS_PROMPT_TEMPLATE = """\
You are given several independent analyses of the same entry point.
Reconcile them into one concise, accurate Markdown documentation section.

Guidelines:
- Output only the documentation body (do not repeat level-1 or level-2 headers).
- Resolve overlap and conflicts instead of concatenating the analyses.
- Base the final document on the supplied reference data.
- Do NOT include any '<!-- codegraph:' comments or markdown code fences enclosing the whole answer.

{{ reference_data }}

{{ analysis_results }}
"""


@runtime_checkable
class LlmClient(Protocol):
    """Abstract interface for LLM completion services."""

    def generate(self, prompt: str, *, system_prompt: str | None = None) -> str:
        """Send prompt to LLM and return the generated text response."""
        ...


@dataclass(frozen=True)
class PromptRenderer:
    """Renders prompt templates with XML reference data."""

    template: str = DEFAULT_ENTRY_PROMPT_TEMPLATE
    placeholder: str = "{{ reference_data }}"

    def render(self, context: LlmEntryContext) -> str:
        """Render the complete prompt for a single entry context."""
        reference_data_xml = context.to_reference_data_xml()
        return self.template.replace(self.placeholder, reference_data_xml)


@dataclass(frozen=True)
class EntryAnalysisPrompt:
    """One named analysis angle applied to an entry context."""

    name: str
    template: str
    system_prompt: str | None = DEFAULT_SYSTEM_PROMPT

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("analysis prompt name must not be empty")

    def render(self, context: LlmEntryContext) -> str:
        return PromptRenderer(template=self.template).render(context)


@dataclass(frozen=True)
class EntryAnalysisResult:
    """One named intermediate analysis used by the synthesis prompt."""

    name: str
    content: str


def render_analysis_results_xml(
    results: Sequence[EntryAnalysisResult],
) -> str:
    """Render ordered intermediate analyses with unambiguous boundaries."""
    root = ElementTree.Element("analysis_results")
    for result in results:
        analysis = ElementTree.SubElement(
            root,
            "analysis",
            {"name": result.name},
        )
        content = ElementTree.SubElement(analysis, "content")
        content.text = result.content
    ElementTree.indent(root, space="  ")
    return ElementTree.tostring(root, encoding="unicode") + "\n"


@dataclass(frozen=True)
class SynthesisPromptRenderer:
    """Render reference data and ordered analyses into a final prompt."""

    template: str = DEFAULT_SYNTHESIS_PROMPT_TEMPLATE
    reference_placeholder: str = "{{ reference_data }}"
    analyses_placeholder: str = "{{ analysis_results }}"

    def render(
        self,
        context: LlmEntryContext,
        results: Sequence[EntryAnalysisResult],
    ) -> str:
        return (
            self.template.replace(
                self.reference_placeholder,
                context.to_reference_data_xml(),
            ).replace(
                self.analyses_placeholder,
                render_analysis_results_xml(results),
            )
        )


@dataclass(frozen=True)
class ResponseSanitizer:
    """Sanitizes raw LLM output into clean Markdown for EntryDocumentResult."""

    strip_fences: bool = True
    sanitize_reserved_markers: bool = True

    def sanitize(self, raw_content: str) -> str:
        """Clean code fences and reserved markers from LLM response."""
        content = raw_content.strip()

        if self.strip_fences:
            # Strip outer ```markdown ... ``` or ``` ... ``` if present
            if content.startswith("```"):
                lines = content.splitlines()
                if lines and lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                content = "\n".join(lines).strip()

        if self.sanitize_reserved_markers:
            # Codegraph reserves <!-- codegraph:... --> for structural indexing.
            # Strip or escape any accidentally generated markers.
            content = re.sub(r"<!--\s*codegraph:[^>]*-->", "", content).strip()

        return content


@dataclass
class MockLlmClient:
    """Deterministic Mock LLM client for testing and demonstration.

    Features:
    - Canned responses for explicit entry IDs.
    - Dynamic rule-based generator from XML reference data.
    - Full call history audit log.
    """

    canned_responses: Mapping[str, str] = field(default_factory=dict)
    history: list[tuple[str, str | None]] = field(default_factory=list)

    def generate(self, prompt: str, *, system_prompt: str | None = None) -> str:
        """Record the call and return a deterministic mock documentation body."""
        self.history.append((prompt, system_prompt))

        # Check for canned responses matching any key in prompt
        for entry_id, canned_text in self.canned_responses.items():
            if entry_id in prompt:
                return canned_text

        return self._generate_dynamic_response(prompt)

    def _generate_dynamic_response(self, prompt: str) -> str:
        """Parse XML reference data from the prompt and craft structured markdown."""
        action = "create"
        entry_id = "unknown_entry"
        functions: list[str] = []

        try:
            # Extract <reference_data>...</reference_data> segment from prompt
            start = prompt.find("<reference_data>")
            end = prompt.rfind("</reference_data>")
            if start != -1 and end != -1:
                xml_content = prompt[start : end + len("</reference_data>")]
                root = ElementTree.fromstring(xml_content)
                action_el = root.find("action")
                if action_el is not None and action_el.text:
                    action = action_el.text.strip()
                entry_el = root.find(".//entry/entry_id")
                if entry_el is not None and entry_el.text:
                    entry_id = entry_el.text.strip()
                seen_functions: set[str] = set()
                for fn_el in root.findall(".//functions/function/function_id"):
                    if fn_el.text and fn_el.text.strip() not in seen_functions:
                        fn_id = fn_el.text.strip()
                        seen_functions.add(fn_id)
                        functions.append(fn_id)
        except Exception:
            pass

        if action == "create":
            doc_lines = [
                f"Initial documentation for `{entry_id}`.",
                "",
                "### Overview",
                f"Entry point `{entry_id}` handles user or external invocation.",
            ]
            if functions:
                doc_lines.extend([
                    "",
                    "### Dependencies",
                    f"Interacts with: {', '.join(f'`{f}`' for f in functions)}.",
                ])
            return "\n".join(doc_lines)

        if action == "update":
            return "\n".join([
                f"Updated documentation for `{entry_id}`.",
                "",
                "### Overview",
                f"Maintains behavior for `{entry_id}` with recent updates applied.",
                "",
                "### Changes",
                "- Synchronized implementation and dependency references with latest source diff.",
            ])

        return f"Documentation for `{entry_id}` (action: {action})."


class OpenAiLlmClient:
    """Reference adapter demonstrating real OpenAI API integration.

    Requires `openai` package installed and `OPENAI_API_KEY` configured.
    """

    def __init__(
        self,
        *,
        model: str = "gpt-4o",
        api_key: str | None = None,
        temperature: float = 0.2,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.temperature = temperature

    def generate(self, prompt: str, *, system_prompt: str | None = None) -> str:
        """Invoke OpenAI ChatCompletion API."""
        try:
            import openai
        except ImportError as err:
            raise RuntimeError(
                "The `openai` package is required to use OpenAiLlmClient. "
                "Install it via `pip install openai` or `uv add openai`."
            ) from err

        client = openai.OpenAI(api_key=self.api_key)
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        response = client.chat.completions.create(
            model=self.model,
            messages=messages,  # type: ignore[arg-type]
            temperature=self.temperature,
        )
        return response.choices[0].message.content or ""


@dataclass
class EntryDocumentAuthor:
    """Coordinates prompt rendering, LLM invocation, and output sanitization for single entries."""

    client: LlmClient
    renderer: PromptRenderer = field(default_factory=PromptRenderer)
    sanitizer: ResponseSanitizer = field(default_factory=ResponseSanitizer)
    system_prompt: str | None = DEFAULT_SYSTEM_PROMPT

    def author(self, context: LlmEntryContext) -> str:
        """Generate clean documentation for one LlmEntryContext.

        This method conforms to the `Callable[[LlmEntryContext], str]` interface required
        by `author_entry_documents`.
        """
        prompt = self.renderer.render(context)
        raw_response = self.client.generate(
            prompt,
            system_prompt=self.system_prompt,
        )
        return self.sanitizer.sanitize(raw_response)


@dataclass(frozen=True)
class MultiPromptEntryDocumentAuthor:
    """Analyze one entry from multiple angles, then synthesize one document."""

    client: LlmClient
    analysis_prompts: tuple[EntryAnalysisPrompt, ...]
    synthesis_renderer: SynthesisPromptRenderer = field(
        default_factory=SynthesisPromptRenderer
    )
    sanitizer: ResponseSanitizer = field(default_factory=ResponseSanitizer)
    synthesis_system_prompt: str | None = DEFAULT_SYSTEM_PROMPT

    def __post_init__(self) -> None:
        if not self.analysis_prompts:
            raise ValueError("at least one analysis prompt is required")
        names = tuple(prompt.name for prompt in self.analysis_prompts)
        if len(set(names)) != len(names):
            raise ValueError("analysis prompt names must be unique")

    def author(self, context: LlmEntryContext) -> str:
        """Return one clean Markdown body after analysis and synthesis."""
        results = tuple(
            EntryAnalysisResult(
                name=analysis_prompt.name,
                content=self.client.generate(
                    analysis_prompt.render(context),
                    system_prompt=analysis_prompt.system_prompt,
                ).strip(),
            )
            for analysis_prompt in self.analysis_prompts
        )
        raw_response = self.client.generate(
            self.synthesis_renderer.render(context, results),
            system_prompt=self.synthesis_system_prompt,
        )
        return self.sanitizer.sanitize(raw_response)

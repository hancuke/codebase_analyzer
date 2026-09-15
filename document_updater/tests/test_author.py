from pathlib import Path

from document_updater import (
    AuthorRequest,
    DocumentResult,
    FilePromptRenderer,
    LlmDocumentAuthor,
    PromptBlock,
    PromptMetadata,
    ResultKind,
)


class RecordingClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[tuple[str, str]] = []

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        self.prompts.append((system_prompt, user_prompt))
        return self.response


def test_author_renders_external_template_and_returns_upsert_result(
    tmp_path: Path,
) -> None:
    system_prompt_path = tmp_path / "system-prompt.txt"
    user_prompt_path = tmp_path / "user-prompt.template"
    user_prompt_path.write_text(
        "<Instructions>\n"
        "Write concise technical documentation.\n"
        "</Instructions>\n\n"
        "$contexts\n",
        encoding="utf-8",
    )
    system_prompt_path.write_text(
        "Project: $project\n"
        "Language: $language\n"
        "Glossary:\n$glossary\n",
        encoding="utf-8",
    )
    client = RecordingClient("```markdown\n## Save\n\nSaves the order.\n```")
    author = LlmDocumentAuthor(
        FilePromptRenderer(system_prompt_path, user_prompt_path),
        client,
    )

    result = author.author(
        AuthorRequest(
            fragment_id="entry:save-order",
            blocks=(
                PromptBlock("entry", "save-order"),
                PromptBlock("context", "Public Sub SaveOrder()"),
            ),
            metadata=PromptMetadata(
                project="Order System",
                language="VBA",
                glossary="- Entry: a documentation entry point.",
            ),
        )
    )

    assert result.kind is ResultKind.UPSERT
    assert result.markdown == "## Save\n\nSaves the order."
    assert client.prompts[0] == (
        "Project: Order System\n"
        "Language: VBA\n"
        "Glossary:\n"
        "- Entry: a documentation entry point.\n",
        "<Instructions>\n"
        "Write concise technical documentation.\n"
        "</Instructions>\n\n"
        "<Context_entry>\n"
        "save-order\n"
        "</Context_entry>\n\n"
        "<Context_context>\n"
        "Public Sub SaveOrder()\n"
        "</Context_context>\n"
    )

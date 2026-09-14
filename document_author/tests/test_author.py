from document_author import (
    AuthorRequest,
    LlmDocumentAuthor,
    PromptBlock,
    XmlPromptRenderer,
)
from document_updater import ResultKind


class RecordingClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response


def test_author_renders_ordered_blocks_and_returns_upsert_result() -> None:
    client = RecordingClient("```markdown\n## Save\n\nSaves the order.\n```")
    author = LlmDocumentAuthor(XmlPromptRenderer(), client)

    result = author.author(
        AuthorRequest(
            fragment_id="entry:save-order",
            instructions="Write concise technical documentation.",
            blocks=(
                PromptBlock("entry", "save-order"),
                PromptBlock("context", "Public Sub SaveOrder()"),
            ),
        )
    )

    assert result.kind is ResultKind.UPSERT
    assert result.markdown == "## Save\n\nSaves the order."
    assert client.prompts[0].index('name="entry"') < client.prompts[0].index(
        'name="context"'
    )

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure examples module is discoverable for tests
examples_path = Path(__file__).resolve().parents[2] / "examples"
if str(examples_path) not in sys.path:
    sys.path.insert(0, str(examples_path))

from codegraph import SourceFile, VbaAnalyzer
from document_updater import (
    DocumentAction,
    EntryDocumentResult,
    build_llm_context,
    create_entry_plans,
)
from change_analyzer import analyze_changes
from filetracker import FileTracker
from llm_client import (
    EntryAnalysisPrompt,
    EntryDocumentAuthor,
    LlmClient,
    MockLlmClient,
    MultiPromptEntryDocumentAuthor,
    OpenAiLlmClient,
    PromptRenderer,
    ResponseSanitizer,
)


def _write(root: Path, name: str, content: str) -> None:
    (root / name).write_text(content, encoding="utf-8")


def _sources(root: Path) -> tuple[SourceFile, ...]:
    return tuple(
        SourceFile(path.name, path.read_text(encoding="utf-8"))
        for path in sorted(root.glob("*"))
        if path.suffix.casefold() in {".bas", ".frm"}
    )


def test_llm_client_protocol_conformance() -> None:
    client = MockLlmClient()
    assert isinstance(client, LlmClient)


def test_prompt_renderer_substitutes_reference_data_xml(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "frmMain.frm",
        "Private Sub bOpen_Click()\nEnd Sub\n",
    )
    tracker = FileTracker(str(tmp_path))
    report = analyze_changes(tracker.scan(), _sources(tmp_path), [VbaAnalyzer()])
    plans = create_entry_plans(report)
    assert len(plans) == 1

    context = build_llm_context(plans[0], old_document="")
    renderer = PromptRenderer(
        template="Instructions:\n{{ reference_data }}\nEnd."
    )
    rendered = renderer.render(context)

    assert "Instructions:" in rendered
    assert "<reference_data>" in rendered
    assert "<action>create</action>" in rendered
    assert "<entry_id>vba:frmMain:bOpen_Click</entry_id>" in rendered
    assert "End." in rendered


def test_response_sanitizer_removes_fences_and_reserved_markers() -> None:
    sanitizer = ResponseSanitizer()

    # Strips markdown fences
    fenced_content = "```markdown\n# Doc\nSome body\n```"
    assert sanitizer.sanitize(fenced_content) == "# Doc\nSome body"

    # Strips reserved codegraph markers
    polluted_content = (
        "Doc line.\n<!-- codegraph:entry:start entry_id=\"evil\" -->\nMore doc."
    )
    cleaned = sanitizer.sanitize(polluted_content)
    assert "<!-- codegraph:" not in cleaned
    assert "Doc line." in cleaned
    assert "More doc." in cleaned

    # Validates that sanitized text produces valid EntryDocumentResult
    result = EntryDocumentResult("vba:frm:test", cleaned)
    assert result.markdown == cleaned


def test_mock_llm_client_dynamic_generation_and_history(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "frmMain.frm",
        "Private Sub bSubmit_Click()\n"
        "    Call ValidateInput\n"
        "End Sub\n",
    )
    _write(
        tmp_path,
        "modHelper.bas",
        "Public Sub ValidateInput()\nEnd Sub\n",
    )
    tracker = FileTracker(str(tmp_path))
    report = analyze_changes(tracker.scan(), _sources(tmp_path), [VbaAnalyzer()])
    plans = create_entry_plans(report)
    assert len(plans) == 1

    client = MockLlmClient()
    author = EntryDocumentAuthor(client=client)

    context = build_llm_context(plans[0], old_document="")
    doc = author.author(context)

    assert "Initial documentation for `vba:frmMain:bSubmit_Click`" in doc
    assert "### Overview" in doc
    assert "### Dependencies" in doc
    assert len(client.history) == 1
    assert "vba:frmMain:bSubmit_Click" in client.history[0][0]


def test_mock_llm_client_canned_response() -> None:
    canned = {"vba:frmCustom:bRun": "Custom canned documentation for bRun."}
    client = MockLlmClient(canned_responses=canned)
    response = client.generate("Please document entry vba:frmCustom:bRun now.")
    assert response == "Custom canned documentation for bRun."


def test_openai_client_raises_import_error_when_missing() -> None:
    client = OpenAiLlmClient(api_key="test-key")
    # If openai is not installed in the environment, it should give a clear RuntimeError
    try:
        import openai  # noqa: F401
    except ImportError:
        with pytest.raises(RuntimeError, match="openai.*required"):
            client.generate("Test prompt")


class ScriptedLlmClient:
    def __init__(self, responses: list[str | Exception]) -> None:
        self.responses = iter(responses)
        self.history: list[tuple[str, str | None]] = []

    def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
    ) -> str:
        self.history.append((prompt, system_prompt))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def test_multi_prompt_author_analyzes_then_synthesizes(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "frmMain.frm",
        "Private Sub bSubmit_Click()\nEnd Sub\n",
    )
    tracker = FileTracker(str(tmp_path))
    report = analyze_changes(tracker.scan(), _sources(tmp_path), [VbaAnalyzer()])
    context = build_llm_context(create_entry_plans(report)[0])
    client = ScriptedLlmClient(
        [
            "Behavior findings.",
            "Change-impact findings.",
            "```markdown\nFinal entry documentation.\n```",
        ]
    )
    author = MultiPromptEntryDocumentAuthor(
        client=client,
        analysis_prompts=(
            EntryAnalysisPrompt(
                name="behavior",
                template="Analyze behavior.\n{{ reference_data }}",
            ),
            EntryAnalysisPrompt(
                name="change-impact",
                template="Analyze changes.\n{{ reference_data }}",
            ),
        ),
    )

    document = author.author(context)

    assert document == "Final entry documentation."
    assert len(client.history) == 3
    assert "Analyze behavior." in client.history[0][0]
    assert "Analyze changes." in client.history[1][0]
    for prompt, _ in client.history:
        assert "<entry_id>vba:frmMain:bSubmit_Click</entry_id>" in prompt
    synthesis_prompt = client.history[2][0]
    assert '<analysis name="behavior">' in synthesis_prompt
    assert "<content>Behavior findings.</content>" in synthesis_prompt
    assert '<analysis name="change-impact">' in synthesis_prompt
    assert "<content>Change-impact findings.</content>" in synthesis_prompt
    assert synthesis_prompt.index('name="behavior"') < synthesis_prompt.index(
        'name="change-impact"'
    )


@pytest.mark.parametrize(
    ("analysis_prompts", "message"),
    [
        ((), "at least one analysis prompt"),
        (
            (
                EntryAnalysisPrompt("same", "{{ reference_data }}"),
                EntryAnalysisPrompt("same", "{{ reference_data }}"),
            ),
            "names must be unique",
        ),
    ],
)
def test_multi_prompt_author_rejects_invalid_prompt_configuration(
    analysis_prompts: tuple[EntryAnalysisPrompt, ...],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        MultiPromptEntryDocumentAuthor(
            client=MockLlmClient(),
            analysis_prompts=analysis_prompts,
        )


def test_analysis_prompt_requires_nonempty_name() -> None:
    with pytest.raises(ValueError, match="name must not be empty"):
        EntryAnalysisPrompt(" ", "{{ reference_data }}")


def test_multi_prompt_author_fails_fast_before_synthesis(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "frmMain.frm",
        "Private Sub bSubmit_Click()\nEnd Sub\n",
    )
    tracker = FileTracker(str(tmp_path))
    report = analyze_changes(tracker.scan(), _sources(tmp_path), [VbaAnalyzer()])
    context = build_llm_context(create_entry_plans(report)[0])
    client = ScriptedLlmClient(
        ["Behavior findings.", RuntimeError("analysis failed")]
    )
    author = MultiPromptEntryDocumentAuthor(
        client=client,
        analysis_prompts=(
            EntryAnalysisPrompt("behavior", "{{ reference_data }}"),
            EntryAnalysisPrompt("change-impact", "{{ reference_data }}"),
        ),
    )

    with pytest.raises(RuntimeError, match="analysis failed"):
        author.author(context)

    assert len(client.history) == 2

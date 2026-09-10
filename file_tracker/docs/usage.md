# FileTracker Usage Guide

`filetracker` is a pure-Python, mini Git-style library for tracking changes to files and
code symbols. It provides only change data and baseline transactions; callers implement
documentation updates, LLM calls, and other business logic.

## Installation

```bash
pip install -e .
filetracker --help
```

## File-Level API

```python
from filetracker import ChangeStatus, FileTracker

tracker = FileTracker(
    root="./src",
    exclude_patterns=["**/__pycache__/**", "**/*.pyc"],
)

change_set = tracker.scan()
for file_change in change_set.files:
    print(file_change.status.value, file_change.path)
    if file_change.has_text_diff:
        print(file_change.diff())

# Verify that the working tree is still the version that was scanned before committing.
tracker.commit(
    message="processed source changes",
    expected_revision=change_set.working_revision,
    expected_baseline_revision=change_set.baseline_revision,
)
```

`FileChange.baseline_content` and `FileChange.working_content` are `ContentSnapshot`
instances. Rather than using ambiguous `None` values for every non-text case:

```python
if file_change.working_content.is_text:
    source = file_change.working_content.text
else:
    print(file_change.working_content.availability.value)
```

`undo()` restores only the previous baseline and never changes the working directory:

```python
if tracker.undo():
    print("Baseline restored")
```

A baseline that exists but is corrupted raises `BaselineError`; a revision mismatch raises
`RevisionConflictError`. Both prevent unreliable state from being committed as a new baseline.

## Symbol-Level API

`SymbolTracker` is a library-level feature; the CLI does not expose a command for it. The
caller scans first, then extracts symbols for selected individual files:

```python
from filetracker import ChangeStatus, FileTracker
from symbol_tracker import (
    ParserRegistry,
    SymbolExtractionOptions,
    SymbolTracker,
)
from symbol_tracker.parsers.python_parser import PythonASTParser

file_tracker = FileTracker(root="./src")
parser_registry = ParserRegistry()
parser_registry.register(".py", PythonASTParser())
symbol_tracker = SymbolTracker(parser_registry)

for file_change in file_tracker.scan():
    if (
        file_change.status is ChangeStatus.MODIFIED
        and file_change.path.suffix == ".py"
    ):
        file_symbol_changes = symbol_tracker.extract_symbol_changes(file_change)
        for symbol_change in file_symbol_changes.symbol_changes:
            print(symbol_change.status.value, symbol_change.symbol_name)
            print(symbol_change.diff())
```

By default, only module-level functions and class methods are returned to avoid duplicate
reports for container symbols. To include classes and nested functions:

```python
options = SymbolExtractionOptions(
    include_classes=True,
    include_nested_functions=True,
)
file_symbol_changes = symbol_tracker.extract_symbol_changes(file_change, options)
```

## CLI

The CLI displays file-level data only:

```bash
filetracker scan --root ./src --exclude "**/*.pyc"
filetracker diff --root ./src
filetracker commit --root ./src -m "initial baseline"
filetracker undo --root ./src
```

| Command | Description |
| --- | --- |
| `scan` | Lists added, modified, and deleted files |
| `diff` | Outputs unified diffs for available text files |
| `commit` | Saves the working directory as a new baseline |
| `undo` | Restores the previous baseline |

Binary, undecodable, and unreadable files are still listed by `scan`; `diff` reports only
their status and does not fabricate a text diff.

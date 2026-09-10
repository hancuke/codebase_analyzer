# FileTracker and SymbolTracker Design

## Responsibilities

This is a mini Git-style tracking library. It does not generate documentation or call
external services.

- **FileTracker**: Scans file changes, saves baselines, generates file-level diffs, and
  commits and rolls back baselines.
- **SymbolTracker**: Extracts code symbol changes from a scanned `FileChange`; it does not
  read the file system.
- **Caller**: Selects files, invokes symbol extraction, runs its own business logic, and
  decides when to commit.
- **CLI**: Provides only file-level `scan`, `diff`, `commit`, and `undo`.

## Data Flow

```python
change_set = file_tracker.scan()

for file_change in change_set.files:
    if file_change.status is ChangeStatus.MODIFIED:
        symbol_changes = symbol_tracker.extract_symbol_changes(file_change)
        process(symbol_changes)

file_tracker.commit(expected_revision=change_set.working_revision)
```

Scanning, symbol extraction, and business processing all use an individual file as their
boundary, preventing context from separate files from being mixed into the same result.

## File Snapshots

`scan()` returns an immutable `ChangeSet`:

```python
@dataclass(frozen=True)
class ChangeSet:
    files: tuple[FileChange, ...]
    baseline_revision: str
    working_revision: str


@dataclass(frozen=True)
class FileChange:
    path: Path
    status: ChangeStatus
    baseline_state: FileState | None
    working_state: FileState | None
    baseline_content: ContentSnapshot
    working_content: ContentSnapshot
```

`files` is sorted by POSIX relative path. `baseline_revision` identifies the baseline at
scan time; `working_revision` is calculated from the paths and content hashes of all working
tree files. When passed to
`commit(expected_revision=..., expected_baseline_revision=...)`, the commit is rejected if
files or the baseline changed after the scan.

`ContentSnapshot.availability` explicitly distinguishes:

- `ABSENT`: This side does not exist, such as the baseline for an added file.
- `TEXT`: Available UTF-8 text, stored in `.text`.
- `BINARY`, `UNDECODABLE`, and `UNREADABLE`: Reasons the content cannot be safely treated as
  text.

`FileChange.diff()` returns a unified diff only when both sides are text or `ABSENT`;
otherwise, it returns an empty string. Callers can use `.has_text_diff` to distinguish this
case.

## Baseline Integrity

A missing baseline manifest represents the initial state. If one exists but cannot be read,
has invalid JSON, or has an invalid structure, the library raises `BaselineError`; it never
silently resets it to an empty baseline.

Each commit first atomically saves a snapshot of the current manifest, then atomically writes
the new manifest and records the snapshot name in the new manifest's `undo_snapshot`. `undo()`
follows that link to restore the previous manifest; the working directory is not changed. If
writing the new manifest fails, the old manifest remains valid and may leave only an orphaned
snapshot that can safely be ignored.

## Symbol Extraction

```python
@dataclass(frozen=True)
class FileSymbolChanges:
    file_change: FileChange
    symbol_changes: tuple[SymbolChange, ...]


symbol_changes = symbol_tracker.extract_symbol_changes(
    file_change,
    SymbolExtractionOptions(
        include_classes=False,
        include_nested_functions=False,
    ),
)
```

Added files parse only working text, deleted files parse only baseline text, and modified files
compare both sides. By default, results include module-level functions and class methods, but
not class containers or nested functions. This avoids reporting a changed method, its class,
and its enclosing function as duplicates. Enable the relevant options when the full tree is
needed.

The Python parser preserves decorators, type annotations, default values, and return types in
`SymbolState.declaration`; `SymbolState.content` and `body_hash` cover the complete source
fragment.

## Paths and Exclusion Rules

Every pattern in `FileTracker(root, exclude_patterns=...)` matches POSIX relative paths. For
example, `generated` excludes that directory, `generated/**` excludes its contents, and
`**/*.pyc` excludes pyc files in nested directories. Absolute paths are generated only by
calling `FileTracker.resolve_path(relative_path)`.

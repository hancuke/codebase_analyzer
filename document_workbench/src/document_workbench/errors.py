class WorkbenchError(Exception):
    """Base class for workbench failures."""


class WorkbenchBusyError(WorkbenchError):
    """Raised when another process holds the workbench lock."""


class WorkbenchCorruptError(WorkbenchError):
    """Raised when persisted workbench state or data is invalid."""


class UnsupportedWorkbenchVersionError(WorkbenchCorruptError):
    """Raised when the workbench manifest uses an unsupported schema."""


class WorkbenchStateError(WorkbenchError):
    """Raised when an operation is invalid for the current workbench state."""


class InvalidUnitIdError(ValueError, WorkbenchError):
    """Raised when a unit ID is not a canonical relative POSIX path."""

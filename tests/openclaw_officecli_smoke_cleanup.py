from collections.abc import Callable
from dataclasses import dataclass

CLEANUP_PROBE = 'chmod -R a+rwX "$OPENCLAW_STATE_DIR"'


@dataclass(frozen=True, slots=True)
class CleanupFailure:
    phase: str
    error: Exception


type CleanupOperation[T] = tuple[str, Callable[[], T]]


def run_cleanup[T](operations: tuple[CleanupOperation[T], ...]) -> tuple[CleanupFailure, ...]:
    """Attempt every cleanup operation and retain failures for the caller."""
    failures: list[CleanupFailure] = []
    for phase, operation in operations:
        try:
            operation()
        except Exception as error:
            # Teardown must retain this failure while attempting every remaining resource.
            failures.append(CleanupFailure(phase, error))
    return tuple(failures)


def cleanup_error_message(failures: tuple[CleanupFailure, ...], active_error: BaseException | None) -> str | None:
    """Return cleanup failures or attach them to the active test exception."""
    if not failures:
        return None
    message = "\n".join(f"{failure.phase}: {failure.error!r}" for failure in failures)
    if active_error is None:
        return message
    active_error.add_note(f"Cleanup failures:\n{message}")
    return None

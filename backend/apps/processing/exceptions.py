class ProcessingError(Exception):
    """Raised when source content cannot satisfy the processing contract."""


class ProcessingAlreadyQueued(ProcessingError):
    """Raised when a corpus already has a pending or running task."""


class RetryableProcessingError(ProcessingError):
    """Raised for transient worker failures that are safe to retry."""

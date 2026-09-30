class ParseAdapterError(ValueError):
    """Sanitized parser failure safe to persist on a parse run."""

    code = "parser_error"
    retryable = False
    fallback_allowed = False

    def __init__(self, message: str = "Document parsing failed", *, code: str | None = None):
        super().__init__(message)
        if code:
            self.code = code


class TransientParseError(ParseAdapterError):
    code = "parser_temporarily_unavailable"
    retryable = True
    fallback_allowed = True


class CircuitOpenError(TransientParseError):
    """No provider call was made; defer without consuming the retry budget."""

    code = "mineru_circuit_open"


class PermanentParseError(ParseAdapterError):
    code = "parser_rejected_document"


class ProviderResponseError(ParseAdapterError):
    code = "parser_invalid_response"
    fallback_allowed = True


class LowQualityParseError(ParseAdapterError):
    code = "parser_low_quality"
    fallback_allowed = True


class UnsafeDocumentError(PermanentParseError):
    code = "document_integrity_failed"

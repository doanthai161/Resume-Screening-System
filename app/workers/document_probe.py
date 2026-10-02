"""Bounded-lifetime native document inspection child; stdout is a JSON protocol."""

import json
import logging
import sys
from dataclasses import asdict

from app.core.config import settings
from app.workers.document import ResumeDocument, _analyze_docx, _analyze_pdf
from app.workers.errors import PermanentParseError


def main() -> None:
    logging.disable(logging.CRITICAL)
    for name, value in json.loads(sys.argv[2]).items():
        setattr(settings, name, value)
    content = sys.stdin.buffer.read(settings.MAX_RESUME_SIZE + 1)
    extension = sys.argv[1]
    if len(content) > settings.MAX_RESUME_SIZE:
        result = {"error_code": "document_integrity_failed"}
    else:
        document = ResumeDocument(None, content, extension, "document" + extension)
        try:
            analyzer = _analyze_pdf if extension == ".pdf" else _analyze_docx
            result = asdict(analyzer(document))
        except PermanentParseError as exc:
            result = {"error_code": exc.code}
        except Exception:
            result = {"error_code": "document_inspection_failed"}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    main()

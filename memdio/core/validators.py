"""Input validation for memdio."""

import re

MAX_CONTENT_SIZE = 1_048_576  # 1 MB
MAX_QUERY_LENGTH = 1_000
MAX_TAGS_LENGTH = 500

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


class ValidationError(ValueError):
    """Raised when input validation fails."""


def validate_content(content: str) -> str:
    if not isinstance(content, str):
        raise ValidationError("Content must be a string")
    if not content.strip():
        raise ValidationError("Content must not be empty")
    if len(content.encode("utf-8")) > MAX_CONTENT_SIZE:
        raise ValidationError(
            f"Content exceeds maximum size of {MAX_CONTENT_SIZE} bytes"
        )
    return content


def validate_memory_id(mem_id: str) -> str:
    if not isinstance(mem_id, str) or not _UUID_RE.match(mem_id):
        raise ValidationError("Invalid memory ID format (expected UUID)")
    return mem_id


def validate_query(query: str) -> str:
    if not isinstance(query, str):
        raise ValidationError("Query must be a string")
    if len(query) > MAX_QUERY_LENGTH:
        raise ValidationError(
            f"Query exceeds maximum length of {MAX_QUERY_LENGTH} characters"
        )
    return query


def validate_tags(tags: str | None) -> str | None:
    if tags is None:
        return None
    if not isinstance(tags, str):
        raise ValidationError("Tags must be a string")
    if len(tags) > MAX_TAGS_LENGTH:
        raise ValidationError(
            f"Tags exceed maximum length of {MAX_TAGS_LENGTH} characters"
        )
    return tags

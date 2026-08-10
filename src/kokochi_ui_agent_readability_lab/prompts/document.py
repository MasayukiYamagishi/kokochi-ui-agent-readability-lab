"""Canonical prompt documents and deterministic input insertion."""

from __future__ import annotations

from datetime import date
import hashlib
import re
from typing import Self

from pydantic import ValidationError, field_validator, model_validator

from kokochi_ui_agent_readability_lab.records import ExperimentConfig
from kokochi_ui_agent_readability_lab.records.schema import (
    NonEmptyString,
    RepositoryIdentifier,
    Sha256Hex,
    StrictModel,
)


INPUT_REPRESENTATION_PLACEHOLDER = "{{INPUT_REPRESENTATION}}"
TASK_INSTRUCTION_PLACEHOLDER = "{{TASK_INSTRUCTION}}"
FIXTURE_INPUT_PLACEHOLDER = "{{FIXTURE_INPUT}}"
BEGIN_FIXTURE_INPUT_MARKER = "<<<BEGIN_UNTRUSTED_FIXTURE_INPUT>>>"
END_FIXTURE_INPUT_MARKER = "<<<END_UNTRUSTED_FIXTURE_INPUT>>>"

_FRONT_MATTER_DELIMITER = "---\n"
_REQUIRED_METADATA_FIELDS = frozenset(
    {"prompt_version", "body_sha256", "created_at", "change_reason"}
)
_REPRESENTATION_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class PromptError(ValueError):
    """Raised when a prompt is malformed, mismatched, or unsafe to render."""


class PromptMetadata(StrictModel):
    """Auditable metadata for one immutable prompt body."""

    prompt_version: RepositoryIdentifier
    body_sha256: Sha256Hex
    created_at: date
    change_reason: NonEmptyString


class PromptDocument(StrictModel):
    """A validated prompt template and its immutable metadata."""

    metadata: PromptMetadata
    body: str

    @field_validator("body")
    @classmethod
    def body_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt body must not be blank")
        return value

    @model_validator(mode="after")
    def template_contract_must_be_unambiguous(self) -> Self:
        for placeholder in (
            INPUT_REPRESENTATION_PLACEHOLDER,
            FIXTURE_INPUT_PLACEHOLDER,
        ):
            if self.body.count(placeholder) != 1:
                raise ValueError(
                    f"prompt body must contain {placeholder!r} exactly once"
                )
        if self.body.count(TASK_INSTRUCTION_PLACEHOLDER) > 1:
            raise ValueError(
                f"prompt body must contain {TASK_INSTRUCTION_PLACEHOLDER!r} at most once"
            )

        delimited_input = (
            f"{BEGIN_FIXTURE_INPUT_MARKER}\n"
            f"{FIXTURE_INPUT_PLACEHOLDER}\n"
            f"{END_FIXTURE_INPUT_MARKER}"
        )
        if self.body.count(BEGIN_FIXTURE_INPUT_MARKER) != 1:
            raise ValueError("prompt body must contain one input start marker")
        if self.body.count(END_FIXTURE_INPUT_MARKER) != 1:
            raise ValueError("prompt body must contain one input end marker")
        if delimited_input not in self.body:
            raise ValueError(
                "fixture input placeholder must be on its own line between markers"
            )
        return self


def prompt_body_sha256(body: str) -> Sha256Hex:
    """Return the SHA-256 digest of the exact UTF-8 prompt body."""

    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _validate_canonical_bytes(content: bytes) -> str:
    if content.startswith(b"\xef\xbb\xbf"):
        raise PromptError("prompt must not contain a UTF-8 BOM")
    if b"\r" in content:
        raise PromptError("prompt must use LF line endings")
    if not content.endswith(b"\n"):
        raise PromptError("prompt must end with a newline")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PromptError("prompt must be valid UTF-8") from error
    for line_number, line in enumerate(text.splitlines(), start=1):
        if line.endswith(" "):
            raise PromptError(
                f"prompt line {line_number} must not have trailing spaces"
            )
    return text


def _parse_metadata(lines: list[str]) -> PromptMetadata:
    values: dict[str, str] = {}
    for line in lines:
        if ": " not in line:
            raise PromptError("prompt metadata must use 'key: value' lines")
        key, value = line.split(": ", maxsplit=1)
        if key in values:
            raise PromptError(f"prompt metadata contains duplicate field {key!r}")
        if key not in _REQUIRED_METADATA_FIELDS:
            raise PromptError(f"prompt metadata contains unknown field {key!r}")
        if not value:
            raise PromptError(f"prompt metadata field {key!r} must not be empty")
        values[key] = value

    missing = sorted(_REQUIRED_METADATA_FIELDS - values.keys())
    if missing:
        raise PromptError(
            "prompt metadata is missing required fields: " + ", ".join(missing)
        )

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", values["created_at"]) is None:
        raise PromptError("prompt created_at must use YYYY-MM-DD")
    try:
        created_at = date.fromisoformat(values["created_at"])
    except ValueError as error:
        raise PromptError("prompt created_at must use YYYY-MM-DD") from error

    try:
        return PromptMetadata.model_validate(
            {
                "prompt_version": values["prompt_version"],
                "body_sha256": values["body_sha256"],
                "created_at": created_at,
                "change_reason": values["change_reason"],
            }
        )
    except ValidationError as error:
        raise PromptError(f"prompt metadata is invalid: {error}") from error


def parse_prompt_markdown(content: bytes) -> PromptDocument:
    """Parse canonical Markdown and verify the declared body hash and template."""

    text = _validate_canonical_bytes(content)
    lines = text.splitlines(keepends=True)
    if not lines or lines[0] != _FRONT_MATTER_DELIMITER:
        raise PromptError("prompt must start with Markdown front matter")
    try:
        closing_index = lines.index(_FRONT_MATTER_DELIMITER, 1)
    except ValueError as error:
        raise PromptError("prompt front matter is not closed") from error

    metadata_lines = [line.removesuffix("\n") for line in lines[1:closing_index]]
    metadata = _parse_metadata(metadata_lines)
    body = "".join(lines[closing_index + 1 :])
    actual_hash = prompt_body_sha256(body)
    if actual_hash != metadata.body_sha256:
        raise PromptError(
            "prompt body hash differs from metadata; update the version and hash"
        )

    try:
        return PromptDocument(metadata=metadata, body=body)
    except ValidationError as error:
        raise PromptError(f"prompt template is invalid: {error}") from error


def validate_prompt_matches_config(
    content: bytes,
    config: ExperimentConfig,
) -> PromptDocument:
    """Reject a prompt whose metadata does not match its config reference."""

    prompt = parse_prompt_markdown(content)
    if prompt.metadata.prompt_version != config.prompt.prompt_version:
        raise PromptError(
            "prompt version differs from config.yaml: "
            f"{prompt.metadata.prompt_version!r} != "
            f"{config.prompt.prompt_version!r}"
        )
    expected_path = f"prompts/{prompt.metadata.prompt_version}.md"
    if config.prompt.source_path != expected_path:
        raise PromptError(
            "prompt source path differs from its version: "
            f"{config.prompt.source_path!r} != {expected_path!r}"
        )
    if config.prompt.approved_body_sha256 is None:
        raise PromptError("config.yaml must define prompt.approved_body_sha256")
    if prompt.metadata.body_sha256 != config.prompt.approved_body_sha256:
        raise PromptError("prompt body hash differs from config.yaml")
    return prompt


def build_prompt(
    prompt: PromptDocument,
    *,
    input_representation: str,
    fixture_input: str,
    task_instruction: str | None = None,
) -> str:
    """Insert captured fixture data without interpreting it as template text."""

    if _REPRESENTATION_PATTERN.fullmatch(input_representation) is None:
        raise PromptError("input representation must be lowercase kebab-case")
    if not fixture_input.strip():
        raise PromptError("fixture input must not be blank")
    for marker in (BEGIN_FIXTURE_INPUT_MARKER, END_FIXTURE_INPUT_MARKER):
        if marker in fixture_input:
            raise PromptError(
                "fixture input must not contain reserved boundary markers"
            )
    requires_task_instruction = TASK_INSTRUCTION_PLACEHOLDER in prompt.body
    if requires_task_instruction:
        if task_instruction is None or not task_instruction.strip():
            raise PromptError("prompt template requires a non-empty task instruction")
        for reserved in (
            BEGIN_FIXTURE_INPUT_MARKER,
            END_FIXTURE_INPUT_MARKER,
            TASK_INSTRUCTION_PLACEHOLDER,
            FIXTURE_INPUT_PLACEHOLDER,
        ):
            if reserved in task_instruction:
                raise PromptError(
                    "task instruction must not contain reserved prompt markers"
                )
    elif task_instruction is not None:
        raise PromptError("prompt template does not accept a task instruction")

    before_input, after_input = prompt.body.split(
        FIXTURE_INPUT_PLACEHOLDER,
        maxsplit=1,
    )
    before_input = before_input.replace(
        INPUT_REPRESENTATION_PLACEHOLDER,
        input_representation,
    )
    after_input = after_input.replace(
        INPUT_REPRESENTATION_PLACEHOLDER,
        input_representation,
    )
    if task_instruction is not None:
        before_input = before_input.replace(
            TASK_INSTRUCTION_PLACEHOLDER,
            task_instruction,
        )
        after_input = after_input.replace(
            TASK_INSTRUCTION_PLACEHOLDER,
            task_instruction,
        )
    return before_input + fixture_input + after_input

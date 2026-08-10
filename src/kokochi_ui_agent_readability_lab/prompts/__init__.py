"""Versioned prompt loading, validation, and rendering."""

from kokochi_ui_agent_readability_lab.prompts.document import (
    BEGIN_FIXTURE_INPUT_MARKER,
    END_FIXTURE_INPUT_MARKER,
    FIXTURE_INPUT_PLACEHOLDER,
    INPUT_REPRESENTATION_PLACEHOLDER,
    TASK_INSTRUCTION_PLACEHOLDER,
    PromptDocument,
    PromptError,
    PromptMetadata,
    build_prompt,
    parse_prompt_markdown,
    prompt_body_sha256,
    validate_prompt_matches_config,
)

__all__ = [
    "BEGIN_FIXTURE_INPUT_MARKER",
    "END_FIXTURE_INPUT_MARKER",
    "FIXTURE_INPUT_PLACEHOLDER",
    "INPUT_REPRESENTATION_PLACEHOLDER",
    "TASK_INSTRUCTION_PLACEHOLDER",
    "PromptDocument",
    "PromptError",
    "PromptMetadata",
    "build_prompt",
    "parse_prompt_markdown",
    "prompt_body_sha256",
    "validate_prompt_matches_config",
]

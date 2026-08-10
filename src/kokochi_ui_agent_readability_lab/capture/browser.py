"""Collect fixture-scoped HTML, accessibility, and image artifacts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
import json
from types import MappingProxyType
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from playwright.sync_api import (
    Browser,
    BrowserContext as PlaywrightBrowserContext,
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)
from pydantic import AwareDatetime, Field, FiniteFloat, PositiveInt, model_validator

from kokochi_ui_agent_readability_lab.records import (
    ArtifactReference,
    ArtifactRole,
    ColorScheme,
    ContentHash,
    ReducedMotion,
    artifact_reference,
    sha256_digest,
)
from kokochi_ui_agent_readability_lab.records.schema import (
    Identifier,
    NonEmptyString,
    RepositoryIdentifier,
    StrictModel,
)


CAPTURE_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"


class CaptureFailureCode(StrEnum):
    """Stable failure categories that prevent inference on partial input."""

    NAVIGATION_TIMEOUT = "navigation-timeout"
    NAVIGATION_FAILED = "navigation-failed"
    NAVIGATION_HTTP_ERROR = "navigation-http-error"
    FIXTURE_NOT_READY = "fixture-not-ready"
    FIXTURE_IDENTITY_MISMATCH = "fixture-identity-mismatch"
    FONT_READINESS_FAILED = "font-readiness-failed"
    BROWSER_CONTEXT_MISMATCH = "browser-context-mismatch"
    HTML_COLLECTION_FAILED = "html-collection-failed"
    ACCESSIBILITY_COLLECTION_FAILED = "accessibility-collection-failed"
    SCREENSHOT_COLLECTION_FAILED = "screenshot-collection-failed"


class CaptureError(RuntimeError):
    """A classified all-or-nothing input collection failure."""

    def __init__(
        self,
        code: CaptureFailureCode,
        message: str,
        *,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code


class CaptureSettings(StrictModel):
    """Browser and serialization controls fixed before a capture."""

    viewport_width: PositiveInt = 1280
    viewport_height: PositiveInt = 720
    device_scale_factor: Annotated[FiniteFloat, Field(gt=0)] = 1.0
    locale: NonEmptyString = "en-US"
    timezone: NonEmptyString = "Asia/Tokyo"
    color_scheme: ColorScheme = ColorScheme.LIGHT
    reduced_motion: ReducedMotion = ReducedMotion.NO_PREFERENCE
    fixture_root_selector: Literal["[data-fixture-root]"] = "[data-fixture-root]"
    navigation_wait_until: Literal["domcontentloaded"] = "domcontentloaded"
    screenshot_animations: Literal["disabled"] = "disabled"
    screenshot_caret: Literal["hide"] = "hide"
    screenshot_scale: Literal["device"] = "device"
    font_readiness: Literal["document.fonts.ready"] = "document.fonts.ready"
    required_font_family: Literal["Inter"] = "Inter"
    font_probe_text: Literal[
        "Full name Company Department Email address Phone number"
    ] = "Full name Company Department Email address Phone number"


class CaptureRequest(StrictModel):
    """One stable fixture URL and the expected identity contract."""

    url: NonEmptyString
    experiment_id: RepositoryIdentifier
    fixture_id: RepositoryIdentifier
    fixture_version: RepositoryIdentifier
    settings: CaptureSettings = Field(default_factory=CaptureSettings)

    @model_validator(mode="after")
    def url_path_must_match_identity(self) -> Self:
        parsed = urlsplit(self.url)
        expected_path = f"/experiments/{self.experiment_id}/{self.fixture_id}"
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("capture URL must be an absolute HTTP(S) URL")
        if parsed.path.rstrip("/") != expected_path or parsed.query or parsed.fragment:
            raise ValueError(f"capture URL path must be {expected_path!r}")
        return self


class BrowserObservation(StrictModel):
    """Browser values observed after the readiness condition completed."""

    viewport_width: PositiveInt
    viewport_height: PositiveInt
    device_scale_factor: Annotated[FiniteFloat, Field(gt=0)]
    locale: NonEmptyString
    timezone: NonEmptyString
    color_scheme: ColorScheme
    reduced_motion: ReducedMotion
    browser_version: NonEmptyString
    font_status: Literal["loaded"]
    fixture_font_family: NonEmptyString
    fixture_primary_font_family: NonEmptyString
    fixture_regular_font_loaded: bool
    fixture_bold_font_loaded: bool


class CaptureArtifactMetadata(StrictModel):
    """Hash and size of one collected input file."""

    artifact_id: Identifier
    kind: Identifier
    relative_path: NonEmptyString
    media_type: NonEmptyString
    byte_length: int = Field(ge=0)
    content_hash: ContentHash


class CaptureManifest(StrictModel):
    """Provenance for a complete, fixture-scoped capture bundle."""

    schema_version: Literal["1.0.0"]
    captured_at: AwareDatetime
    experiment_id: RepositoryIdentifier
    fixture_id: RepositoryIdentifier
    fixture_version: RepositoryIdentifier
    fixture_path: NonEmptyString
    artifacts: tuple[CaptureArtifactMetadata, ...] = Field(min_length=4)

    @model_validator(mode="after")
    def artifact_identifiers_and_paths_must_be_unique(self) -> Self:
        artifact_ids = [artifact.artifact_id for artifact in self.artifacts]
        relative_paths = [artifact.relative_path for artifact in self.artifacts]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("capture artifact_id values must be unique")
        if len(relative_paths) != len(set(relative_paths)):
            raise ValueError("capture artifact relative_path values must be unique")
        return self


@dataclass(frozen=True)
class CapturedInputs:
    """Complete bytes ready for the repository write-once result store."""

    manifest: CaptureManifest
    artifacts: Mapping[str, bytes]

    def artifact_references(self) -> tuple[ArtifactReference, ...]:
        """Build run-manifest references from the exact captured bytes."""

        by_path = {
            artifact.relative_path: artifact for artifact in self.manifest.artifacts
        }
        references = [
            artifact_reference(
                artifact_id=metadata.artifact_id,
                role=ArtifactRole.INPUT,
                kind=metadata.kind,
                relative_path=metadata.relative_path,
                media_type=metadata.media_type,
                content=self.artifacts[metadata.relative_path],
            )
            for metadata in by_path.values()
        ]
        manifest_path = "inputs/capture-manifest.json"
        references.append(
            artifact_reference(
                artifact_id="input-capture-manifest",
                role=ArtifactRole.INPUT,
                kind="capture-manifest",
                relative_path=manifest_path,
                media_type="application/json",
                content=self.artifacts[manifest_path],
            )
        )
        return tuple(references)


def new_capture_context(
    browser: Browser,
    settings: CaptureSettings,
) -> PlaywrightBrowserContext:
    """Create a browser context that exactly applies the capture controls."""

    return browser.new_context(
        viewport={
            "width": settings.viewport_width,
            "height": settings.viewport_height,
        },
        device_scale_factor=settings.device_scale_factor,
        locale=settings.locale,
        timezone_id=settings.timezone,
        color_scheme=settings.color_scheme.value,
        reduced_motion=settings.reduced_motion.value,
    )


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, separators=(",", ": ")) + "\n"
    ).encode("utf-8")


def _metadata(
    *,
    artifact_id: str,
    kind: str,
    relative_path: str,
    media_type: str,
    content: bytes,
) -> CaptureArtifactMetadata:
    return CaptureArtifactMetadata(
        artifact_id=artifact_id,
        kind=kind,
        relative_path=relative_path,
        media_type=media_type,
        byte_length=len(content),
        content_hash=sha256_digest(content),
    )


def _observe_browser(page: Page, settings: CaptureSettings) -> BrowserObservation:
    browser = page.context.browser
    browser_version = browser.version if browser is not None else "unavailable"
    viewport = page.viewport_size
    if viewport is None:
        raise CaptureError(
            CaptureFailureCode.BROWSER_CONTEXT_MISMATCH,
            "capture requires a fixed viewport",
            retryable=False,
        )
    raw = page.evaluate(
        """parameters => {
            const root = document.querySelector(parameters.rootSelector)
            if (!(root instanceof HTMLElement)) {
                throw new Error('fixture root is unavailable')
            }
            const fixtureFontFamily = getComputedStyle(root).fontFamily
            const fixturePrimaryFontFamily = fixtureFontFamily
                .split(',')[0]
                .trim()
                .replace(/^['"]|['"]$/g, '')
            const matchingFontFaces = [...document.fonts].filter(
                face => face.family.replace(/^['"]|['"]$/g, '')
                    === parameters.requiredFontFamily,
            )
            const fontWeightIsLoaded = weight =>
                matchingFontFaces.some(
                    face => face.weight === weight && face.status === 'loaded',
                ) && document.fonts.check(
                    `${weight} 16px "${parameters.requiredFontFamily}"`,
                    parameters.fontProbeText,
                )
            return {
                deviceScaleFactor: window.devicePixelRatio,
                locale: navigator.language,
                timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                colorScheme: matchMedia('(prefers-color-scheme: dark)').matches
                    ? 'dark'
                    : 'light',
                reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches
                    ? 'reduce'
                    : 'no-preference',
                fontStatus: document.fonts.status,
                fixtureFontFamily,
                fixturePrimaryFontFamily,
                fixtureRegularFontLoaded: fontWeightIsLoaded('400'),
                fixtureBoldFontLoaded: fontWeightIsLoaded('700'),
            }
        }""",
        {
            "rootSelector": settings.fixture_root_selector,
            "requiredFontFamily": settings.required_font_family,
            "fontProbeText": settings.font_probe_text,
        },
    )
    return BrowserObservation(
        viewport_width=viewport["width"],
        viewport_height=viewport["height"],
        device_scale_factor=raw["deviceScaleFactor"],
        locale=raw["locale"],
        timezone=raw["timezone"],
        color_scheme=ColorScheme(raw["colorScheme"]),
        reduced_motion=ReducedMotion(raw["reducedMotion"]),
        browser_version=browser_version,
        font_status=raw["fontStatus"],
        fixture_font_family=raw["fixtureFontFamily"],
        fixture_primary_font_family=raw["fixturePrimaryFontFamily"],
        fixture_regular_font_loaded=raw["fixtureRegularFontLoaded"],
        fixture_bold_font_loaded=raw["fixtureBoldFontLoaded"],
    )


def _validate_observation(
    observation: BrowserObservation,
    settings: CaptureSettings,
) -> None:
    unloaded_weights = [
        weight
        for weight, loaded in (
            ("400", observation.fixture_regular_font_loaded),
            ("700", observation.fixture_bold_font_loaded),
        )
        if not loaded
    ]
    if unloaded_weights:
        raise CaptureError(
            CaptureFailureCode.FONT_READINESS_FAILED,
            "required fixture font is not loaded for weights: "
            + ", ".join(unloaded_weights),
            retryable=True,
        )
    expected = {
        "viewport_width": settings.viewport_width,
        "viewport_height": settings.viewport_height,
        "device_scale_factor": settings.device_scale_factor,
        "locale": settings.locale,
        "timezone": settings.timezone,
        "color_scheme": settings.color_scheme,
        "reduced_motion": settings.reduced_motion,
        "fixture_primary_font_family": settings.required_font_family,
    }
    mismatches = [
        name
        for name, expected_value in expected.items()
        if getattr(observation, name) != expected_value
    ]
    if mismatches:
        raise CaptureError(
            CaptureFailureCode.BROWSER_CONTEXT_MISMATCH,
            "browser context differs from capture settings: " + ", ".join(mismatches),
            retryable=False,
        )


def capture_fixture(
    page: Page,
    request: CaptureRequest,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> CapturedInputs:
    """Capture all representations in memory, returning nothing partial on failure."""

    try:
        response = page.goto(
            request.url,
            wait_until=request.settings.navigation_wait_until,
        )
    except PlaywrightTimeoutError as error:
        raise CaptureError(
            CaptureFailureCode.NAVIGATION_TIMEOUT,
            "fixture navigation timed out",
            retryable=True,
        ) from error
    except PlaywrightError as error:
        raise CaptureError(
            CaptureFailureCode.NAVIGATION_FAILED,
            "fixture navigation failed",
            retryable=True,
        ) from error
    if response is None or response.status >= 400:
        status_code = response.status if response is not None else None
        raise CaptureError(
            CaptureFailureCode.NAVIGATION_HTTP_ERROR,
            f"fixture navigation returned status {status_code}",
            retryable=status_code is None or status_code >= 500,
            status_code=status_code,
        )

    root = page.locator(request.settings.fixture_root_selector)
    try:
        root.wait_for(state="attached")
        if root.count() != 1:
            raise CaptureError(
                CaptureFailureCode.FIXTURE_NOT_READY,
                "fixture page must contain exactly one fixture root",
                retryable=False,
            )
    except PlaywrightTimeoutError as error:
        raise CaptureError(
            CaptureFailureCode.FIXTURE_NOT_READY,
            "fixture root did not become ready",
            retryable=True,
        ) from error
    except PlaywrightError as error:
        raise CaptureError(
            CaptureFailureCode.FIXTURE_NOT_READY,
            "fixture root readiness check failed",
            retryable=True,
        ) from error

    expected_attributes = {
        "data-experiment-id": request.experiment_id,
        "data-fixture-id": request.fixture_id,
        "data-fixture-version": request.fixture_version,
    }
    try:
        actual_attributes = {
            name: root.get_attribute(name) for name in expected_attributes
        }
    except PlaywrightError as error:
        raise CaptureError(
            CaptureFailureCode.FIXTURE_NOT_READY,
            "fixture identity could not be read",
            retryable=True,
        ) from error
    if actual_attributes != expected_attributes:
        raise CaptureError(
            CaptureFailureCode.FIXTURE_IDENTITY_MISMATCH,
            f"fixture identity mismatch: {actual_attributes}",
            retryable=False,
        )

    try:
        page.evaluate("async () => { await document.fonts.ready }")
    except PlaywrightError as error:
        raise CaptureError(
            CaptureFailureCode.FONT_READINESS_FAILED,
            "document fonts did not become ready",
            retryable=True,
        ) from error

    try:
        observation = _observe_browser(page, request.settings)
    except CaptureError:
        raise
    except (PlaywrightError, KeyError, TypeError, ValueError) as error:
        raise CaptureError(
            CaptureFailureCode.BROWSER_CONTEXT_MISMATCH,
            "browser context could not be observed",
            retryable=False,
        ) from error
    _validate_observation(observation, request.settings)

    try:
        html = (root.inner_html() + "\n").encode("utf-8")
    except PlaywrightError as error:
        raise CaptureError(
            CaptureFailureCode.HTML_COLLECTION_FAILED,
            "fixture HTML collection failed",
            retryable=True,
        ) from error
    if not html.strip():
        raise CaptureError(
            CaptureFailureCode.HTML_COLLECTION_FAILED,
            "fixture HTML collection returned empty content",
            retryable=False,
        )
    try:
        accessibility = (root.aria_snapshot() + "\n").encode("utf-8")
    except PlaywrightError as error:
        raise CaptureError(
            CaptureFailureCode.ACCESSIBILITY_COLLECTION_FAILED,
            "fixture accessibility collection failed",
            retryable=True,
        ) from error
    if not accessibility.strip():
        raise CaptureError(
            CaptureFailureCode.ACCESSIBILITY_COLLECTION_FAILED,
            "fixture accessibility collection returned empty content",
            retryable=False,
        )
    try:
        screenshot = root.screenshot(
            animations=request.settings.screenshot_animations,
            caret=request.settings.screenshot_caret,
            scale=request.settings.screenshot_scale,
        )
    except PlaywrightError as error:
        raise CaptureError(
            CaptureFailureCode.SCREENSHOT_COLLECTION_FAILED,
            "fixture screenshot collection failed",
            retryable=True,
        ) from error
    if not screenshot:
        raise CaptureError(
            CaptureFailureCode.SCREENSHOT_COLLECTION_FAILED,
            "fixture screenshot collection returned empty content",
            retryable=False,
        )

    settings_bytes = _canonical_json_bytes(
        {
            "schema_version": CAPTURE_SCHEMA_VERSION,
            "settings": request.settings.model_dump(mode="json"),
            "observation": observation.model_dump(mode="json"),
        }
    )
    contents = {
        "inputs/dom-inner-html.html": html,
        "inputs/accessibility-tree.yml": accessibility,
        "inputs/screenshot.png": screenshot,
        "inputs/capture-settings.json": settings_bytes,
    }
    specifications = (
        (
            "input-dom-inner-html",
            "dom-inner-html",
            "inputs/dom-inner-html.html",
            "text/html; charset=utf-8",
        ),
        (
            "input-accessibility-tree",
            "accessibility-tree",
            "inputs/accessibility-tree.yml",
            "application/yaml",
        ),
        (
            "input-screenshot",
            "fixture-screenshot",
            "inputs/screenshot.png",
            "image/png",
        ),
        (
            "input-capture-settings",
            "capture-settings",
            "inputs/capture-settings.json",
            "application/json",
        ),
    )
    metadata = tuple(
        _metadata(
            artifact_id=artifact_id,
            kind=kind,
            relative_path=relative_path,
            media_type=media_type,
            content=contents[relative_path],
        )
        for artifact_id, kind, relative_path, media_type in specifications
    )
    manifest = CaptureManifest(
        schema_version=CAPTURE_SCHEMA_VERSION,
        captured_at=clock(),
        experiment_id=request.experiment_id,
        fixture_id=request.fixture_id,
        fixture_version=request.fixture_version,
        fixture_path=urlsplit(request.url).path,
        artifacts=metadata,
    )
    manifest_bytes = (manifest.model_dump_json(indent=2) + "\n").encode("utf-8")
    all_contents = {**contents, "inputs/capture-manifest.json": manifest_bytes}
    return CapturedInputs(
        manifest=manifest,
        artifacts=MappingProxyType(all_contents),
    )

from typing import cast

import pytest
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
from pydantic import ValidationError

from kokochi_ui_agent_readability_lab.capture import (
    CaptureError,
    CaptureFailureCode,
    CaptureRequest,
    CaptureSettings,
    capture_fixture,
)


def test_capture_request_requires_stable_identity_path() -> None:
    with pytest.raises(ValidationError, match="capture URL path"):
        CaptureRequest(
            url="http://127.0.0.1:4173/another-path",
            experiment_id="example-experiment",
            fixture_id="explicit-label",
            fixture_version="v1",
        )


@pytest.mark.parametrize("device_scale_factor", [0, -1, -0.5])
def test_capture_settings_require_positive_device_scale_factor(
    device_scale_factor: float,
) -> None:
    with pytest.raises(ValidationError, match="device_scale_factor"):
        CaptureSettings(device_scale_factor=device_scale_factor)


class TimeoutPage:
    def goto(self, *_args: object, **_kwargs: object) -> None:
        raise PlaywrightTimeoutError("timed out")


def test_capture_classifies_navigation_timeout_without_partial_result() -> None:
    request = CaptureRequest(
        url=("http://127.0.0.1:4173/experiments/example-experiment/explicit-label"),
        experiment_id="example-experiment",
        fixture_id="explicit-label",
        fixture_version="v1",
    )

    with pytest.raises(CaptureError) as captured:
        capture_fixture(cast(Page, TimeoutPage()), request)

    assert captured.value.code is CaptureFailureCode.NAVIGATION_TIMEOUT
    assert captured.value.retryable is True


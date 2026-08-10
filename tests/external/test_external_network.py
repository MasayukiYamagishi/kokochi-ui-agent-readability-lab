import pytest
from playwright.sync_api import Page, expect


@pytest.mark.external
def test_example_domain(page: Page) -> None:
    page.goto("https://example.com/")
    expect(page).to_have_title("Example Domain")

from collections.abc import Callable
from urllib.parse import urlparse

from playwright.sync_api import Page, expect


FixtureUrl = Callable[[str | None, str | None], str]


def expand_experiment(page: Page, name: str) -> None:
    toggle = page.get_by_role("button", name=name, exact=True)
    expect(toggle).to_have_attribute("aria-expanded", "false")
    toggle.click()
    expect(toggle).to_have_attribute("aria-expanded", "true")


def test_development_server_catalog_and_direct_fixture(
    page: Page, fixture_dev_url: FixtureUrl
) -> None:
    console_errors: list[str] = []
    page.on(
        "console",
        lambda message: (
            console_errors.append(message.text) if message.type == "error" else None
        ),
    )

    page.goto(fixture_dev_url(None, None))
    expect(page.get_by_role("heading", name="Fixture catalog")).to_be_visible()
    expand_experiment(page, "Fixture host contract")
    fixture_button = page.get_by_role("button", name="stable-url Version v1")
    expect(fixture_button).to_be_visible()
    fixture_button.click()

    preview = page.frame_locator('iframe[title="Preview: stable-url"]')
    expect(preview.locator("[data-fixture-root]")).to_have_attribute(
        "data-fixture-version", "v1"
    )
    expect(page.locator(".vite-error-overlay")).to_have_count(0)

    page.goto(fixture_dev_url("fixture-host-contract", "stable-url"))
    expect(page.locator("[data-fixture-root]")).to_have_attribute(
        "data-fixture-id", "stable-url"
    )
    expect(page.locator(".fixture-host")).to_have_count(0)
    assert console_errors == []


def test_local_production_preview_catalog_and_direct_fixture(
    page: Page, fixture_preview_url: FixtureUrl
) -> None:
    page.goto(fixture_preview_url(None, None))
    expect(page.get_by_role("heading", name="Fixture catalog")).to_be_visible()
    expand_experiment(page, "Fixture host contract")
    expect(page.get_by_role("button", name="stable-url Version v1")).to_be_visible()

    page.goto(fixture_preview_url("fixture-host-contract", "stable-url"))
    fixture_root = page.locator("[data-fixture-root]")
    expect(fixture_root).to_have_attribute(
        "data-experiment-id", "fixture-host-contract"
    )
    expect(fixture_root).to_have_attribute("data-fixture-id", "stable-url")
    expect(fixture_root).to_have_attribute("data-fixture-version", "v1")
    expect(page.locator(".fixture-host")).to_have_count(0)


def test_fixture_catalog_and_isolated_preview(
    page: Page, fixture_url: FixtureUrl
) -> None:
    page.goto(fixture_url(None, None))

    expect(page).to_have_title("Fixture catalog | KOKOCHI UI")
    expect(page.get_by_role("heading", name="Fixture catalog")).to_be_visible()
    expect(page.get_by_role("navigation", name="Fixture navigation")).to_be_visible()
    expand_experiment(page, "Fixture host contract")
    stable_url_fixture = page.get_by_role("button", name="stable-url Version v1")
    expect(stable_url_fixture).to_be_visible()
    stable_url_fixture.click()
    expect(stable_url_fixture).to_have_attribute("aria-pressed", "true")

    preview = page.frame_locator('iframe[title="Preview: stable-url"]')
    fixture_root = preview.locator("[data-fixture-root]")
    expect(fixture_root).to_have_attribute(
        "data-experiment-id", "fixture-host-contract"
    )
    expect(fixture_root).to_have_attribute("data-fixture-id", "stable-url")
    expect(fixture_root).to_have_attribute("data-fixture-version", "v1")
    expect(
        preview.get_by_role("navigation", name="Direct fixture navigation")
    ).to_have_count(0)


def test_experiment_groups_are_collapsed_accordions(
    page: Page, fixture_url: FixtureUrl
) -> None:
    page.goto(fixture_url(None, None))

    toggle = page.get_by_role("button", name="Fixture host contract", exact=True)
    fixture = page.get_by_role("button", name="stable-url Version v1")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(fixture).to_be_hidden()

    toggle.click()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(fixture).to_be_visible()

    toggle.click()
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(fixture).to_be_hidden()


def test_back_navigation_from_direct_fixture_and_experiment_overview(
    page: Page, fixture_url: FixtureUrl
) -> None:
    page.goto(fixture_url("fixture-host-contract", "stable-url"))

    back_to_overview = page.get_by_role("link", name="Back to experiment overview")
    expect(back_to_overview).to_have_attribute(
        "href", "/experiments/fixture-host-contract"
    )
    back_to_overview.click()

    expect(
        page.get_by_role("heading", name="Fixture host contract", level=1)
    ).to_be_visible()
    back_to_catalog = page.get_by_role("link", name="Back to all experiments")
    expect(back_to_catalog).to_have_attribute("href", "/experiments")
    back_to_catalog.click()

    expect(page.get_by_role("heading", name="Fixture catalog")).to_be_visible()


def test_fixture_catalog_layout_controls(page: Page, fixture_url: FixtureUrl) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(fixture_url(None, None))

    header = page.locator(".fixture-host__header")
    header_box = header.bounding_box()
    assert header_box is not None
    assert header_box["height"] < 100

    navigation = page.get_by_role("navigation", name="Fixture navigation")
    splitter = page.get_by_role("separator", name="Resize fixture navigation")
    hide_navigation = page.get_by_role("button", name="Hide fixture navigation")

    initial_navigation_box = navigation.bounding_box()
    assert initial_navigation_box is not None

    splitter.focus()
    page.keyboard.press("ArrowRight")
    keyboard_resized_box = navigation.bounding_box()
    assert keyboard_resized_box is not None
    assert keyboard_resized_box["width"] > initial_navigation_box["width"]

    splitter_box = splitter.bounding_box()
    assert splitter_box is not None
    page.mouse.move(
        splitter_box["x"] + splitter_box["width"] / 2,
        splitter_box["y"] + splitter_box["height"] / 2,
    )
    page.mouse.down()
    page.mouse.move(
        splitter_box["x"] + splitter_box["width"] / 2 + 48,
        splitter_box["y"] + splitter_box["height"] / 2,
    )
    page.mouse.up()
    pointer_resized_box = navigation.bounding_box()
    assert pointer_resized_box is not None
    assert pointer_resized_box["width"] > keyboard_resized_box["width"]

    hide_navigation.click()
    expect(navigation).to_be_hidden()
    collapsed_preview_box = page.locator(".fixture-host__preview").bounding_box()
    assert collapsed_preview_box is not None
    assert collapsed_preview_box["width"] > 1300
    show_navigation = page.get_by_role("button", name="Show fixture navigation")
    expect(show_navigation).to_be_visible()

    show_navigation.click()
    expect(navigation).to_be_visible()
    restored_navigation_box = navigation.bounding_box()
    assert restored_navigation_box is not None
    assert restored_navigation_box["width"] == pointer_resized_box["width"]


def test_registered_fixture_direct_urls_honor_the_contract(
    page: Page, fixture_url: FixtureUrl
) -> None:
    page.goto(fixture_url(None, None))
    registrations = page.locator("[data-direct-fixture-link]").evaluate_all(
        """links => links.map((link) => ({
            url: link.href,
            experimentId: link.dataset.experimentId,
            fixtureId: link.dataset.fixtureId,
            fixtureVersion: link.dataset.fixtureVersion,
        }))"""
    )

    assert registrations, "The fixture-test build must register its contract fixture"
    for registration in registrations:
        direct_url = registration["url"]
        experiment_id = registration["experimentId"]
        fixture_id = registration["fixtureId"]
        fixture_version = registration["fixtureVersion"]
        assert isinstance(direct_url, str)
        assert isinstance(experiment_id, str)
        assert isinstance(fixture_id, str)
        assert isinstance(fixture_version, str)

        parsed_url = urlparse(direct_url)
        path_segments = parsed_url.path.strip("/").split("/")
        assert path_segments == ["experiments", experiment_id, fixture_id]

        response = page.goto(direct_url)
        assert response is not None
        assert response.status == 200
        expect(page.locator(".fixture-host")).to_have_count(0)

        fixture_root = page.locator("[data-fixture-root]")
        expect(fixture_root).to_have_count(1)
        expect(fixture_root).to_have_attribute("data-experiment-id", experiment_id)
        expect(fixture_root).to_have_attribute("data-fixture-id", fixture_id)
        expect(fixture_root).to_have_attribute("data-fixture-version", fixture_version)


def test_unknown_fixture_shows_clear_not_found_page(
    page: Page, fixture_url: FixtureUrl
) -> None:
    response = page.goto(fixture_url("missing-experiment", "missing-fixture"))

    assert response is not None
    assert response.status == 200
    expect(page).to_have_title("Fixture not found | KOKOCHI UI")
    expect(page.get_by_role("heading", name="Fixture not found")).to_be_visible()
    expect(
        page.get_by_role("link", name="Return to the fixture catalog")
    ).to_have_attribute("href", "/experiments")

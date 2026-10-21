from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:4182"


def collect_errors(page):
    errors = []
    page.on("console", lambda msg: errors.append(f"console:{msg.type}:{msg.text}") if msg.type == "error" else None)
    page.on("pageerror", lambda exc: errors.append(f"pageerror:{exc}"))
    page.on("requestfailed", lambda req: errors.append(f"requestfailed:{req.url}:{req.failure}"))
    return errors


def assert_no_horizontal_overflow(page):
    overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    assert overflow <= 1, f"horizontal overflow: {overflow}px"


def preview_flow(browser):
    context = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
    page = context.new_page()
    errors = collect_errors(page)
    page.goto(BASE, wait_until="networkidle")
    page.locator("#runtimeLabel").wait_for()
    assert "PREVIEW" in page.locator("#runtimeLabel").inner_text()
    assert page.locator("[data-claim-trial]").is_visible()
    assert_no_horizontal_overflow(page)

    page.locator("[data-claim-trial]").click()
    page.locator("#servicesList").get_by_text("تست رایگان", exact=True).wait_for()
    assert "۱۰۰ MB" in page.locator("#servicesList").inner_text()

    page.locator('.bottom-nav [data-go="plans"]').click()
    page.locator('#plansList [data-plan="smart40"]').click()
    page.locator('[data-start-payment="smart40"]').click()
    page.locator("#chooseReceipt").click()
    page.locator("#submitReceipt").click()
    page.locator("#pendingOrders .pending-card").wait_for()

    page.locator("#roleSwitch").click()
    page.locator("#ownerApp").wait_for(state="visible")
    page.locator("#ownerMessages .bot-message").wait_for()
    page.locator('[data-owner-menu="orders"]').first.click()
    page.wait_for_timeout(300)
    assert_no_horizontal_overflow(page)
    assert errors == [], errors
    context.close()


def telegram_flow(browser):
    context = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
    page = context.new_page()
    errors = collect_errors(page)
    page.goto(f"{BASE}/?telegram=1", wait_until="networkidle")
    page.locator("#runtimeLabel").wait_for()
    assert "@PablloVPN3_bot" in page.locator("#runtimeLabel").inner_text()
    assert page.locator("#roleSwitch").is_hidden()
    assert page.locator("#resetDemo").is_hidden()
    assert len(page.locator("#featuredPlans .plan-card").all()) >= 2

    if page.locator("[data-claim-trial]").count():
        page.locator("[data-claim-trial]").click()
    else:
        page.locator('.bottom-nav [data-go="services"]').click()
    page.locator("#servicesList").get_by_text("تست رایگان", exact=True).wait_for()
    assert "۱۰۰ MB" in page.locator("#servicesList").inner_text()

    page.locator('.bottom-nav [data-go="plans"]').click()
    page.locator('#plansList [data-plan="smart40"]').click()
    page.locator('[data-start-payment="smart40"]').click()
    page.locator("#paymentSheet.show").wait_for()
    assert page.locator("#submitReceipt").is_enabled()
    page.locator("#submitReceipt").click()
    page.locator("#pendingOrders .pending-card").wait_for()
    assert "در انتظار" in page.locator("#pendingOrders").inner_text()
    assert_no_horizontal_overflow(page)
    assert errors == [], errors
    context.close()


def desktop_smoke(browser):
    context = browser.new_context(viewport={"width": 1365, "height": 900})
    page = context.new_page()
    errors = collect_errors(page)
    page.goto(BASE, wait_until="networkidle")
    assert page.locator(".app-frame").bounding_box()["width"] <= 521
    assert_no_horizontal_overflow(page)
    assert errors == [], errors
    context.close()


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        preview_flow(browser)
        telegram_flow(browser)
        desktop_smoke(browser)
        browser.close()
    print("Playwright audit: preview + Telegram mode + desktop passed with no console errors")


if __name__ == "__main__":
    main()

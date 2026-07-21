"""One-time attended Indeed login: establishes the persistent browser session.

Run inside tmux: uv run python -m funnel.login_indeed
Fills the email from .env, submits, then prompts on stdin for the OTP code
from the login email. Saves the session on success; subsequent headless bot
runs reuse it via is_logged_in().

Screenshots of each step go to /artifacts/login/ so the operator can see the
headless page state.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import dotenv

from src.job_manager.indeed.authenticator_indeed import IndeedAuthenticator
from src.utils.browser_utils import create_playwright_browser, save_browser_session

SHOT_DIR = Path("/artifacts/login")

CODE_SELECTORS = [
    "input[name='passcode']",
    "input[autocomplete='one-time-code']",
    "input[id*='passcode']",
    "input[type='number']",
]


async def _shot(page, name: str) -> None:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        await page.screenshot(path=str(SHOT_DIR / f"{name}.png"), full_page=False)
    except Exception as exc:  # screenshot failure must never kill the login
        print(f"(screenshot {name} failed: {exc})")


async def main() -> int:
    secrets = dotenv.dotenv_values(".env")
    email = secrets.get("indeed_email")
    if not email:
        print("indeed_email missing from .env")
        return 1

    browser, context, page = await create_playwright_browser()
    try:
        auth = IndeedAuthenticator(page)
        auth.set_parameters(email)

        if await auth.is_logged_in():
            print("Already logged in — saved session is valid. Nothing to do.")
            return 0

        await page.goto(auth.INDEED_LOGIN_URL, wait_until="domcontentloaded")
        await asyncio.sleep(2)
        await _shot(page, "01-login-page")

        await page.fill("input[type='email']", email)
        for sel in ("button[type='submit']", "#login-submit-button"):
            try:
                await page.click(sel, timeout=5000)
                break
            except Exception:
                continue
        await asyncio.sleep(3)
        await _shot(page, "02-after-email-submit")
        print("Email submitted. Check the inbox for the Indeed verification code.")

        code = input("Enter the OTP code from the email: ").strip()
        filled = False
        for sel in CODE_SELECTORS:
            try:
                await page.fill(sel, code, timeout=3000)
                filled = True
                break
            except Exception:
                continue
        if not filled:
            await _shot(page, "03-no-code-field")
            print("Could not find the code input — see /artifacts/login/03-no-code-field.png")
            return 1

        for sel in ("button[type='submit']", "#passcode-submit-button"):
            try:
                await page.click(sel, timeout=5000)
                break
            except Exception:
                continue
        await asyncio.sleep(4)
        await _shot(page, "04-after-code-submit")

        if await auth.check_login_success():
            await save_browser_session(context)
            print("Login successful — session saved.")
            return 0
        print("Login not confirmed — see /artifacts/login/04-after-code-submit.png")
        return 1
    finally:
        await browser.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

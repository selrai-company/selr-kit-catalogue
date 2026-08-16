#!/usr/bin/env python3
"""Watch the classroom, and draft an entry for anything new in it.

Skool publishes no interface for this, so the only way to read the classroom is
to open it in a browser that is already signed in as you. That has two
consequences worth knowing before trusting this:

  * The first sign-in has to be done by a person, once. After that the session
    is kept in a browser profile beside this script and reused.
  * Skool can change the look of that page whenever it likes, and this reads the
    page. So it is built to fail loudly rather than quietly: a run that finds no
    courses at all is treated as broken, never as "nothing new". Otherwise an
    expired sign-in would look exactly like a quiet week, for ever.

What it writes is deliberately incomplete. The classroom knows a kit's name,
blurb and drop date; it cannot know the kit's fingerprint, because that is a
fact about what the kit puts on somebody's computer and it lives in the kit's
own repository. So a drafted entry carries an empty ``detect``, and every
member's tracker drops an entry with an empty ``detect`` on the floor. The draft
is therefore invisible to everybody until a person fills that field in, which is
what makes it safe for this to commit unattended.

    watch.py --login      sign in once, in a visible browser
    watch.py --dry-run    read the classroom and print what it would add
    watch.py              read, draft, commit and push
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
CATALOGUE = REPO / "kits.json"
PROFILE = HERE / ".skool-profile"
CONFIG = HERE / "watch.config.json"

CLASSROOM = "https://www.skool.com/selr-ai-2055/classroom"

# Courses that exist in the classroom but are not kits. Extend this rather than
# teaching the reader to be clever: a wrong guess here is a card in front of
# every member, and a name is the only thing this can judge on.
DEFAULT_SKIP = [
    "start here", "replays", "training sessions", "upskill",
    "weekly builds", "announcements", "community",
]


def fail(message: str) -> int:
    """One plain sentence to the error log, and a non-zero exit.

    The error log is what the Skill Tracker health-checks, so a job that dies
    this way turns up on the report as failing rather than disappearing.
    """
    print(message, file=sys.stderr)
    return 1


def load_config() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def slug_of(url: str) -> str:
    """The last part of a classroom address, which is the only stable name."""
    return url.rstrip("/").rsplit("/", 1)[-1].lower()


# ----------------------------------------------------------------- the browser

def read_classroom(headless: bool = True) -> list[dict]:
    """Every course card on the classroom page.

    Matched on the shape of the address rather than on class names, because
    class names in a modern web page are generated and change without warning,
    while the address of a lesson is part of how the site works.
    """
    from playwright.sync_api import sync_playwright

    courses: list[dict] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch_persistent_context(
            str(PROFILE), headless=headless, channel="chrome")
        page = browser.new_page() if not browser.pages else browser.pages[0]
        page.goto(CLASSROOM, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(3_000)

        # Signed out, Skool sends you to the public page about the community.
        if "/classroom" not in page.url:
            browser.close()
            raise RuntimeError(
                "Skool did not show the classroom - the saved sign-in has "
                "expired. Run this again with --login to sign in.")

        found = page.eval_on_selector_all(
            'a[href*="/classroom/"]',
            """els => els.map(a => {
                 const card = a.closest('[class*=card], li, article') || a;
                 const text = (card.innerText || '').trim().split('\\n')
                     .map(s => s.trim()).filter(Boolean);
                 return {href: a.href, lines: text.slice(0, 4)};
               })""",
        )
        browser.close()

    seen: set[str] = set()
    for row in found:
        slug = slug_of(row["href"])
        if not slug or slug == "classroom" or slug in seen:
            continue
        seen.add(slug)
        lines = [ln for ln in row["lines"] if ln and "%" not in ln]
        if not lines:
            continue
        courses.append({
            "slug": slug,
            "url": row["href"].split("?")[0],
            "name": lines[0],
            "blurb": lines[1] if len(lines) > 1 else "",
        })
    return courses


# ------------------------------------------------------------------ the diff

def already_known(catalogue: dict, course: dict, skip: list[str]) -> bool:
    name = course["name"].strip().lower()
    if any(word in name for word in skip):
        return True
    for kit in catalogue.get("kits", []):
        if kit.get("id", "").lower() == course["slug"]:
            return True
        if kit.get("name", "").strip().lower() == name:
            return True
        if slug_of(str(kit.get("classroom_url") or "")) == course["slug"]:
            return True
    return False


def draft(course: dict) -> dict:
    """An entry with everything the classroom knows, and nothing it does not.

    ``detect`` is left empty on purpose. Every member's tracker drops an entry
    with an empty ``detect``, so this is invisible until somebody fills it in -
    which is the whole reason this is safe to commit without being read first.
    """
    return {
        "id": course["slug"],
        "name": course["name"],
        "emoji": "",
        "tag": "AI SKILL DROP",
        "blurb": course["blurb"][:90],
        "dropped": date.today().isoformat(),
        "classroom_url": course["url"],
        "install_prompt": "",
        "detect": [],
    }


# ------------------------------------------------------------------- the write

def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *args],
                          capture_output=True, text=True)


def publish(added: list[dict]) -> int:
    names = ", ".join(a["name"] for a in added)
    body = (
        f"Draft {len(added)} new {'entry' if len(added) == 1 else 'entries'} "
        f"from the classroom\n\n"
        f"{names}\n\n"
        "Written by tools/kit-watch, which reads the classroom three times a "
        "day. Each entry has an empty detect, so every member's tracker drops "
        "it and nobody sees a card until the fingerprint is filled in by hand - "
        "the classroom cannot know what a kit puts on somebody's computer.\n"
    )
    git("add", "kits.json")
    done = git("commit", "-m", body)
    if done.returncode != 0:
        return fail(f"Nothing could be committed: {done.stderr.strip()}")
    pushed = git("push", "origin", "main")
    if pushed.returncode != 0:
        return fail(f"Committed, but the push failed: {pushed.stderr.strip()}")
    print(f"Added and pushed: {names}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--login", action="store_true",
                    help="open a visible browser so you can sign in to Skool once")
    ap.add_argument("--dry-run", action="store_true",
                    help="read the classroom and print what would change")
    args = ap.parse_args(argv)

    if args.login:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch_persistent_context(
                str(PROFILE), headless=False, channel="chrome")
            page = browser.pages[0] if browser.pages else browser.new_page()
            page.goto(CLASSROOM)
            print("Sign in to Skool in the window that opened, open the "
                  "classroom, then close the window.")
            try:
                page.wait_for_event("close", timeout=600_000)
            except Exception:  # noqa: BLE001 - closing the window is the signal
                pass
            browser.close()
        print(f"Sign-in saved to {PROFILE}")
        return 0

    try:
        catalogue = json.loads(CATALOGUE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return fail(f"The kit list could not be read: {type(exc).__name__}.")

    try:
        courses = read_classroom()
    except Exception as exc:  # noqa: BLE001 - a traceback helps nobody at 7am
        return fail(str(exc) if isinstance(exc, RuntimeError)
                    else f"The classroom could not be read: {type(exc).__name__}.")

    # No courses at all is a broken run, never a quiet week. An expired sign-in
    # and a genuinely empty classroom look identical from here, and only one of
    # them is worth waking somebody for - so treat both as worth waking for.
    if not courses:
        return fail("The classroom page showed no courses at all. Either the "
                    "sign-in has expired or the page has changed shape.")

    skip = load_config().get("skip", DEFAULT_SKIP)
    new = [c for c in courses if not already_known(catalogue, c, skip)]

    print(f"{len(courses)} courses in the classroom, "
          f"{len(catalogue.get('kits', []))} in the list, {len(new)} new")

    if not new:
        return 0

    for course in new:
        print(f"  new: {course['name']}  ({course['url']})")

    if args.dry_run:
        print("Nothing written - this was a dry run.")
        return 0

    catalogue.setdefault("kits", []).extend(draft(c) for c in new)
    catalogue["as_of"] = date.today().isoformat()
    CATALOGUE.write_text(json.dumps(catalogue, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    return publish(new)


if __name__ == "__main__":
    raise SystemExit(main())

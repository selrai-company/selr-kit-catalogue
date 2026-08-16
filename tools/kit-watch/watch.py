#!/usr/bin/env python3
"""Watch the classroom, and draft an entry for anything new in it.

Skool publishes no interface for this, so the only way to read the classroom is
to open it in a browser that is already signed in as you. That has two
consequences worth knowing before trusting this:

  * The first sign-in has to be done by a person, once. After that the session
    is kept in a browser profile beside this script and reused.
  * Skool can change the look of that page whenever it likes. So this reads the
    list the page renders itself from, not the tiles it renders - the markup has
    already been rebuilt once underneath this. Either way it is built to fail
    loudly rather than quietly: a run that finds no courses at all is treated as
    broken, never as "nothing new". Otherwise an expired sign-in would look
    exactly like a quiet week, for ever.

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

# Skool marks a finished course 2 and one still being written 1. Anything not
# published is held back rather than drafted: a card pointing at a lesson a
# member cannot open is worse than a card that arrives a day late, and the next
# run picks it up by itself once it goes live.
PUBLISHED = 2

# Classroom titles lead with an emoji. It is worth keeping for the card, but it
# has to come off the name before anything is compared - otherwise every kit
# already in the list reads as new, because "Brain Builder" and "\N{BRAIN} Brain
# Builder" are not the same string.
LEADING_SYMBOLS = re.compile(r"^[^\w(]+")

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


def plain_name(title: str) -> str:
    """A classroom title with its leading emoji taken off, for comparing."""
    return LEADING_SYMBOLS.sub("", title).strip()


def emoji_of(title: str) -> str:
    """The emoji a classroom title leads with, if it leads with one."""
    found = LEADING_SYMBOLS.match(title)
    return found.group(0).strip() if found else ""


def kebab(text: str) -> str:
    """An id in the style of the ones already in the list, not Skool's hex."""
    return "-".join(re.findall(r"[a-z0-9]+", text.lower()))[:60]


def is_ready(course: dict) -> bool:
    """Published, and not calling itself a draft in its own title."""
    return course["published"] and "(draft" not in course["name"].lower()


# ----------------------------------------------------------------- the browser

def read_classroom(headless: bool = True) -> list[dict]:
    """Every course in the classroom, read from the page's own data.

    The page ships the list it draws itself from in a ``__NEXT_DATA__`` script
    tag, and that is what this reads. It used to read the tiles instead, which
    stopped working the day Skool rebuilt them as drag-sortable panels with no
    links inside them - the addresses this matched on simply stopped existing.
    The data behind the page survived that change untouched, and carries the
    drop date and publish state as well, which the tiles never showed.
    """
    from playwright.sync_api import sync_playwright

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

        blob = page.eval_on_selector("#__NEXT_DATA__", "el => el.textContent") \
            if page.query_selector("#__NEXT_DATA__") else None
        browser.close()

    if not blob:
        raise RuntimeError(
            "The classroom page carried no __NEXT_DATA__ at all, so there was "
            "nothing to read. The page has been rebuilt.")
    try:
        listed = json.loads(blob)["props"]["pageProps"]["allCourses"]
    except (ValueError, KeyError, TypeError):
        raise RuntimeError(
            "The classroom page no longer keeps its course list at "
            "props.pageProps.allCourses. The page has been rebuilt.")

    courses: list[dict] = []
    for item in listed:
        meta = item.get("metadata") or {}
        title = (meta.get("title") or "").strip()
        slug = (item.get("name") or "").strip().lower()
        if not title or not slug:
            continue
        courses.append({
            "slug": slug,
            "url": f"{CLASSROOM}/{slug}",
            "name": title,
            "blurb": (meta.get("desc") or "").strip(),
            "published": item.get("state") == PUBLISHED,
            # The day the course was made, which beats the day this happened to
            # notice it - the old reader could only ever write "today".
            "created": (item.get("createdAt") or "")[:10],
        })
    return courses


# ------------------------------------------------------------------ the diff

def already_known(catalogue: dict, course: dict, skip: list[str]) -> bool:
    name = plain_name(course["name"]).lower()
    if any(word in name for word in skip):
        return True
    for kit in catalogue.get("kits", []):
        if kit.get("id", "").lower() == course["slug"]:
            return True
        if plain_name(kit.get("name", "")).lower() == name:
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
    title = plain_name(course["name"])
    return {
        # Skool's own id is eight characters of hex, which tells a reader
        # nothing. The address keeps that, so the id can be the readable one.
        "id": kebab(title) or course["slug"],
        "name": title,
        "emoji": emoji_of(course["name"]),
        "tag": "AI SKILL DROP",
        "blurb": course["blurb"][:90],
        "dropped": course["created"] or date.today().isoformat(),
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
    ready = [c for c in courses if is_ready(c)]
    new = [c for c in ready if not already_known(catalogue, c, skip)]

    print(f"{len(courses)} courses in the classroom, "
          f"{len(catalogue.get('kits', []))} in the list, {len(new)} new")
    held = len(courses) - len(ready)
    if held:
        print(f"  {held} still unpublished - held back until they go live")

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

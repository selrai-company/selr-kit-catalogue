# Selr AI kit catalogue

The live list of kits in the [SELR AI Premium classroom](https://www.skool.com/selr-ai-2055/classroom),
and the one file every member's Skill Tracker reads.

**This repository is public on purpose.** The tracker fetches `kits.json` over plain HTTPS with no
sign-in, so it has to be readable without one. Everything in here — kit names, blurbs, drop dates,
install prompts — is already published in the classroom. Nothing private belongs in this file.

## What members do with it

Their Skill Tracker fetches this list on every run, works out which of these kits are on their
computer, and shows them a card each. Publishing a kit here updates every member's report on their
next run, with no reinstall.

If the fetch fails they fall back to the last copy they saw, and the page says how old it is. That
fallback is silent by design, so **a broken address here is invisible to everyone** — check the
address returns JSON after any change to this repository's name or layout.

## Adding a kit

Append an entry and bump `as_of`:

```json
{
  "id": "xero-connector",
  "name": "Xero",
  "emoji": "📊",
  "tag": "AI SKILL DROP",
  "blurb": "Reconcile your books and read your numbers from a chat.",
  "dropped": "2026-08-19",
  "classroom_url": "https://www.skool.com/selr-ai-2055/classroom/...",
  "install_prompt": "Install the xero-connector skill for me, following ...",
  "detect": [{ "kind": "skill", "value": "xero-connector" }]
}
```

`detect` is the only field that cannot be written from memory. It says what the kit puts on a
member's computer, which is a fact about the kit's own repository rather than about the classroom.

**An entry with an empty `detect` is dropped by every member's tracker**, so a half-written one is
invisible rather than wrong. That is what makes it safe for the watcher below to commit a draft.

Four kinds of check, and no others:

| kind | value | passes when |
|---|---|---|
| `skill` | folder name | `~/.claude/skills/<value>/SKILL.md` exists |
| `agent` | file stem | `~/.claude/agents/<value>.md` exists |
| `path` | home-relative path | the path exists |
| `file_contains` | phrase, plus `path` | that file holds the phrase |

Adding a fifth kind needs a tracker release first. Until members have it, a kit using one reads as
**"can't check on this version"** on their page — never as "not installed", which would tell someone
they do not have a kit they are running.

## Checking a fingerprint before you publish

On a machine that has the kit:

```
~/.claude/skills/skill-tracker/venv/bin/python \
  ~/.claude/skills/skill-tracker/scripts/scan.py --check-kits --kits-file kits.json
```

It prints every check and what it resolved to. A new entry should read `[installed]` there. If it
says `missing` on a machine that has the kit, the entry is wrong — fix the entry, never the tracker.

## internal/kit-watch

**Everything under `internal/` is for whoever maintains this list, and for nobody else.** The
repository is public because `kits.json` has to be, not because this folder is of any use to a
member. It is not part of the kit members install, nothing in `skill-tracker-kit` refers to it, and
it runs only where you clone this repository.

A scheduled job that reads the classroom three times a day and drafts an entry for anything new.

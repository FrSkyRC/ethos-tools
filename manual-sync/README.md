# Ethos Tools — manual-sync

## `sync.py`

Syncs real manual content from [`ethos-manual`](https://github.com/robthomson/ethos-manual)'s `.odt` master files into [`ethos-manual-rework`](https://github.com/robthomson/ethos-manual-rework)'s `docs/<locale>/` tree, via `ethos-manual/forge/odt_to_markdown.py`.

`ethos-manual-rework/docs/en/` started out written from scratch rather than sourced from the real manual — this replaces that with the real thing, mechanically converted, never inventing or paraphrasing text. Meant to be re-run by hand each time a new `.odt` revision is ready, for as long as the `.odt` stays the source of truth — once writers/translators work directly in `ethos-manual-rework` (via its editor tool or plain git), this script's job is done.

### Prerequisites

- Python 3, with `Pillow` installed (`ethos-manual/forge/odt_to_markdown.py`'s own dependency — not currently pinned in that repo's `requirements.txt`, install it yourself: `pip install Pillow`).
- `ethos-manual` and `ethos-manual-rework` checked out somewhere — by default, as siblings of this `ethos-tools` checkout (override with `--ethos-manual`/`--ethos-manual-rework` if yours aren't laid out that way).

### Usage

```text
python sync.py <locale> [--ethos-manual DIR] [--ethos-manual-rework DIR] [--keep-conversion-output]
```

```bash
python sync.py en
```

This:

1. Runs `odt_to_markdown.py --split-chapters --summary` against the `.odt` configured for that locale in `LOCALE_ODT` (top of `sync.py` — hand-maintained; bump the filename there to the newer revision to sync it).
2. Regroups any top-level chapters configured in `REGROUP_SECTIONS` under a single synthetic parent section (currently: the 8 individual per-radio "layout" chapters, grouped under one "Radio Layouts" entry) — a pure `SUMMARY.md` restructuring, the underlying chapter files/folders aren't touched or moved. Needed because `.odt`'s own "one H1 = one top-level chapter" structure doesn't scale to a usable nav on its own — the real manual has ~20 top-level chapters, which overflowed `navigation.tabs` before `mkdocs.yml` switched to sidebar-only nav (`navigation.sections`) and this grouping landed. Modeled directly on the old `ethos-manual/french` tree's own `SUMMARY.md`, which already solved this the same way.
3. Replaces everything under `ethos-manual-rework/docs/<locale>/` with the conversion's (now-regrouped) output — **except** `contributing/` (real, hand-written documentation about the rework repo/site itself, not part of the Ethos manual — see `PRESERVED_TOP_LEVEL` in `sync.py` if that ever needs to grow).
4. Merges the final `SUMMARY.md` with the preserved section(s)' own existing nav entries (read back out of the file being replaced, not hardcoded — stays correct as that content changes over time).
5. Prints the conversion's own summary — files written, images extracted/converted, and any warnings (dead cross-reference anchors, undecodable/missing images, chapters `REGROUP_SECTIONS` expected to find but didn't — e.g. if a future `.odt` revision renames/removes a radio layout chapter). **Content warnings are real issues to look at, not bugs in this sync** — land the PR as-is and fix them in a follow-up, per the actual manual content, not by guessing.

Nothing is committed or pushed — review the resulting `git diff` in `ethos-manual-rework` and open a PR from it like any other change.

### Locale status

| Locale | Real `.odt` source | Synced end to end |
| --- | --- | --- |
| `en` | Yes | Yes |
| `de` | Yes | Not yet — needs the same-relative-path reconciliation below worked out first |
| `it` | Yes | Not yet |
| `es` | Yes | Not yet |
| everything else | No | Not applicable — no real source to sync from |

Every other locale previously present in `ethos-manual-rework` (`cs`, `he`, `nl`, `nb`, `pl`, `pt-BR`, `zh`, `fr`) has no real `.odt` behind it at all and was removed from that repo's `mkdocs.yml`/`docs/` rather than left as fake-translated-from-fake-English content with nothing real to eventually correct it against.

**Why `de`/`it`/`es` aren't synced yet**: `mkdocs-static-i18n`'s `docs_structure: folder` mode expects a translated page to live at the *same relative path* as its English counterpart, for locale-switching and screenshot-fallback to work. A locale's own real manual doesn't necessarily organize its chapters identically to English's — reconciling that (without inventing or reordering real content to force a fit that isn't there) is real, mostly-manual work this script doesn't attempt yet. `REGROUP_SECTIONS` also only has an `en` entry so far — each locale's own `.odt` produces its own (differently-titled, differently-slugged) chapter folders, so the radio-layout grouping needs its own per-locale entry too once one of these is actually synced.

### A note on the underlying conversion

`odt_to_markdown.py` is deliberately a pragmatic converter, not a full ODF renderer — see its own docstring for exactly what it does and doesn't handle (bold/italic only, no footnote bodies, decorative shapes skipped, etc.). This script doesn't work around or reinterpret any of that; whatever it produces is what lands.

# Ethos Tools — manual-sync

Two scripts, two different ways of getting real manual content out of
[`ethos-manual`](https://github.com/robthomson/ethos-manual)'s `.odt` master files and into
[`ethos-manual-rework`](https://github.com/robthomson/ethos-manual-rework)'s `docs/<locale>/` tree, both via
`ethos-manual/forge/odt_to_markdown.py`. Both are meant to be re-run by hand each time a new `.odt` revision is
ready, for as long as the `.odt` stays the source of truth — once writers/translators work directly in
`ethos-manual-rework` (via its editor tool or plain git), neither script's job is needed anymore.

## `sync_mapped.py` — current approach for `en`

`sync.py` (below) replaces the site's nav wholesale with the `.odt`'s own chapter breakdown. Tried first for
English, and rejected on live review: the real manual's own organization doesn't make a good site IA on its own
(too many top-level chapters, uneven granularity). `ethos-manual-rework/main`'s hand-designed nav
(`docs/en/SUMMARY.md`) is kept as-is; `sync_mapped.py` instead maps real, never-invented `.odt` text into that
*existing* structure, driven by `page_map.py`'s `PAGE_MAP` config (target path -> real source path(s)).

```bash
python sync_mapped.py en
```

`PAGE_MAP` has four shapes, matching how each part of the existing nav lines up against the real structure —
see `page_map.py`'s own docstring/comments for the full mapping and the reasoning behind each entry:

- **swap** — target page's own title/path kept, content replaced wholesale by one real page.
- **concat** — several real sub-pages folded into one target page (heading levels demoted below the first
  page's own H1), for target sections that are intentionally flatter than the real manual's own breakdown.
- **expand_children** — a target section's children fully replaced by new real per-topic pages; the section's
  landing page is either sourced from a matching real chapter intro, or (`"landing_source": "auto"`)
  auto-generated as a bare heading + link list when no single real chapter introduces the whole group (e.g.
  "Radio Notes" grouping 8 separate real per-radio chapters) — regenerated fresh every run so it can't drift
  out of sync with the real children list.
- **append_children** — real content with no target page yet, added alongside a section's existing children.

Pages not listed in `PAGE_MAP` at all (How-To Guides, Reference, Contributing) are left completely untouched —
by design, not an oversight; there's no real `.odt` chapter behind them.

Assets are copied selectively: only the specific images a mapped page actually references (parsed out of its own
`](../assets/...)` links), not the whole conversion's `assets/` tree — a blanket merge would silently overwrite
images belonging to pages this script never maps, some of which coincidentally share a filename with the real
conversion's own auto-generated asset names.

Only `en` has a `PAGE_MAP` right now. `de`/`it`/`es` need their own once their `.odt` structures are reconciled
against `en`'s target nav — likely harder than `en`'s, since (per the `sync.py` section below) their own manuals
don't necessarily organize chapters identically to English's.

## `sync.py` — mirrors the `.odt`'s own structure

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

| Locale | Real `.odt` source | Synced |
| --- | --- | --- |
| `en` | Yes | Yes, via `sync_mapped.py` + `PAGE_MAP` (see above) — not via this script's own `REGROUP_SECTIONS` mirroring, which was tried first and rejected |
| `de` | Yes | Not yet — needs its own `PAGE_MAP` reconciled against `en`'s target nav first |
| `it` | Yes | Not yet |
| `es` | Yes | Not yet |
| everything else | No | Not applicable — no real source to sync from |

Every other locale previously present in `ethos-manual-rework` (`cs`, `he`, `nl`, `nb`, `pl`, `pt-BR`, `zh`, `fr`) has no real `.odt` behind it at all and was removed from that repo's `mkdocs.yml`/`docs/` rather than left as fake-translated-from-fake-English content with nothing real to eventually correct it against.

**Why `de`/`it`/`es` aren't synced yet**: `mkdocs-static-i18n`'s `docs_structure: folder` mode expects a translated page to live at the *same relative path* as its English counterpart, for locale-switching and screenshot-fallback to work. A locale's own real manual doesn't necessarily organize its chapters identically to English's — reconciling that (without inventing or reordering real content to force a fit that isn't there) is real, mostly-manual work this script doesn't attempt yet. `REGROUP_SECTIONS` also only has an `en` entry so far — each locale's own `.odt` produces its own (differently-titled, differently-slugged) chapter folders, so the radio-layout grouping needs its own per-locale entry too once one of these is actually synced.

### A note on the underlying conversion

`odt_to_markdown.py` is deliberately a pragmatic converter, not a full ODF renderer — see its own docstring for exactly what it does and doesn't handle (bold/italic only, no footnote bodies, decorative shapes skipped, etc.). This script doesn't work around or reinterpret any of that; whatever it produces is what lands.

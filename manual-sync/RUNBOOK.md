# Runbook: bringing a branch/locale onto real `.odt`-sourced content

This is the repeatable process used to replace `ethos-manual-rework`'s
fake, written-from-scratch (or machine-translated-from-fake) content with
real text from `ethos-manual`'s `.odt` masters, first done for the `main`
branch's `en`/`de`/`es`/`it` locales. Follow this when doing the same for
another branch (e.g. `1.6`) or another locale.

See `README.md` in this same directory for what each script's flags do.
This file is the *order of operations and decisions*, not the reference.

## 0. Prerequisites

- The target `ethos-manual-rework` branch's English nav must already be on
  **literate-nav** (`docs/en/SUMMARY.md` exists, not a YAML `nav:` block in
  `mkdocs.yml`). If it isn't yet, that migration is its own, separate,
  first step — don't try to build a `PAGE_MAP` against a YAML nav.
- `ethos-manual`'s matching branch needs `forge/odt_to_markdown.py`. If the
  target `ethos-manual` branch doesn't have it (e.g. an older branch
  forked before the tool existed), port it over first — it's a single
  standalone script, no other dependencies from the branch it came from.
- Know which locales actually have a real `.odt` source on the target
  `ethos-manual` branch (check each locale's own `docs/` folder — some
  branches only have **PDF exports** for some locales, which this whole
  process can't use at all; see "PDF-only locales" below).

## 1. English first — build `PAGE_MAP`

English is always the first locale, and the only one requiring real
judgment work; every other locale rides on it (step 3).

1. Convert the real English `.odt`:
   `python forge/odt_to_markdown.py <odt> --output-dir <tmp> --split-chapters --summary`
2. Compare the real `SUMMARY.md` this produces against the target branch's
   *existing* `docs/en/SUMMARY.md` (its current, hand-authored nav — kept
   as-is, not replaced; see the `feature/sync-real-manual-content`
   rejection below for why). For every target page, decide which shape it
   needs:
   - **swap** — real content maps onto one target page 1:1. Verify a
     non-obvious rename (target title doesn't match the real chapter's
     own title) by checking the real page's own **asset filenames**, not
     just its prose title — e.g. target's "Outputs" turned out to be real
     chapter "Channels", confirmed via `model-icon-outputs.png`.
   - **concat** — several real sub-pages fold into one target page,
     because the target's nav is intentionally flatter than the real
     manual's own breakdown at that point (heading levels get demoted
     below the first page's own H1). Needed because the editor's
     `SUMMARY.md` nav format only supports 2 levels (top-level + one
     level of children) — a literal 3rd-level split isn't an option.
   - **expand_children** — a target section's children get fully replaced
     by new real per-topic pages. The section's own landing page is
     either sourced from a matching real chapter intro, or (`"landing_source":
     "auto"`) auto-generated as a bare heading + link list when no single
     real chapter introduces the whole group — give it an explicit
     `"title"` in `page_map.py` (see step 3; needed for non-English
     locales, which have no `SUMMARY.md` of their own to read a title
     back out of).
   - **append_children** — real content with no existing target page at
     all, added alongside a section's existing children.
   - **no source** — a target page genuinely has no real `.odt` chapter
     behind it (e.g. task-oriented How-To guides that read as separately-
     authored content, or site meta-pages like Contributing). Leave it
     out of `PAGE_MAP` entirely — not a bug, don't force a mapping.
3. Encode all of this in `page_map.py`'s `PAGE_MAP["en"]`.
4. Run `python sync_mapped.py en`, read every `NOTE:` it prints (missing
   sources, dangling links stripped) — each one is either an expected,
   already-understood gap or a real problem to go fix in `PAGE_MAP`.
5. Verify (see step 4 below) before committing.

## 2. Fix `odt_to_markdown.py` bugs *before* trusting a locale's structure

Before building anything for a non-English locale, convert its own real
`.odt` and structurally compare its `SUMMARY.md` against English's real
one (top-level chapter count, order, and roughly child counts). If they
don't match, don't try to work around it in `sync_mapped.py` — the
mismatch is almost certainly a **converter bug** that will bite every
locale that hits it, and the right fix is a general one in
`odt_to_markdown.py` itself. Two found and fixed so far, both worth
checking for on any new locale/branch:

- **Image-only headings promoted to chapter/section boundaries.** A
  `<text:h>` whose only content is an image (no real title text) still
  renders non-empty markdown (`![alt](path)`), so it passed the "does
  this heading have content" check and got treated as a real chapter
  split. Symptom: extra bogus top-level chapters, or a real chapter's
  child count inflated. Fixed generally: a heading only counts as a real
  boundary once its own image markdown is stripped back out and there's
  still text left.
- **No real headings in the body at all.** Some translators' documents
  have chapter/section titles styled as plain paragraphs instead of real
  ODF headings (confirmed via direct XML inspection: `<text:p>`, not
  `<text:h>`, no `text:outline-level`). Symptom: `--split-chapters`
  produces just a `Home` page and nothing else. Fixed via a TOC
  cross-reference heuristic (`extract_toc_entries`/
  `resolve_toc_fallback_headings` in `odt_to_markdown.py`) — the
  document's own auto-generated table of contents still has the real
  title list, since it was built from whatever *was* a real heading at
  some point in the document's editing history. Matching is bounded to
  each chapter's own span (using the reliably-unique chapter titles as
  anchors) rather than matched globally — see that function's own
  docstring for why two simpler approaches (first-match, require-global-
  uniqueness) were tried and rejected first before landing on this.

Re-run the conversion after any such fix and re-check the structural
comparison before moving on.

## 3. Other locales — derive `PAGE_MAP`, don't hand-write it

Once a locale's real structure matches English's (same top-level chapter
count and order — child count can differ slightly, see below),
**don't** build a second `PAGE_MAP` by hand. `sync_mapped.py`'s
`sync_locale_derived()` does it automatically: it converts both English's
and the target locale's `.odt` in the same run, matches their real
structures purely by **chapter/child position** (`build_locale_translation`),
and translates every `PAGE_MAP["en"]` real-path reference to whatever
sits at the same position in the locale's own structure
(`derive_locale_page_map`). This works without reading a word of the
target language — position correspondence is the only thing it relies on,
and that's been empirically verified structurally (see step 2) before any
of it runs.

```
python sync_mapped.py de
python sync_mapped.py es
python sync_mapped.py it
```

- `docs/<locale>/SUMMARY.md` doesn't exist and isn't created for these —
  `mkdocs-static-i18n`'s folder mode means every locale shares English's
  nav structure; only real *content* gets placed at each already-existing
  target path (`manage_nav=False`).
- A position with no counterpart in the locale's own document (its
  chapter has fewer children at that point than English's) is a real,
  honest gap — reported via a `NOTE:` and left alone, not padded or
  guessed. Every locale synced so far shares one such gap (neither
  German/Spanish/Italian's own "Configure Screens" chapter has a 5th
  child matching English's "Adding Custom Widgets").
- Once real content lands for a locale, add it to `mkdocs.yml`'s
  `extra.real_content_locales` (see step 5) in the same PR.

### PDF-only locales

If a locale only has PDF exports on the target `ethos-manual` branch (no
`.odt`), this whole process doesn't apply — `odt_to_markdown.py` is
ODT-specific, and reliable structured text/heading extraction from a PDF
is a materially different, harder problem (no clean heading markup to
begin with, image extraction is much less reliable). Don't attempt to
"mirror" the process for such a locale without first confirming PDF
extraction is even viable; treat it as a separate, scoped investigation
of its own, not an assumed extension of this runbook.

## 4. Verify before committing — every single time

Real, hard-won lessons, not optional:

- **Never use `mkdocs build --strict -q`.** The `-q` flag suppresses the
  WARNING-level log records strict mode needs to see in order to abort —
  a genuinely broken build looked completely clean locally this way,
  and it took GitHub's own CI (running the same command *without* `-q`)
  to catch real, missing-target-file link warnings that should have
  failed the build from the start. Always run the plain, non-quiet form,
  and check the actual captured exit code separately if piping output
  (a shell chain's own exit code can silently reflect the *last*
  command in the pipeline, not the build itself).
- **Re-run the sync twice in a row and diff the resulting tree.** It
  should be byte-identical. This caught a real bug (`append_children`
  duplicating its own `SUMMARY.md` bullet on a second run) that a
  single run would never surface.
- **Live-test through `ethos-manual-rework-editor`** before opening a
  PR — spot-check one page of each `PAGE_MAP` shape actually used (a
  swap, a concat, an `expand_children` group, etc.), not just that the
  build passed. This caught the stale-landing-page bug described next.
- **A section's `expand_children` landing page must be regenerated on
  every run, for every locale** — not just once for English. An early
  version only rewrote a non-English locale's *content* pages and
  assumed the (shared, English-owned) landing page didn't need
  per-locale attention; that left old, pre-sync content sitting under
  new real children until caught live.
- **Copy only the assets a page actually references**, resolved relative
  to that page's own real source path (works whether the source was at
  the conversion root — `assets/x.png` — or one level deep —
  `../assets/x.png`). A blanket merge of the entire real conversion's
  `assets/` folder into the target's existing one silently overwrote
  images belonging to pages this process never touches (How-To Guides,
  Reference, ...), some of which coincidentally shared a filename with
  the real converter's own auto-generated names.
- **Rewrite internal cross-reference links, and strip genuinely dangling
  ones.** A copied page's own links still point at the *real* source's
  original relative paths, which don't exist once that content lands at
  a different target path — rewrite them through a real-path→target-path
  map built from `PAGE_MAP` itself. A link to a real page `PAGE_MAP`
  doesn't map anywhere at all (and an image link to an asset
  `odt_to_markdown.py` itself already reported as missing on disk) has no
  valid target either way — strip it down to its plain text rather than
  leave a build-breaking dead link.

## 5. Ship

1. New branch, one PR per locale-group is fine (English first, then
   the rest together) — see the actual PR history on
   `ethos-manual-rework` for the shape this took.
2. If `odt_to_markdown.py` needed a fix (step 2), that's its own,
   separate PR against `ethos-manual`, merged *before* the locale sync
   PR that depends on it.
3. Add newly-real locales to `mkdocs.yml`'s `extra.real_content_locales`
   — read by `ethos-manual-rework-editor`'s Language picker
   (`realContentLocaleNames()` in `mkdocsConfig.ts`) to only offer
   locales actually worth editing, without restricting anything else
   (workspace creation, direct page loads for any other locale still
   work — this is a display filter, not an access control).
4. If some other locale on the branch has no real source at all and
   never will (pure machine-translated-from-fake placeholder text),
   consider removing it from `plugins.i18n.languages` in `mkdocs.yml`
   entirely (and deleting its `docs/<locale>/` folder — leaving the
   folder but removing the config entry leaves `hooks/_locales.py`,
   which discovers locales by scanning `docs_dir` directly, and
   `mkdocs.yml` disagreeing about what exists). Don't remove a locale
   that has real, hand-translated content just because it lacks an
   `.odt` source (e.g. `main`'s French, from an earlier, separate i18n
   pilot) — that's a different situation from a never-real locale.

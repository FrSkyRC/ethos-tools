#!/usr/bin/env python3
"""Maps real manual content from ethos-manual's .odt master files into
ethos-manual-rework's docs/<locale>/ tree -- WITHOUT changing that tree's
existing nav structure (docs/<locale>/SUMMARY.md).

Different from sync.py, which replaces the site's nav wholesale with the
real manual's own chapter breakdown (its REGROUP_SECTIONS mode). That
approach was tried first and rejected on live review: the real manual's
own organization doesn't make a good site IA. ethos-manual-rework/main's
existing nav design is kept; this script instead pulls real, never-
invented .odt text into specific pages of that existing nav, per the
PAGE_MAP config in page_map.py (target path -> real source path(s)).

Like sync.py, meant to be re-run each time a new .odt revision is ready --
PAGE_MAP is the repeatable, mechanical part; the one-time judgment work
was building PAGE_MAP itself (matching every target page in the existing
nav against its real source, verified against the real text).

Usage:
    python sync_mapped.py en
    python sync_mapped.py en --ethos-manual ../../ethos-manual --ethos-manual-rework ../../ethos-manual-rework
    python sync_mapped.py en --keep-conversion-output   # inspect the raw odt_to_markdown.py output afterward

Only "en" has a PAGE_MAP right now -- see page_map.py.
"""
from __future__ import annotations

import argparse
import posixpath
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from page_map import PAGE_MAP  # noqa: E402
from sync import BULLET_RE, LOCALE_ODT, run_conversion  # noqa: E402

HEADING_RE = re.compile(r"^(#{1,6})(\s+\S.*)$")
FENCE_RE = re.compile(r"^\s*```")
# Markdown link/image targets, e.g. "[Edit model](../model-setup/edit-model.md)"
# or "![alt](../assets/x.png)" -- (is-image, link text, link target).
LINK_RE = re.compile(r"(!?)\[([^\]]*)\]\(([^)\s]+)\)")


def build_real_to_target_map(page_map: dict) -> dict[str, str]:
    """real source path (relative to conversion_dir) -> target path
    (relative to docs_root), covering every entry PAGE_MAP places
    somewhere. Used by rewrite_internal_links() to fix up a copied page's
    own cross-references to *other* real chapters PAGE_MAP also moved --
    those still point at the real chapter's original relative path
    (e.g. "../configure-screens/index.md"), which no longer exists once
    that chapter's content lands at its target path (e.g.
    "../displays/index.md") instead."""
    m: dict[str, str] = {}
    for target_rel, real_rel in page_map.get("swap", {}).items():
        m[real_rel] = target_rel
    for target_rel, real_rels in page_map.get("concat", {}).items():
        for real_rel in real_rels:
            m[real_rel] = target_rel
    for section_path, spec in page_map.get("expand_children", {}).items():
        landing_source = spec.get("landing_source")
        if landing_source and landing_source != "auto":
            m[landing_source] = section_path
        for _title, target_child_rel, real_source_rel in spec["children"]:
            m[real_source_rel] = target_child_rel
    for _section_path, child_specs in page_map.get("append_children", {}).items():
        for _title, target_child_rel, real_source_rel in child_specs:
            m[real_source_rel] = target_child_rel
    return m


def rewrite_internal_links(content: str, real_rel: str, target_rel: str, real_to_target: dict[str, str]) -> str:
    """Rewrites a copied page's own relative links that point at another
    real chapter PAGE_MAP also relocated, so they point at that chapter's
    *target* path instead. Links to assets, external URLs, bare anchors,
    or any real page PAGE_MAP doesn't know about are left untouched
    (asset links already stay valid unrewritten -- see
    copy_referenced_assets; a link to a real page outside PAGE_MAP has no
    target to point at, so rewriting it would just break it a different
    way -- left as a real, pre-existing dead link instead, same as this
    sync's other known-deferred issues)."""
    real_dir = posixpath.dirname(real_rel)
    target_dir = posixpath.dirname(target_rel)

    def repl(m: re.Match) -> str:
        bang, text, link = m.group(1), m.group(2), m.group(3)
        if link.startswith(("http://", "https://", "mailto:", "#")) or "assets/" in link:
            return m.group(0)
        path_part, sep, anchor = link.partition("#")
        if not path_part:
            return m.group(0)
        resolved = posixpath.normpath(posixpath.join(real_dir, path_part))
        new_target = real_to_target.get(resolved)
        if not new_target:
            return m.group(0)
        new_link = posixpath.relpath(new_target, target_dir)
        if sep:
            new_link += sep + anchor
        return f"{bang}[{text}]({new_link})"

    return LINK_RE.sub(repl, content)


def strip_dangling_links(content: str, target_rel: str, docs_root: Path) -> tuple[str, int]:
    """Final safety net, run after every page PAGE_MAP places: a copied
    page can still contain a relative link to *another real chapter that
    PAGE_MAP deliberately never mapped anywhere* (rewrite_internal_links
    only fixes links to chapters PAGE_MAP does place somewhere), or an
    image link whose source asset odt_to_markdown.py itself already
    reported as missing on disk (a translator's linked-image file that
    genuinely isn't present -- happens for some German images; see this
    sync's own commit/PR notes). Either way there's no valid target, so
    leaving it dead isn't an option under mkdocs build --strict --
    instead drop the link syntax and keep just its visible text (empty,
    for an image with no alt text, which simply removes the broken
    image reference) -- mirrors how a print manual would read the same
    cross-reference with no live link/image at all. Never touches bare
    #anchor-only links (anchor mismatches are a separate,
    non-strict-breaking, already-documented known issue)."""
    target_dir = posixpath.dirname(target_rel)
    count = [0]

    def repl(m: re.Match) -> str:
        bang, text, link = m.group(1), m.group(2), m.group(3)
        if link.startswith(("http://", "https://", "mailto:", "#")):
            return m.group(0)
        path_part, _sep, _anchor = link.partition("#")
        if not path_part:
            return m.group(0)
        resolved = posixpath.normpath(posixpath.join(target_dir, path_part))
        if (docs_root / resolved).exists():
            return m.group(0)
        count[0] += 1
        return text

    new_content = LINK_RE.sub(repl, content)
    return new_content, count[0]


def demote_headings(text: str, levels: int = 1) -> str:
    """Shifts every Markdown heading down by `levels` (# -> ##, capped at
    ######), skipping fenced code blocks so a '#' comment inside one isn't
    mistaken for a heading. Used by build_concat_content() so a chapter's
    sub-pages read as sub-sections of the target page they're folded
    into, instead of colliding with its own H1."""
    out_lines = []
    in_fence = False
    for line in text.split("\n"):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            out_lines.append(line)
            continue
        if not in_fence:
            m = HEADING_RE.match(line)
            if m:
                new_level = min(len(m.group(1)) + levels, 6)
                line = ("#" * new_level) + m.group(2)
        out_lines.append(line)
    return "\n".join(out_lines)


def copy_referenced_assets(content: str, real_rel: str, conversion_dir: Path, docs_root: Path) -> int:
    """Copies only the specific assets a just-written page actually
    references (parsed straight out of its own links, resolved relative
    to the *source* page's own original directory -- real_rel -- so this
    works whether the link reads "../assets/x.png" (any non-root page) or
    plain "assets/x.png" (a page originally at the conversion root, e.g.
    index.md)) into docs_root/assets/, instead of merging the real
    conversion's entire assets/ tree wholesale. Deliberate: target's
    assets/ already holds images for pages this script never maps
    (How-To Guides, Reference, Contributing, ...), some coincidentally
    same-named as real's own auto-generated asset names -- a blanket
    merge silently overwrote those with unrelated real images the first
    time this was tried. Returns how many files were copied."""
    real_dir = posixpath.dirname(real_rel)
    n = 0
    for m in LINK_RE.finditer(content):
        link = m.group(3)
        path_part = link.partition("#")[0]
        if not path_part:
            continue
        resolved = posixpath.normpath(posixpath.join(real_dir, path_part))
        if not (resolved == "assets" or resolved.startswith("assets/")):
            continue
        src = conversion_dir / resolved
        if not src.exists():
            continue
        dest = docs_root / resolved
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        n += 1
    return n


def build_concat_content(conversion_dir: Path, real_rels: list[str], target_rel: str,
                          real_to_target: dict[str, str], docs_root: Path, asset_counter: list[int]) -> str:
    """Concatenates several real pages into one target page's content --
    the first page's heading levels are kept as-is (its H1 becomes the
    target page's own H1); every page after it is demoted one level so
    it reads as a sub-section instead of a second top-level heading.
    Each source page's own internal links/assets are rewritten/copied
    individually (see rewrite_internal_links, copy_referenced_assets)
    before joining, since they were written relative to that source's
    own original directory."""
    parts = []
    for i, rel in enumerate(real_rels):
        text = (conversion_dir / rel).read_text(encoding="utf-8").rstrip("\n")
        asset_counter[0] += copy_referenced_assets(text, rel, conversion_dir, docs_root)
        text = rewrite_internal_links(text, rel, target_rel, real_to_target)
        if i > 0:
            text = demote_headings(text, levels=1)
        parts.append(text)
    return "\n\n".join(parts) + "\n"


def find_section_line_range(lines: list[str], section_path: str) -> tuple[int, int] | None:
    """Locates a top-level SUMMARY.md section by its own path (more
    stable than matching by title, which can carry stray bold-artifact
    markup) -- returns (index of its own bullet line, index just past its
    last child line)."""
    for i, line in enumerate(lines):
        m = BULLET_RE.match(line)
        if not m or len(m.group(1)) != 0 or m.group(3) != section_path:
            continue
        end = i + 1
        while end < len(lines):
            nxt = BULLET_RE.match(lines[end])
            if nxt and len(nxt.group(1)) == 0:
                break
            end += 1
        return i, end
    return None


def place_page(src: Path, real_rel: str, target_rel: str, conversion_dir: Path, docs_root: Path,
                real_to_target: dict[str, str], asset_counter: list[int]) -> None:
    """Copies one real page to its target path, rewriting its own
    internal links to other relocated real chapters (see
    rewrite_internal_links) and pulling in just the assets it references
    (see copy_referenced_assets)."""
    dest = docs_root / target_rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    content = src.read_text(encoding="utf-8")
    asset_counter[0] += copy_referenced_assets(content, real_rel, conversion_dir, docs_root)
    content = rewrite_internal_links(content, real_rel, target_rel, real_to_target)
    dest.write_text(content, encoding="utf-8")


def auto_landing_content(section_title: str, section_path: str, children: list[tuple[str, str, str]]) -> str:
    """Landing page for a section whose children were fully replaced but
    which has no single real chapter to source its own intro from (e.g.
    "Radio Notes" -> 8 separate real per-radio chapters). Purely
    structural -- a heading plus a bare link list, no descriptive prose --
    same precedent as sync.py's REGROUP_SECTIONS "Radio Layouts" landing
    page ("# Radio Layouts\n"): navigational scaffolding, not manual
    content, so it doesn't run afoul of "never invent text". Written
    fresh every sync (not preserved across runs) specifically so it can
    never drift out of sync with the real children list the way the old,
    hand-written landing page did the first time this ran."""
    section_dir = Path(section_path).parent.as_posix()
    lines = [f"# {section_title}", ""]
    for title, target_child_rel, _real_source_rel in children:
        child_dir = Path(target_child_rel).parent.as_posix()
        link = Path(target_child_rel).name if child_dir == section_dir else target_child_rel
        lines.append(f"- [{title}]({link})")
    return "\n".join(lines) + "\n"


def apply_page_map(locale: str, page_map: dict, conversion_dir: Path, docs_root: Path,
                    manage_nav: bool = True) -> None:
    """Places every PAGE_MAP entry's real content at its target path.

    manage_nav controls whether docs/<locale>/SUMMARY.md itself gets
    edited (expand_children replacing a section's children,
    append_children adding new ones). True for English, which OWNS the
    shared nav structure every other locale's pages inherit via
    mkdocs-static-i18n's folder mode -- for a non-English locale, the
    exact same target paths already exist in nav (added when English's
    SUMMARY.md was edited) and docs/<locale>/ deliberately has no
    SUMMARY.md of its own to edit; only the *content* at those paths
    needs placing (see sync_locale_derived)."""
    asset_count = [0]  # mutable counter, threaded through place_page()
    real_to_target = build_real_to_target_map(page_map)
    touched: set[str] = set()  # every target_rel PAGE_MAP wrote, for the dangling-link pass at the end

    swaps = page_map.get("swap", {})
    print(f"\n=== Applying {len(swaps)} 1:1 content swap(s) ===")
    for target_rel, real_rel in swaps.items():
        src = conversion_dir / real_rel
        if not src.exists():
            print(f"NOTE: swap source missing, left as-is: {target_rel} <- {real_rel}")
            continue
        place_page(src, real_rel, target_rel, conversion_dir, docs_root, real_to_target, asset_count)
        touched.add(target_rel)

    concats = page_map.get("concat", {})
    print(f"\n=== Applying {len(concats)} concatenation(s) ===")
    for target_rel, real_rels in concats.items():
        missing = [r for r in real_rels if not (conversion_dir / r).exists()]
        if missing:
            print(f"NOTE: concat source(s) missing, left as-is: {target_rel} <- {missing}")
            continue
        dest = docs_root / target_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        content = build_concat_content(conversion_dir, real_rels, target_rel, real_to_target, docs_root, asset_count)
        dest.write_text(content, encoding="utf-8")
        touched.add(target_rel)

    if manage_nav:
        summary_path = docs_root / "SUMMARY.md"
        lines = summary_path.read_text(encoding="utf-8").split("\n")
    else:
        summary_path = None
        lines = []

    expand = page_map.get("expand_children", {})
    print(f"\n=== Applying {len(expand)} section child-replacement(s) ===")
    for section_path, spec in expand.items():
        if manage_nav:
            rng = find_section_line_range(lines, section_path)
            if rng is None:
                print(f"NOTE: section not found in SUMMARY.md, skipped: {section_path}")
                continue
            start, end = rng
        landing_source = spec.get("landing_source")
        new_children = []
        for title, target_child_rel, real_source_rel in spec["children"]:
            src = conversion_dir / real_source_rel
            if not src.exists():
                print(f"NOTE: child source missing, skipped: {target_child_rel} <- {real_source_rel}")
                continue
            place_page(src, real_source_rel, target_child_rel, conversion_dir, docs_root, real_to_target, asset_count)
            touched.add(target_child_rel)
            if manage_nav:
                new_children.append(f"    * [{title}]({target_child_rel})")

        if landing_source == "auto":
            # Regenerated for every locale (title stays in English --
            # structural scaffolding, not translated prose; see
            # page_map.py's own comment on this). Deliberately NOT
            # skipped for non-English locales: leaving a non-English
            # locale's *previous* landing page in place here would keep
            # whatever stale content/links it had before this section's
            # children were replaced -- the exact bug this auto-
            # regeneration exists to prevent in the first place (see
            # this module's/PR's commit history for the English case
            # that surfaced it).
            content = auto_landing_content(spec["title"], section_path, spec["children"])
            (docs_root / section_path).write_text(content, encoding="utf-8")
            touched.add(section_path)
        elif landing_source:
            src = conversion_dir / landing_source
            if src.exists():
                place_page(src, landing_source, section_path, conversion_dir, docs_root, real_to_target, asset_count)
                touched.add(section_path)
            else:
                print(f"NOTE: landing source missing for {section_path}: {landing_source}")
        # landing_source omitted/None entirely: section's own existing
        # landing page is left untouched (only valid when nothing about
        # it depends on the specific children list, unlike "auto").

        if manage_nav:
            lines = lines[:start + 1] + new_children + lines[end:]

    append = page_map.get("append_children", {})
    print(f"\n=== Applying {len(append)} section child-addition(s) ===")
    for section_path, child_specs in append.items():
        if manage_nav:
            rng = find_section_line_range(lines, section_path)
            if rng is None:
                print(f"NOTE: section not found in SUMMARY.md, skipped: {section_path}")
                continue
            start, end = rng
            # Idempotency: a page this same entry already added on a
            # previous sync run is still one of this section's children
            # (append_children never removes anything, unlike
            # expand_children) -- re-running against that output must add
            # its *content* fresh (below) but must not add its
            # SUMMARY.md bullet a second time.
            existing_paths = {
                em.group(3) for line in lines[start + 1:end] if (em := BULLET_RE.match(line))
            }
            new_children = []
        for title, target_child_rel, real_source_rel in child_specs:
            src = conversion_dir / real_source_rel
            if not src.exists():
                print(f"NOTE: child source missing, skipped: {target_child_rel} <- {real_source_rel}")
                continue
            place_page(src, real_source_rel, target_child_rel, conversion_dir, docs_root, real_to_target, asset_count)
            touched.add(target_child_rel)
            if manage_nav and target_child_rel not in existing_paths:
                new_children.append(f"    * [{title}]({target_child_rel})")
        if manage_nav:
            lines = lines[:end] + new_children + lines[end:]

    if manage_nav:
        summary_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n=== Copied {asset_count[0]} referenced asset(s) into docs/{locale}/assets/ ===")

    print(f"\n=== Checking {len(touched)} placed page(s) for dangling internal links ===")
    stripped_total = 0
    for target_rel in sorted(touched):
        path = docs_root / target_rel
        content = path.read_text(encoding="utf-8")
        new_content, n = strip_dangling_links(content, target_rel, docs_root)
        if n:
            path.write_text(new_content, encoding="utf-8")
            stripped_total += n
            print(f"NOTE: stripped {n} dangling link(s) in {target_rel} (pointed at a real page "
                  f"PAGE_MAP doesn't place anywhere -- kept the link's own text, dropped the link itself)")
    if stripped_total:
        print(f"{stripped_total} dangling link(s) stripped in total -- see NOTEs above for exactly where; "
              f"if any of those point at real content worth mapping properly, that's a PAGE_MAP gap to fix, "
              f"not something this pass should paper over silently.")


def parse_real_summary(path: Path) -> list[dict]:
    """Parses a raw (real-conversion, not target) SUMMARY.md into ordered
    top-level chapters, each with its own index_path and an ordered list
    of its children's paths. Used by build_locale_translation() to line
    up one locale's real structure against another's, position for
    position, without caring what either locale's chapter/child is
    actually *titled* (which this function doesn't even keep)."""
    lines = path.read_text(encoding="utf-8").split("\n")
    chapters: list[dict] = []
    cur: dict | None = None
    for line in lines:
        m = BULLET_RE.match(line)
        if not m:
            continue
        indent, _title, target = m.groups()
        if len(indent) == 0:
            if target == "index.md":
                continue  # Home
            cur = {"index_path": target, "children": []}
            chapters.append(cur)
        elif cur is not None:
            cur["children"].append(target)
    return chapters


def build_locale_translation(en_chapters: list[dict], locale_chapters: list[dict]) -> dict[str, str]:
    """en real path -> locale real path, matched purely by position
    (chapter index, then child index) -- see this module's docstring and
    the PR/commit notes for why this is safe: English's and a locale's
    real top-level chapter count/order have been confirmed (for de/es,
    after fixing the image-only-heading bug in odt_to_markdown.py) to
    match exactly. Raises if the top-level count doesn't match -- that's
    real structural drift needing a human look, not something to guess
    past silently."""
    if len(en_chapters) != len(locale_chapters):
        raise SystemExit(
            f"Structural mismatch: English's real structure has {len(en_chapters)} top-level "
            f"chapters, this locale's has {len(locale_chapters)} -- can't derive a positional "
            f"PAGE_MAP safely from a mismatched chapter count. This needs a human look (likely "
            f"the .odt's own structure has drifted, or has its own conversion quirk like the "
            f"image-only-heading bug already fixed for Spanish) -- not a guess.")
    # parse_real_summary() deliberately excludes the Home bullet (it's not
    # a real chapter), but PAGE_MAP["en"]'s swap entry for the target
    # Home page ("index.md": "index.md") still references it by that
    # same literal path in every locale -- map it directly rather than
    # positionally.
    translation: dict[str, str] = {"index.md": "index.md"}
    for en_ch, loc_ch in zip(en_chapters, locale_chapters):
        translation[en_ch["index_path"]] = loc_ch["index_path"]
        for j, en_child in enumerate(en_ch["children"]):
            if j < len(loc_ch["children"]):
                translation[en_child] = loc_ch["children"][j]
            # else: this locale's chapter has fewer children than
            # English's at this position -- any PAGE_MAP entry
            # referencing en_child reports its own NOTE (see
            # derive_locale_page_map) rather than guessing a target.
    return translation


def derive_locale_page_map(en_page_map: dict, translation: dict[str, str]) -> dict:
    """Rebuilds English's PAGE_MAP for another locale by translating
    every real-path reference through translation (see
    build_locale_translation) -- target paths are untouched (shared
    across every locale via mkdocs-static-i18n's folder mode); only
    which *real* file each target draws from changes. An entry whose
    real path has no positional counterpart in this locale (its chapter
    has fewer children at that position) is dropped with a NOTE --
    real, honest incompleteness, not invented content filling the gap."""
    def tr(real_rel: str, context: str) -> str | None:
        t = translation.get(real_rel)
        if t is None:
            print(f"NOTE: no positional counterpart for real path '{real_rel}' ({context}) -- "
                  f"this locale's real structure doesn't have a chapter/child at English's "
                  f"position. Skipped.")
        return t

    out: dict = {"swap": {}, "concat": {}, "expand_children": {}, "append_children": {}}

    for target_rel, real_rel in en_page_map.get("swap", {}).items():
        t = tr(real_rel, f"swap -> {target_rel}")
        if t:
            out["swap"][target_rel] = t

    for target_rel, real_rels in en_page_map.get("concat", {}).items():
        translated = [t for r in real_rels if (t := tr(r, f"concat -> {target_rel}"))]
        if translated:
            out["concat"][target_rel] = translated

    for section_path, spec in en_page_map.get("expand_children", {}).items():
        landing_source = spec.get("landing_source")
        new_landing = landing_source if landing_source == "auto" else (
            tr(landing_source, f"expand_children landing -> {section_path}") if landing_source else None)
        new_children = []
        for title, target_child_rel, real_source_rel in spec["children"]:
            t = tr(real_source_rel, f"expand_children child -> {target_child_rel}")
            if t:
                new_children.append((title, target_child_rel, t))
        if new_children:
            out["expand_children"][section_path] = {
                "title": spec["title"], "landing_source": new_landing, "children": new_children,
            }

    for section_path, child_specs in en_page_map.get("append_children", {}).items():
        new_children = []
        for title, target_child_rel, real_source_rel in child_specs:
            t = tr(real_source_rel, f"append_children -> {target_child_rel}")
            if t:
                new_children.append((title, target_child_rel, t))
        if new_children:
            out["append_children"][section_path] = new_children

    return out


def sync_locale_mapped(locale: str, ethos_manual: Path, ethos_manual_rework: Path, keep_output: bool) -> None:
    if locale not in LOCALE_ODT:
        raise SystemExit(f"No .odt configured for locale '{locale}' -- see LOCALE_ODT in sync.py.")

    odt_path = ethos_manual / LOCALE_ODT[locale]
    if not odt_path.exists():
        raise SystemExit(f"Configured .odt not found: {odt_path}\n"
                          f"(LOCALE_ODT['{locale}'] in sync.py may need bumping to a newer revision's filename.)")

    docs_root = ethos_manual_rework / "docs" / locale
    if not (docs_root / "SUMMARY.md").exists():
        raise SystemExit(f"No existing SUMMARY.md at {docs_root} -- sync_mapped.py maps real content "
                          f"into an *existing* nav, it doesn't create one from scratch.")

    conversion_dir = Path(tempfile.mkdtemp(prefix="manual-sync-mapped-")) if not keep_output else \
        ethos_manual / LOCALE_ODT[locale].split("/")[0] / "markdown-mapped"

    print(f"\n=== Converting {odt_path.name} ===")
    run_conversion(ethos_manual, odt_path, conversion_dir)

    print(f"\n=== Mapping real content into docs/{locale}/ (existing nav structure preserved) ===")
    apply_page_map(locale, PAGE_MAP[locale], conversion_dir, docs_root, manage_nav=True)

    if not keep_output:
        shutil.rmtree(conversion_dir, ignore_errors=True)

    print(f"\nMapped {locale}: {odt_path.name} -> {docs_root} (nav structure unchanged)")
    print("Review the diff before committing -- pages not listed in PAGE_MAP (How-To Guides, "
          "Reference, Contributing, ...) are untouched by design, not an oversight.")


def sync_locale_derived(locale: str, ethos_manual: Path, ethos_manual_rework: Path, keep_output: bool) -> None:
    """Non-English locale path: no PAGE_MAP is hand-written for these --
    it's derived from PAGE_MAP["en"] by matching this locale's real
    structure against English's real structure position for position
    (see build_locale_translation/derive_locale_page_map). Converts BOTH
    English's and this locale's .odt in the same run so both real
    structures are fresh and consistent with each other."""
    if locale == "en":
        raise SystemExit("sync_locale_derived is for non-English locales; use sync_locale_mapped for en.")
    if locale not in LOCALE_ODT:
        raise SystemExit(f"No .odt configured for locale '{locale}' -- see LOCALE_ODT in sync.py.")

    en_odt = ethos_manual / LOCALE_ODT["en"]
    loc_odt = ethos_manual / LOCALE_ODT[locale]
    for p in (en_odt, loc_odt):
        if not p.exists():
            raise SystemExit(f"Configured .odt not found: {p} (see LOCALE_ODT in sync.py).")

    docs_root = ethos_manual_rework / "docs" / locale
    if not docs_root.exists():
        raise SystemExit(f"No docs/{locale}/ in ethos-manual-rework -- this locale isn't set up in "
                          f"mkdocs.yml/docs/ yet, sync_mapped.py doesn't create a new locale from scratch.")

    keep_dir = lambda odt_key, suffix: ethos_manual / LOCALE_ODT[odt_key].split("/")[0] / f"markdown-mapped-{suffix}"
    en_dir = Path(tempfile.mkdtemp(prefix="manual-sync-mapped-en-")) if not keep_output else keep_dir("en", "en")
    loc_dir = Path(tempfile.mkdtemp(prefix=f"manual-sync-mapped-{locale}-")) if not keep_output else \
        keep_dir(locale, locale)

    print(f"\n=== Converting {en_odt.name} (English, for position reference) ===")
    run_conversion(ethos_manual, en_odt, en_dir)
    print(f"\n=== Converting {loc_odt.name} ===")
    run_conversion(ethos_manual, loc_odt, loc_dir)

    en_chapters = parse_real_summary(en_dir / "SUMMARY.md")
    loc_chapters = parse_real_summary(loc_dir / "SUMMARY.md")
    translation = build_locale_translation(en_chapters, loc_chapters)
    locale_page_map = derive_locale_page_map(PAGE_MAP["en"], translation)

    print(f"\n=== Mapping real content into docs/{locale}/ (shared nav structure, content only) ===")
    apply_page_map(locale, locale_page_map, loc_dir, docs_root, manage_nav=False)

    if not keep_output:
        shutil.rmtree(en_dir, ignore_errors=True)
        shutil.rmtree(loc_dir, ignore_errors=True)

    print(f"\nMapped {locale}: {loc_odt.name} -> {docs_root} (derived from PAGE_MAP['en'] positionally)")
    print("Review the diff before committing -- docs/{locale}/SUMMARY.md doesn't exist and wasn't "
          "created; this locale's nav is entirely inherited from docs/en/SUMMARY.md + mkdocs.yml's "
          "nav_translations, per mkdocs-static-i18n's folder mode.".format(locale=locale))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("locale", choices=sorted(LOCALE_ODT), help="which locale to map")
    parser.add_argument("--ethos-manual", type=Path, default=Path(__file__).resolve().parents[2] / "ethos-manual",
                         help="path to the ethos-manual checkout (default: sibling of this repo)")
    parser.add_argument("--ethos-manual-rework", type=Path,
                         default=Path(__file__).resolve().parents[2] / "ethos-manual-rework",
                         help="path to the ethos-manual-rework checkout (default: sibling of this repo)")
    parser.add_argument("--keep-conversion-output", action="store_true", dest="keep_output",
                         help="leave odt_to_markdown.py's raw output on disk afterward "
                              "(under <locale-dir>/markdown-mapped*/) instead of using a temp dir")
    args = parser.parse_args()

    ethos_manual = args.ethos_manual.resolve()
    ethos_manual_rework = args.ethos_manual_rework.resolve()
    if args.locale == "en":
        sync_locale_mapped(args.locale, ethos_manual, ethos_manual_rework, args.keep_output)
    else:
        sync_locale_derived(args.locale, ethos_manual, ethos_manual_rework, args.keep_output)


if __name__ == "__main__":
    main()

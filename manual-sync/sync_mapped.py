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
ASSET_REF_RE = re.compile(r"\]\(\.\./assets/([^)]+)\)")


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


def build_concat_content(conversion_dir: Path, real_rels: list[str]) -> str:
    """Concatenates several real pages into one target page's content --
    the first page's heading levels are kept as-is (its H1 becomes the
    target page's own H1); every page after it is demoted one level so
    it reads as a sub-section instead of a second top-level heading."""
    parts = []
    for i, rel in enumerate(real_rels):
        text = (conversion_dir / rel).read_text(encoding="utf-8").rstrip("\n")
        if i > 0:
            text = demote_headings(text, levels=1)
        parts.append(text)
    return "\n\n".join(parts) + "\n"


def copy_referenced_assets(content: str, conversion_dir: Path, docs_root: Path) -> int:
    """Copies only the specific assets a just-written page actually
    references (parsed straight out of its own `](../assets/...)` links)
    into docs_root/assets/, instead of merging the real conversion's
    entire assets/ tree wholesale. Deliberate: target's assets/ already
    holds images for pages this script never maps (How-To Guides,
    Reference, Contributing, ...), some coincidentally same-named as
    real's own auto-generated asset names -- a blanket merge silently
    overwrote those with unrelated real images the first time this was
    tried. Returns how many files were copied."""
    n = 0
    for m in ASSET_REF_RE.finditer(content):
        rel = m.group(1)
        src = conversion_dir / "assets" / rel
        if not src.exists():
            continue
        dest = docs_root / "assets" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        n += 1
    return n


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


def place_page(src: Path, dest: Path, conversion_dir: Path, docs_root: Path, asset_counter: list[int]) -> None:
    """Copies one real page to its target path and pulls in just the
    assets it references (see copy_referenced_assets)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    asset_counter[0] += copy_referenced_assets(dest.read_text(encoding="utf-8"), conversion_dir, docs_root)


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


def apply_page_map(locale: str, conversion_dir: Path, docs_root: Path) -> None:
    page_map = PAGE_MAP.get(locale)
    if not page_map:
        raise SystemExit(f"No PAGE_MAP configured for locale '{locale}' -- see page_map.py.")

    asset_count = [0]  # mutable counter, threaded through place_page()

    swaps = page_map.get("swap", {})
    print(f"\n=== Applying {len(swaps)} 1:1 content swap(s) ===")
    for target_rel, real_rel in swaps.items():
        src = conversion_dir / real_rel
        if not src.exists():
            print(f"NOTE: swap source missing, left as-is: {target_rel} <- {real_rel}")
            continue
        place_page(src, docs_root / target_rel, conversion_dir, docs_root, asset_count)

    concats = page_map.get("concat", {})
    print(f"\n=== Applying {len(concats)} concatenation(s) ===")
    for target_rel, real_rels in concats.items():
        missing = [r for r in real_rels if not (conversion_dir / r).exists()]
        if missing:
            print(f"NOTE: concat source(s) missing, left as-is: {target_rel} <- {missing}")
            continue
        dest = docs_root / target_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        content = build_concat_content(conversion_dir, real_rels)
        dest.write_text(content, encoding="utf-8")
        asset_count[0] += copy_referenced_assets(content, conversion_dir, docs_root)

    summary_path = docs_root / "SUMMARY.md"
    lines = summary_path.read_text(encoding="utf-8").split("\n")

    expand = page_map.get("expand_children", {})
    print(f"\n=== Applying {len(expand)} section child-replacement(s) ===")
    for section_path, spec in expand.items():
        rng = find_section_line_range(lines, section_path)
        if rng is None:
            print(f"NOTE: section not found in SUMMARY.md, skipped: {section_path}")
            continue
        start, end = rng
        landing_source = spec.get("landing_source")
        section_title = BULLET_RE.match(lines[start]).group(2)
        new_children = []
        for title, target_child_rel, real_source_rel in spec["children"]:
            src = conversion_dir / real_source_rel
            if not src.exists():
                print(f"NOTE: child source missing, skipped: {target_child_rel} <- {real_source_rel}")
                continue
            place_page(src, docs_root / target_child_rel, conversion_dir, docs_root, asset_count)
            new_children.append(f"    * [{title}]({target_child_rel})")

        if landing_source == "auto":
            content = auto_landing_content(section_title, section_path, spec["children"])
            (docs_root / section_path).write_text(content, encoding="utf-8")
        elif landing_source:
            src = conversion_dir / landing_source
            if src.exists():
                place_page(src, docs_root / section_path, conversion_dir, docs_root, asset_count)
            else:
                print(f"NOTE: landing source missing for {section_path}: {landing_source}")
        # landing_source omitted/None entirely: section's own existing
        # landing page is left untouched (only valid when nothing about
        # it depends on the specific children list, unlike "auto").

        lines = lines[:start + 1] + new_children + lines[end:]

    append = page_map.get("append_children", {})
    print(f"\n=== Applying {len(append)} section child-addition(s) ===")
    for section_path, child_specs in append.items():
        rng = find_section_line_range(lines, section_path)
        if rng is None:
            print(f"NOTE: section not found in SUMMARY.md, skipped: {section_path}")
            continue
        _, end = rng
        new_children = []
        for title, target_child_rel, real_source_rel in child_specs:
            src = conversion_dir / real_source_rel
            if not src.exists():
                print(f"NOTE: child source missing, skipped: {target_child_rel} <- {real_source_rel}")
                continue
            place_page(src, docs_root / target_child_rel, conversion_dir, docs_root, asset_count)
            new_children.append(f"    * [{title}]({target_child_rel})")
        lines = lines[:end] + new_children + lines[end:]

    summary_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n=== Copied {asset_count[0]} referenced asset(s) into docs/{locale}/assets/ ===")


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
    apply_page_map(locale, conversion_dir, docs_root)

    if not keep_output:
        shutil.rmtree(conversion_dir, ignore_errors=True)

    print(f"\nMapped {locale}: {odt_path.name} -> {docs_root} (nav structure unchanged)")
    print("Review the diff before committing -- pages not listed in PAGE_MAP (How-To Guides, "
          "Reference, Contributing, ...) are untouched by design, not an oversight.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("locale", choices=sorted(PAGE_MAP), help="which locale to map")
    parser.add_argument("--ethos-manual", type=Path, default=Path(__file__).resolve().parents[2] / "ethos-manual",
                         help="path to the ethos-manual checkout (default: sibling of this repo)")
    parser.add_argument("--ethos-manual-rework", type=Path,
                         default=Path(__file__).resolve().parents[2] / "ethos-manual-rework",
                         help="path to the ethos-manual-rework checkout (default: sibling of this repo)")
    parser.add_argument("--keep-conversion-output", action="store_true", dest="keep_output",
                         help="leave odt_to_markdown.py's raw output on disk afterward "
                              "(under <locale-dir>/markdown-mapped/) instead of using a temp dir")
    args = parser.parse_args()

    sync_locale_mapped(args.locale, args.ethos_manual.resolve(), args.ethos_manual_rework.resolve(), args.keep_output)


if __name__ == "__main__":
    main()

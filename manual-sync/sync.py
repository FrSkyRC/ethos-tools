#!/usr/bin/env python3
"""Syncs real manual content from ethos-manual's .odt master files into
ethos-manual-rework's docs/<locale>/ tree.

ethos-manual-rework/docs/en/ started out written from scratch (see its own
docs/en/contributing/index.md) rather than sourced from the real manual —
this script replaces that with the real thing, mechanically converted from
the .odt master via ethos-manual/forge/odt_to_markdown.py, never inventing
or paraphrasing text. Meant to be re-run by hand each time a new .odt
revision is ready (bump LOCALE_ODT below), for as long as the .odt stays
the source of truth — once translators/writers work directly in
ethos-manual-rework (via ethos-manual-rework-editor or plain git), this
script's job is done and it stops being run.

Usage:
    python sync.py en
    python sync.py en --ethos-manual ../../ethos-manual --ethos-manual-rework ../../ethos-manual-rework
    python sync.py en --keep-conversion-output   # inspect the raw odt_to_markdown.py output afterward

Only "en" is exercised/supported end to end right now — de/it/es have real
.odt sources too (see LOCALE_ODT) but syncing them needs the same-relative-
path reconciliation against English's structure worked out first (a real
locale's own manual doesn't necessarily organize chapters identically to
English's, which matters because mkdocs-static-i18n's docs_structure:
folder mode expects a translated page at the same relative path as its
English counterpart for locale-switching/asset-fallback to work) — not
attempted here yet.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# locale -> .odt path, relative to ethos-manual/. Filenames aren't a
# consistent pattern across locales (each translator's own naming choice
# each revision) -- hand-maintained on purpose, not auto-detected. Bump
# the path here when a new revision is ready to sync; that edit *is* the
# "re-run the sync" trigger.
LOCALE_ODT = {
    "en": "english/docs/EN Ethos_User_Manual_26.1.0-rev16.odt",
    "de": "german/docs/[DE] Ethos_Bedienunsanleitung_26.1.0-rev15.odt",
    "it": "italian/docs/[IT] Ethos_Manuale_Utente_26.1.0-rev16.odt",
    "es": "spanish/docs/[ES] Manual Usuario Ethos 26.1.0Rev16.odt",
}

# Top-level entries under docs/<locale>/ that this script never touches --
# real hand-written content about the rework repo/site itself, not part of
# the Ethos manual, so there's nothing for the .odt to source it from.
PRESERVED_TOP_LEVEL = {"contributing"}

BULLET_RE = re.compile(r"^(\s*)[*-]\s+\[([^\]]+)\]\(([^)]+)\)\s*$")

# Top-level chapters nested under one synthetic parent section instead of
# staying their own top-level nav entries -- purely a SUMMARY.md
# restructuring (the underlying chapter files/folders are untouched, so
# every image/cross-reference path inside them, already correct for their
# original location, stays valid). Needed because mkdocs-material's
# navigation.tabs (or, now, navigation.sections -- see mkdocs.yml) doesn't
# scale to the real manual's ~20 top-level chapters the way the old,
# invented nav's ~12 did -- confirmed live: it overflowed the tab bar.
#
# Locale-keyed because each locale's own .odt produces its own
# (differently-titled, differently-slugged) chapters -- only "en" is
# populated so far; German/Italian/Spanish's own equivalent groupings are
# part of the still-pending Phase 2 work (see this module's docstring).
#
# Modeled directly on the old ethos-manual/french tree's own SUMMARY.md,
# which already solved this exact problem the same way (one "Layouts"
# section grouping all 8 per-radio pages) -- not a new invention.
REGROUP_SECTIONS: dict[str, list[dict]] = {
    "en": [
        {
            "title": "Radio Layouts",
            "path": "radio-layouts/index.md",
            # Matches ethos-manual/french/radio-layouts/index.md's own
            # landing page -- a bare title, no body prose, purely
            # navigational scaffolding, not manual content.
            "landing_content": "# Radio Layouts\n",
            "chapter_folders": [
                "x20-x20s-layouts", "x20-pro-layout", "x20-pro-aw-layout",
                "x20r-rs-layout", "x18-x18se-layout", "xe-xes-xe-rs-layouts",
                "x14-x14rs", "twin-xlite",
            ],
        },
    ],
}


def apply_regroup(summary_text: str, sections: list[dict], output_dir: Path) -> str:
    """Nests each configured group of top-level chapters (matched by their
    folder name) under one new synthetic parent bullet, and writes that
    parent's own (minimal, non-manual-content) landing page into
    output_dir -- see REGROUP_SECTIONS above."""
    lines = summary_text.split("\n")

    for section in sections:
        folders = set(section["chapter_folders"])
        remaining: list[str] = []
        matched: list[str] = []
        found: set[str] = set()
        insert_at: int | None = None
        i = 0
        while i < len(lines):
            m = BULLET_RE.match(lines[i])
            folder = m.group(3).split("/")[0] if m and len(m.group(1)) == 0 else None
            if folder in folders:
                if insert_at is None:
                    insert_at = len(remaining)
                found.add(folder)
                end = i + 1
                while end < len(lines):
                    nxt = BULLET_RE.match(lines[end])
                    if nxt and len(nxt.group(1)) == 0:
                        break
                    end += 1
                matched.extend(("    " + line if line.strip() else line) for line in lines[i:end])
                i = end
            else:
                remaining.append(lines[i])
                i += 1

        missing = folders - found
        if missing:
            print(f"NOTE: expected chapter(s) not found to group under '{section['title']}' "
                  f"(.odt structure may have changed -- check REGROUP_SECTIONS in this script): "
                  f"{sorted(missing)}")
        if not matched:
            lines = remaining
            continue

        parent_line = f"* [{section['title']}]({section['path']})"
        lines = remaining[:insert_at] + [parent_line] + matched + remaining[insert_at:]

        landing_path = output_dir / section["path"]
        landing_path.parent.mkdir(parents=True, exist_ok=True)
        landing_path.write_text(section["landing_content"], encoding="utf-8")

    return "\n".join(lines)


def find_preserved_summary_block(summary_text: str, top_level_title: str) -> str | None:
    """Extracts one top-level section's own bullet line plus every indented
    line under it from an existing SUMMARY.md, verbatim -- e.g. the
    "Contributing" section, which this script preserves rather than
    regenerates (see PRESERVED_TOP_LEVEL). Returns None if no such section
    exists yet. Reading it back out of the file being replaced (rather than
    hardcoding its text here) means it stays correct even as that section's
    real content changes over time -- this script never needs updating just
    because someone added a new Contributing page."""
    lines = summary_text.split("\n")
    for i, line in enumerate(lines):
        m = BULLET_RE.match(line)
        if not m or len(m.group(1)) != 0 or m.group(2) != top_level_title:
            continue
        end = i + 1
        while end < len(lines):
            nxt = BULLET_RE.match(lines[end])
            if nxt and len(nxt.group(1)) == 0:
                break
            end += 1
        return "\n".join(lines[i:end])
    return None


def run_conversion(ethos_manual: Path, odt_path: Path, output_dir: Path) -> None:
    script = ethos_manual / "forge" / "odt_to_markdown.py"
    cmd = [sys.executable, str(script), str(odt_path), "--output-dir", str(output_dir),
           "--split-chapters", "--summary"]
    print(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=odt_path.parent.parent)  # run from the locale dir, e.g. english/
    if result.returncode != 0:
        raise SystemExit(f"odt_to_markdown.py failed (exit {result.returncode}) -- see output above.")


def sync_locale(locale: str, ethos_manual: Path, ethos_manual_rework: Path, keep_output: bool) -> None:
    if locale not in LOCALE_ODT:
        raise SystemExit(f"No .odt configured for locale '{locale}' -- see LOCALE_ODT in this script.")

    odt_path = ethos_manual / LOCALE_ODT[locale]
    if not odt_path.exists():
        raise SystemExit(f"Configured .odt not found: {odt_path}\n"
                          f"(LOCALE_ODT['{locale}'] may need bumping to a newer revision's filename.)")

    docs_root = ethos_manual_rework / "docs" / locale
    old_summary = docs_root / "SUMMARY.md"
    old_summary_text = old_summary.read_text(encoding="utf-8") if old_summary.exists() else ""

    conversion_dir = Path(tempfile.mkdtemp(prefix="manual-sync-")) if not keep_output else \
        ethos_manual / LOCALE_ODT[locale].split("/")[0] / "markdown"

    print(f"\n=== Converting {odt_path.name} ===")
    run_conversion(ethos_manual, odt_path, conversion_dir)

    new_summary_text = (conversion_dir / "SUMMARY.md").read_text(encoding="utf-8").rstrip("\n")
    regroup_sections = REGROUP_SECTIONS.get(locale, [])
    if regroup_sections:
        print(f"\n=== Regrouping {[s['title'] for s in regroup_sections]} ===")
        new_summary_text = apply_regroup(new_summary_text, regroup_sections, conversion_dir)

    print(f"\n=== Replacing docs/{locale}/ (preserving {sorted(PRESERVED_TOP_LEVEL)}) ===")
    docs_root.mkdir(parents=True, exist_ok=True)
    for entry in list(docs_root.iterdir()):
        if entry.name in PRESERVED_TOP_LEVEL:
            continue
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()

    for entry in conversion_dir.iterdir():
        if entry.name == "SUMMARY.md":
            continue  # merged separately, below
        dest = docs_root / entry.name
        if entry.is_dir():
            shutil.copytree(entry, dest)
        else:
            shutil.copy2(entry, dest)
    preserved_blocks = []
    for title in sorted(PRESERVED_TOP_LEVEL):
        # PRESERVED_TOP_LEVEL is a directory-name set (lowercase, matches
        # the actual folder); the SUMMARY.md bullet title is a real display
        # title ("Contributing") -- look it up by capitalizing, matching
        # the one real case this handles today. If that convention doesn't
        # hold for something else added to PRESERVED_TOP_LEVEL later,
        # this simply won't find a block to preserve and will say so.
        block = find_preserved_summary_block(old_summary_text, title.capitalize())
        if block:
            preserved_blocks.append(block)
        else:
            print(f"NOTE: no existing SUMMARY.md '{title.capitalize()}' section found to preserve "
                  f"(nothing carried forward for it -- add it back by hand if that's wrong).")

    merged_summary = new_summary_text
    if preserved_blocks:
        merged_summary += "\n" + "\n".join(preserved_blocks)
    (docs_root / "SUMMARY.md").write_text(merged_summary + "\n", encoding="utf-8")

    if not keep_output:
        shutil.rmtree(conversion_dir, ignore_errors=True)

    print(f"\nSynced {locale}: {odt_path.name} -> {docs_root}")
    print("Review the diff before committing -- this script lands the real conversion as-is, "
          "including any warnings odt_to_markdown.py printed above (dead cross-reference anchors, "
          "undecodable/missing images) -- those are real content issues to fix separately, not bugs "
          "in this sync.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("locale", choices=sorted(LOCALE_ODT), help="which locale to sync")
    parser.add_argument("--ethos-manual", type=Path, default=Path(__file__).resolve().parents[2] / "ethos-manual",
                         help="path to the ethos-manual checkout (default: sibling of this repo)")
    parser.add_argument("--ethos-manual-rework", type=Path,
                         default=Path(__file__).resolve().parents[2] / "ethos-manual-rework",
                         help="path to the ethos-manual-rework checkout (default: sibling of this repo)")
    parser.add_argument("--keep-conversion-output", action="store_true", dest="keep_output",
                         help="leave odt_to_markdown.py's raw output on disk afterward "
                              "(under <locale-dir>/markdown/) instead of using a temp dir")
    args = parser.parse_args()

    sync_locale(args.locale, args.ethos_manual.resolve(), args.ethos_manual_rework.resolve(), args.keep_output)


if __name__ == "__main__":
    main()

"""PAGE_MAP: target path (docs/<locale>/...) -> real ODT-sourced content.

Used by sync_mapped.py, which is a different sync mode from sync.py's
mirror-the-real-structure approach. sync.py's REGROUP_SECTIONS mode makes
the *site's* nav follow the real manual's own chapter breakdown -- tried
first, live-reviewed on the real PR preview, and rejected: "I really dont
like the navigation. its a mess. likewise the content is a mess." The
site's existing nav design (docs/en/SUMMARY.md on ethos-manual-rework's
main) is well-organized and is kept as-is; this module instead maps real,
never-invented .odt text INTO that existing structure.

Every path here is relative to its own root:
  - target paths: relative to ethos-manual-rework/docs/<locale>/
  - real paths: relative to the raw odt_to_markdown.py --split-chapters
    output (what sync.py calls conversion_dir)

Three shapes, matching the categorization worked out against the real
english/markdown/SUMMARY.md vs. docs/en/SUMMARY.md (see the plan file this
was built from, C:\\Users\\RobThomson\\.claude\\plans\\humble-wiggling-globe.md):

  - swap: target keeps its own path/title; its content is replaced
    wholesale by one real page's content.
  - concat: target keeps its own path/title; its content is replaced by
    several real pages concatenated into one (heading levels demoted
    below the first page's own H1) -- used where the target's nav is
    intentionally flatter than the real manual's (e.g. "Getting
    Started"'s 4 leaf pages vs. real's 4 chapters-with-sub-pages). Avoids
    a 3rd level of nav nesting docs/en/SUMMARY.md doesn't use anywhere
    else and the editor's nav parser doesn't support.
  - expand_children: a target *section*'s own children are replaced by a
    new set of real per-topic pages (own new title + target path each,
    sourced from one real page each). The section's own top-level
    bullet/landing page is untouched unless landing_source is given.
  - append_children: same shape as expand_children's children list, but
    added alongside a section's *existing* children instead of replacing
    them -- for real content that has no target page yet at all.

Only "en" is populated -- de/it/es need their own PAGE_MAP once their
.odt structures are reconciled (see sync.py's module docstring).
"""
from __future__ import annotations

PAGE_MAP: dict[str, dict] = {
    "en": {
        "swap": {
            "index.md": "index.md",

            "system-setup/index.md": "system-setup/overview.md",
            "system-setup/file-manager.md": "system-setup/file-manager.md",
            "system-setup/alerts.md": "system-setup/alerts.md",
            "system-setup/date-and-time.md": "system-setup/date-and-time.md",
            "system-setup/general.md": "system-setup/general.md",
            "system-setup/battery.md": "system-setup/battery.md",
            "system-setup/hardware.md": "system-setup/hardware.md",
            # Real chapter titles differ from target's; confirmed by the
            # real pages' own asset filenames (system-icon-sticks.png,
            # system-icon-devices.png), which encode Ethos's actual UI
            # menu names independent of the chapter's own prose title.
            "system-setup/controls.md": "system-setup/sticks.md",
            "system-setup/devices.md": "system-setup/device-config.md",
            "system-setup/information.md": "system-setup/info.md",

            "model-setup/index.md": "model-setup/overview.md",
            "model-setup/model-select.md": "model-setup/model-select.md",
            "model-setup/model-edit.md": "model-setup/edit-model.md",
            "model-setup/flight-modes.md": "model-setup/flight-modes.md",
            "model-setup/mixes.md": "model-setup/mixes.md",
            # Confirmed via the real page's own asset filename:
            # model-icon-outputs.png, even though the real chapter's own
            # prose title is "Channels".
            "model-setup/outputs.md": "model-setup/channels.md",
            "model-setup/timers.md": "model-setup/timers.md",
            "model-setup/trims.md": "model-setup/trims.md",
            "model-setup/rf-system.md": "model-setup/rf-system.md",
            "model-setup/curves.md": "model-setup/curves.md",
            "model-setup/logical-switches.md": "model-setup/logic-switches.md",
            "model-setup/special-functions.md": "model-setup/special-functions.md",
            "model-setup/variables.md": "model-setup/variables-vars.md",
            "model-setup/trainer.md": "model-setup/trainer.md",
            "model-setup/telemetry.md": "model-setup/telemetry.md",
            "model-setup/checklist.md": "model-setup/checklist.md",
            "model-setup/glasses.md": "model-setup/glasses.md",
            "model-setup/lua-scripts.md": "model-setup/lua.md",

            "lua-scripts/index.md": "lua-scripts/index.md",
            "lua-scripts/lua-interpreter.md": "lua-scripts/ethos-lua-interpreter.md",
            "lua-scripts/ethos-lua-documentation.md": "lua-scripts/ethos-lua-documentation.md",
            "lua-scripts/example-script-locations.md": "lua-scripts/ethos-lua-example-script-files-location.md",
            "lua-scripts/configuration-limits.md": "lua-scripts/lua-scripting-configuration-limits.md",
            "lua-scripts/basic-widget-layout.md": "lua-scripts/structure-of-a-lua-widget.md",
            "lua-scripts/alternative-display-themes.md": "lua-scripts/alternative-lua-display-themes.md",

            "tutorials/index.md": "programming-tutorials/index.md",
            "tutorials/initial-radio-setup.md": "programming-tutorials/initial-radio-setup-example.md",
            "tutorials/basic-fixed-wing.md": "programming-tutorials/basic-fixed-wing-airplane-example.md",
            "tutorials/basic-flying-wing.md": "programming-tutorials/basic-flying-wing-elevon-airplane-example.md",
            "tutorials/basic-flybarless-heli.md": "programming-tutorials/basic-flybarless-helicopter-example.md",
            # Real's other 2 Programming Tutorials children ('How To'
            # section, Emphasis) are confirmed source/conversion
            # artifacts, not real manual content -- deliberately not
            # mapped anywhere.

            "ethos-suite/index.md": "frsky-suite/index.md",
            "ethos-suite/migration.md": "frsky-suite/procedure-for-migrating-to-frsky-suite.md",
            "ethos-suite/operation.md": "frsky-suite/operation.md",
            "ethos-suite/web-simulator.md": "ethos-web-simulator/index.md",

            # The one How-To Guides page with a real source (see plan).
            # The other 11 How-To pages and all of Reference have no real
            # .odt chapter behind them and are deliberately left alone --
            # not listed here at all, same as Contributing.
            "how-to/converting-1.6-models.md":
                "appendix-a-conversion-of-ethos-models-from-1-6-x-to-26-1-x/index.md",
        },

        # Real chapter-intro + its own sub-pages, concatenated into one
        # target page with demoted (##, ###, ...) headings below the
        # first page's own H1 -- see module docstring for why (target's
        # "Getting Started" nav is intentionally 2-level, real's isn't).
        "concat": {
            "getting-started/main-views.md": [
                "main-views/index.md",
                "main-views/initial-main-view.md",
                "main-views/configured-main-view.md",
            ],
            "getting-started/user-interface-and-navigation.md": [
                "user-interface-and-navigation/index.md",
                "user-interface-and-navigation/reset-menu.md",
                "user-interface-and-navigation/lock-touchscreen.md",
                "user-interface-and-navigation/editing-controls.md",
            ],
            "getting-started/usb-connection-modes.md": [
                "usb-connection-to-pc-modes/index.md",
                "usb-connection-to-pc-modes/power-off-mode.md",
                "usb-connection-to-pc-modes/bootloader-mode.md",
                "usb-connection-to-pc-modes/power-on-mode.md",
            ],
            "getting-started/emergency-mode.md": [
                "emergency-mode/index.md",
                "emergency-mode/emergency-mode-test.md",
            ],
        },

        # Section -> its children fully replaced by real per-topic pages.
        # landing_source of None means the section's own top-level page
        # (its hand-written landing/intro) is left untouched.
        "expand_children": {
            "displays/index.md": {
                # Only used for "auto" landing_source (see radio-notes
                # below) -- a real landing_source's own content supplies
                # the page's title, so this is unused here, but kept for
                # every entry so derive_locale_page_map()/auto_landing_
                # content() never need to fall back to reading English's
                # own SUMMARY.md (which doesn't exist for other locales).
                "title": "Displays",
                "landing_source": "configure-screens/index.md",
                "children": [
                    ("Configuring the Main Screen", "displays/configuring-the-main-screen.md",
                     "configure-screens/configuring-the-main-screen.md"),
                    ("Standard Widgets", "displays/standard-widgets.md",
                     "configure-screens/standard-widgets.md"),
                    ("Adding Additional Screens", "displays/adding-additional-screens.md",
                     "configure-screens/adding-additional-screens.md"),
                    ("Managing Main Screens", "displays/managing-main-screens.md",
                     "configure-screens/managing-main-screens.md"),
                    ("Adding Custom Widgets", "displays/adding-custom-widgets.md",
                     "configure-screens/adding-custom-widgets.md"),
                ],
            },
            "radio-notes/index.md": {
                # No single real chapter covers all 8 radios' notes as one
                # intro -- auto-generate a bare heading + link list from
                # the children below instead (see auto_landing_content in
                # sync_mapped.py). Keeps this page from drifting out of
                # sync with whatever the real per-radio chapter list is.
                # Used for every locale (title stays in English -- it's
                # structural scaffolding, not translated prose, same
                # status as the bare "# Radio Layouts" landing page
                # sync.py's own REGROUP_SECTIONS mode writes).
                "title": "Radio Notes",
                "landing_source": "auto",
                "children": [
                    ("X20/X20S", "radio-notes/x20-x20s.md", "x20-x20s-layouts/index.md"),
                    ("X20 Pro", "radio-notes/x20-pro.md", "x20-pro-layout/index.md"),
                    ("X20 Pro AW", "radio-notes/x20-pro-aw.md", "x20-pro-aw-layout/index.md"),
                    ("X20R/RS", "radio-notes/x20r-rs.md", "x20r-rs-layout/index.md"),
                    ("X18/X18SE", "radio-notes/x18-x18se.md", "x18-x18se-layout/index.md"),
                    ("XE/XES/XE RS", "radio-notes/xe-xes-xe-rs.md", "xe-xes-xe-rs-layouts/index.md"),
                    ("X14/X14RS", "radio-notes/x14-x14rs.md", "x14-x14rs/index.md"),
                    ("TWIN XLite", "radio-notes/twin-xlite.md", "twin-xlite/index.md"),
                ],
            },
        },

        # Real content with no existing target page at all -- added as new
        # children alongside a section's existing ones (not replacing).
        "append_children": {
            "model-setup/index.md": [
                ("Rearranging the Model Menu", "model-setup/rearranging-the-model-menu.md",
                 "model-setup/rearranging-the-model-menu.md"),
            ],
            "ethos-suite/index.md": [
                ("Command Line Operation", "ethos-suite/command-line-operation.md",
                 "frsky-suite/command-line-operation.md"),
            ],
        },
    },
}

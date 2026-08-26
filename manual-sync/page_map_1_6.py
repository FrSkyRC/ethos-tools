"""PAGE_MAP for ethos-manual-rework's `1.6` branch -- same shape/rationale
as page_map.py's own PAGE_MAP (built for the `26.1`/`main` branch), just a
separate config since 1.6's target nav and 1.6's real .odt structure are
both genuinely different documents from 26.1's (fewer supported radios,
a smaller feature set, different chapter granularity in places). See
RUNBOOK.md for the general process this follows.

Real vs. target structure differences worth noting (decisions already
made, not left for sync_mapped.py to guess at):
  - Getting Started's "Main Views" real chapter has 3 children (top bar,
    bottom bar, widgets area) here, not 26.1's 2 (initial/configured main
    view) -- still a single concat target either way.
  - The real manual only has 5 radio-layout chapters (no XE/XES/XE RS,
    X14/X14RS, TWIN XLite -- 1.6-era firmware doesn't support those
    radios), each its own separate chapter (no combined "X20 Pro / X20
    Pro AW" the way target's old hand-written page grouped them) -- same
    "expand target's sparse Radio Notes into real per-radio pages"
    treatment as 26.1's Radio Layouts, just 5 children instead of 8.
  - Real "Configure Screens" has one extra child ("Configuring the main
    screen") beyond target's 2 -- appended rather than dropped, same as
    "Rearranging the Model Menu"/"Command Line Operation" were on 26.1.
  - Target's Tutorials wants an "Initial Radio Setup" page that has no
    real 1.6 counterpart at all (the real manual only covers 3 example
    builds + 2 known conversion artifacts) -- left unmapped, same
    "report unresolved rather than guess" standard as every other gap.
  - Real "Ethos Suite" is already named that in the .odt itself (not
    "FrSky Suite" the way 26.1's was) -- no rename needed for the
    section landing page.
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
            "system-setup/controls.md": "system-setup/sticks.md",
            "system-setup/devices.md": "system-setup/device-config.md",
            "system-setup/information.md": "system-setup/info.md",

            "model-setup/index.md": "model-setup/overview.md",
            "model-setup/model-select.md": "model-setup/model-select.md",
            "model-setup/model-edit.md": "model-setup/edit-model.md",
            "model-setup/flight-modes.md": "model-setup/flight-modes.md",
            "model-setup/mixes.md": "model-setup/mixes.md",
            "model-setup/outputs.md": "model-setup/outputs.md",
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
            "model-setup/lua-scripts.md": "model-setup/lua.md",

            "lua-scripts/index.md": "lua-scripts/index.md",
            "lua-scripts/lua-interpreter.md": "lua-scripts/ethos-lua-interpreter.md",
            "lua-scripts/ethos-lua-documentation.md": "lua-scripts/ethos-lua-documentation.md",
            "lua-scripts/example-script-locations.md": "lua-scripts/ethos-lua-example-script-files-location.md",
            "lua-scripts/configuration-limits.md": "lua-scripts/lua-scripting-configuration-limits.md",
            "lua-scripts/basic-widget-layout.md": "lua-scripts/basic-layout-of-a-lua-widget.md",

            "tutorials/index.md": "programming-tutorials/index.md",
            "tutorials/basic-fixed-wing.md": "programming-tutorials/basic-fixed-wing-airplane-example.md",
            "tutorials/basic-flying-wing.md": "programming-tutorials/basic-flying-wing-elevon-airplane-example.md",
            "tutorials/basic-flybarless-heli.md": "programming-tutorials/basic-flybarless-helicopter-example.md",
            # tutorials/initial-radio-setup.md deliberately not mapped --
            # no real chapter for it at all in the 1.6 manual (see module
            # docstring). Real's other 2 Programming Tutorials children
            # ('How To' section, Emphasis) are the same confirmed
            # source/conversion artifacts as on 26.1 -- not mapped either.

            "ethos-suite/index.md": "ethos-suite/index.md",
            "ethos-suite/migration.md": "ethos-suite/procedure-for-migrating-to-ethos-suite.md",
            "ethos-suite/operation.md": "ethos-suite/operation.md",
        },

        "concat": {
            "getting-started/main-views.md": [
                "main-views/index.md",
                "main-views/the-top-bar.md",
                "main-views/the-bottom-bar.md",
                "main-views/the-widgets-area.md",
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

        "expand_children": {
            "displays/index.md": {
                "title": "Displays",
                "landing_source": "configure-screens/index.md",
                "children": [
                    ("Additional Displays", "displays/additional-displays.md",
                     "configure-screens/adding-additional-screens.md"),
                    ("Custom Widgets", "displays/custom-widgets.md",
                     "configure-screens/adding-custom-widgets.md"),
                ],
            },
            "radio-notes/index.md": {
                # Only 5 real radio-layout chapters here (vs 26.1's 8) --
                # this era's firmware doesn't support XE/XES/XE RS,
                # X14/X14RS, or TWIN XLite. Same auto-generated landing
                # page treatment as 26.1's own Radio Notes expansion.
                "title": "Radio Notes",
                "landing_source": "auto",
                "children": [
                    ("X20/X20S", "radio-notes/x20-x20s.md", "x20-x20s-layouts/index.md"),
                    ("X20 Pro", "radio-notes/x20-pro.md", "x20-pro-layout/index.md"),
                    ("X20 Pro AW", "radio-notes/x20-pro-aw.md", "x20-pro-aw-layout/index.md"),
                    ("X20R/RS", "radio-notes/x20r-rs.md", "x20r-rs-layout/index.md"),
                    ("X18/X18SE", "radio-notes/x18-x18se.md", "x18-x18se-layout/index.md"),
                ],
            },
        },

        "append_children": {
            "displays/index.md": [
                ("Configuring the Main Screen", "displays/configuring-the-main-screen.md",
                 "configure-screens/configuring-the-main-screen.md"),
            ],
            "ethos-suite/index.md": [
                ("Command Line Operation", "ethos-suite/command-line-operation.md",
                 "ethos-suite/command-line-operation.md"),
            ],
        },
    },
}

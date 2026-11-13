# Ethos tests

Tests that drive Ethos as a user does (keys, touches, rotary encoder), on:

- the WASM simulators: any radio, release or debug, any country protocol, local builds (`build/<radio>/simulator/<RADIO>_<FCC|LBT|FLEX>.js`) or the simulators of the releases on GitHub, each started on a fresh storage
- a radio on a serial port (its USB mode set to Serial), through [radio_remote.py](../radio_remote.md)

Both go through the same command server (`run_wasm.js --serve` / `radio_remote.py --serve`), which `run_tests.py` starts and stops.

```sh
python tests/run_tests.py --simulator "d:/Projects/ethos/build/*/simulator/*.js"   # every simulator built
python tests/run_tests.py --simulator X20RS --simulator "X18*"                      # simulators of the latest release
python tests/run_tests.py --simulator X20RS-FLEX --release 26.2.0-RC10              # of a given release
python tests/run_tests.py --radio usb                                               # or COM3; accept the connection on the radio screen
python tests/run_tests.py --port 8766 xact_phyid                                    # a server already running
```

- `TEST...`: the tests to run (`xact_phyid` for `test_xact_phyid.py`), all of them by default
- `--simulator`: a `.js` file (wildcards allowed: quote them), or a radio of a release: `X20RS` (its FCC simulator), `X20RS-FLEX`, `X18-LBT`, `X20*` (the FCC ones), `X20RS-*` (all its countries). Several times allowed
- `--release TAG`: the release of these radios, the latest one by default (release candidates included). `run_wasm.js --radio` downloads its simulators once, in the cache of the MCP server (`--cache DIR` or `ETHOS_CACHE_DIR` for another one)
- `--option name=value`: an option of the tests (see each test)
- `--no-skip`: runs the tests also on the targets they skip (a test needing a device on a simulator)
- `--root-directory DIR`: simulator storage to use instead of a fresh one
- `--out DIR`: screenshots (one folder per target and test) and server logs, `test-results` by default. On a failure, `NN-failure.png` (the screen then) and `NN-failure-last-touch.png` (the screen of the last touch, a red cross where it touched, a green frame around the text found)

The dependencies (OCR, pyserial for `--radio`, which runs `radio_remote.py` with the same Python): `python -m pip install -r tests/requirements.txt`.

The screen elements are found by their text, read by OCR (RapidOCR) on a screenshot: no coordinates, the tests run on any display size. The texts are the English ones (a fresh simulator starts in English, set the radio language to English).

## Writing a test

A `test_<name>.py` module with a class derived from `EthosBaseTest` ([ethos_test.py](ethos_test.py)):

```python
from ethos_test import EthosBaseTest

class MyTest(EthosBaseTest):
    TITLE = 'What it checks'

    def is_available(self):
        """needs a radio"""             # optional (True by default), the docstring is the reason shown when it is not
        return self.kind == 'radio'

    def steps(self):
        return [self.reload_all, self.close_checklist_warning, self.check_something]

    def check_something(self):
        """Check something"""           # the step title
        self.open_system_menu('Hardware')     # found on the screen by its text
        self.wait_log(r'some line', timeout=5)
```

`setup()` (optional) runs before the steps. `teardown()` (optional) runs after them, even when a step failed: it puts the radio back in its state (a function switch off, a setting back...). A failure of either fails the test.

A step is a method, titled by the first line of its docstring, or a `(title, function)` tuple. It fails with `self.fail(message)`, or skips the test with `self.skip(reason)`.

`EthosBaseTest` gives:

- the commands of the remote ([ethos_remote.py](ethos_remote.py)): `self.press`, `self.touch`, `self.turn_rotary_encoder`, `self.reload`, `self.screenshot`, `self.wait`... An input sent while the screen is off only turns it back on, as for the user: it is sent again
- the state of the radio: `self.channels_outputs()` (in %), and as it sends them when they change, `self.controls()` (switches, trims, function switches, version), `self.switches()` (`{"SA": -1, ...}`: -1 up, 0 middle, 1 down), `self.trims()` (in %), `self.function_switches()` (`{"FS1": True, ...}`); `self.wait_controls(condition, timeout)` waits until `condition(controls)` is true (e.g. `lambda c: c['switches']['SA'] == 0`); `self.reset_function_switches()`
- `kind` (`simulator` or `radio`), `info` (`getInformation`: board, version, rfProtocol...), `display` (width, height of the main display), `option(name, default)`
- the screen texts (OCR): `read_screen()` (the texts, with their position), `find_text(text, timeout)`, `has_text(text, timeout)`, `touch_text(text)`, all three with an optional `area=(x, y, w, h)` which limits the search to a part of the screen, in pixels or in fractions of the display (`area=(0.5, 0.0, 0.5, 1.0)`: the right half), `value_of(label)` (the text on the right of a label: the value of a field). The title bar is left out of the searches (touching it closes the page), except with `in_title=True`
- the log: `watch_log()` (the lines from now on), `wait_log(regex, timeout)` (the match of the next line found), `collect_log(regex, activity, quiet)` (the matches until the log is quiet), `log_contains(regex)`. A Lua error in the log fails the test
- `pages()`: the pages of the screen, from the dots under the title bar: (count, current page index from 0)
- `touch_button(right_of=label, area=..., empty=...)`: touches a button found by its frame, for the icon buttons ("+", "⋮"...): the nearest one on the right of a label, or the first one of an area, `empty=True` for the buttons without text
- `scroll_to_end()`: scrolls the page down to its end (a long list); `touch_button(..., last=True)` touches the last button of the area
- `scroll_to_text(text)`: scrolls the page (swipes, the rotary encoder without touch screen) until text is shown
- `set_toggle(label, on)` / `toggle_is_on(label)`: the OFF / ON switch on the line of label (its ON is orange when on)
- `choose_in_list(open_text, choice)`: opens a list by touching open_text and touches choice in it
- `select_choice_of(label, choice)`: selects choice (its text) in the choice on the line of label, nothing done when it is already shown: the list is opened, scrolled with the rotary encoder until choice is shown, choice is touched, then the field must show it; `select_choice(x, y, steps)` moves the selection of the choice at given coordinates
- the common steps: `install_script(local_dir, radio_dir)`, `reload_all`, `close_checklist_warning` (when there is one), `go_home` (the home screen: long RTN), `open_system_menu(entry)` / `open_model_menu(entry)` (an entry of the System / Model menu, looked for on its pages, opened from the home screen unless the menu is already shown), `open_menu(key, entry)`, `open_device_config(category, device)` (System / Device config / category / device), `open_entry(text, expect)` (a line of a list, until the page opened is shown: its title is text, or expect is shown), `back` (RTN, pressed again while the title of the page stays the same: a first RTN can only take the focus away from a button), `title()` (the title of the page)

Without touch screen (the radio answers `unsupported` to a touch), the element is reached with the rotary encoder: it is turned (forward, then backward) until the element which has the focus, the orange one, shows the text, then ENTER is pressed (`enter_text_element(text)`, `focused_texts()`).

The traces show what is touched: `touch "Device config" at 650,288`, `touch choice of "Connection Type" line at 688,94`.

## Tests

| Test | Checks | Targets |
|---|---|---|
| `firmware_info` | System / Info: the Firmware line (radio, version, country protocol, git revision) is the one the target gives (`getInformation`) | all |
| `xact_phyid` | XAct: copy the script, reload everything, System / Device config / Servos / XAct on S.Port, change the Physical ID, save to flash, close (no confirmation asked), reopen: the servo answers on its new ID; put the ID back, save, close, reopen: all the parameters are those of the first opening | radio with an XAct servo on S.Port, keys and touch screen |

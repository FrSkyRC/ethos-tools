"""Base class of the Ethos tests, run by run_tests.py on the simulators and the radios.

    class MyTest(EthosBaseTest):
        TITLE = 'What it checks'

        def is_available(self):
            \"""needs a radio\"""             # optional, the docstring is the reason shown when it is not
            return self.kind == 'radio'

        def setup(self):                   # optional, before the steps
            ...

        def steps(self):
            return [self.reload_all, self.close_checklist_warning, self.check_something]

        def teardown(self):                # optional, after the steps, even after a failure
            ...

        def check_something(self):
            \"""Check something\"""          # the step title
            self.open_system_menu('Hardware')    # found on the screen by its text (OCR)
            self.wait_log(r'some line', timeout=5)

A step is a method (titled by the first line of its docstring) or a (title, function)
tuple. It fails with self.fail(), or skips the test with self.skip().

The screen elements are found by their text, read by OCR on a screenshot (see
ethos_ocr.py): no coordinates, the tests run on any display size. The texts are the
English ones.
"""

import hashlib
import os
import re
import shutil
import time

import ethos_ocr
from ethos_remote import TestFailure, TestSkipped


class EthosBaseTest:
    TITLE = ''

    def __init__(self, target, remote, info, options):
        self.target = target
        self.remote = remote
        self.info = info          # getInformation: board, version, rfProtocol...
        self.options = options    # run_tests.py --option name=value
        main = (info.get('displays') or {}).get('MAIN') or {}
        self.display = (main.get('width'), main.get('height'))
        self.log = None
        self.texts = []           # the texts of the last screen read
        self._texts_of = None     # the digest of the screenshot self.texts were read on
        self._title_texts = ([], None)  # the same for the title bar only
        self.last_touch = None    # (screenshot before the touch, x, y, label, text found), marked on a failure
        self.no_touch = False     # no touch screen: the elements are reached with the rotary encoder

    def __getattr__(self, name):
        # The commands of the remote: self.press('SYS'), self.touch(x, y), self.screenshot('name')...
        return getattr(self.remote, name)

    @property
    def kind(self):
        """'simulator' or 'radio'"""
        return self.target.kind

    def is_available(self):
        """True when the test can run on this target (a simulator or a radio, its board,
        version, rfProtocol, display...). The first line of the docstring of an override
        is the reason shown when it can't."""
        return True

    def skip_reason(self):
        """Why the test is skipped on this target, or None."""
        if not self.is_available():
            doc = (type(self).is_available.__doc__ or '').strip()
            return doc.splitlines()[0] if doc else f'not available on this {self.kind}'
        return None

    def steps(self):
        raise NotImplementedError

    def setup(self):
        """Run before the steps: the state the test needs (a failure fails the test)."""

    def teardown(self):
        """Run after the steps, even when one of them failed: the radio put back in its state
        (a failure of it fails the test)."""

    # ---------- Helpers ----------

    def option(self, name, default=None):
        return self.options.get(name, default)

    @staticmethod
    def fail(message):
        raise TestFailure(message)

    @staticmethod
    def skip(reason):
        raise TestSkipped(reason)

    # ---------- Log ----------

    def watch_log(self):
        """The log lines from now on, searched by wait_log()."""
        self.log = self.remote.log_watcher()
        return self.log

    def wait_log(self, pattern, timeout=10):
        """The match of the next log line matching pattern (a regex)."""
        return (self.log or self.watch_log()).wait_for(pattern, timeout)

    def collect_log(self, pattern, activity=None, quiet=3, timeout=30):
        """The matches of pattern in the next log lines, until no line matches activity
        (pattern by default) for quiet seconds."""
        return (self.log or self.watch_log()).collect(pattern, activity, quiet, timeout)

    def log_contains(self, pattern):
        return bool((self.log or self.watch_log()).find_all(pattern))

    # ---------- Screen texts (OCR) ----------

    def _capture(self):
        """A screenshot of the screen now, to analyse (overwritten at each capture). The
        backlight is turned on first: a dimmed screen can't be read (a long wait, a bind...)."""
        os.makedirs(self.remote.shots_dir, exist_ok=True)
        path = os.path.join(self.remote.shots_dir, 'ocr.png')
        self.remote.reset_backlight_timer()
        self.remote.call('screenshot', out=path)
        return path

    def read_screen(self):
        """The texts on the screen now (ethos_ocr.ScreenText: text, x, y, w, h, center). The
        OCR (0.4 to 1 s) is done again only when the screen changed."""
        path = self._capture()
        with open(path, 'rb') as f:
            digest = hashlib.sha1(f.read()).digest()
        if digest != self._texts_of:
            self.texts = ethos_ocr.read(path)
            self._texts_of = digest
        return self.texts

    def pages(self):
        """The pages of the screen, from the dots under the title bar: (count, current page
        index from 0), (1, 0) without them."""
        # From a tenth of the height: the title bar is shorter than an eighth of the screen on the small
        # displays (dots at y = 47 on 640x360, 76 on 800x480)
        return ethos_ocr.page_dots(self._capture(), (self.display[1] or 480) // 10)

    def _title_bottom(self):
        return (self.display[1] or 480) // 8

    def _area(self, area):
        """area (x, y, w, h) in pixels, or in fractions of the display when all its values
        are between 0 and 1 as floats: (0.5, 0, 0.5, 1) is the right half of the screen."""
        if area is None:
            return None
        if all(isinstance(v, float) and 0 <= v <= 1 for v in area):
            width, height = self.display[0] or 800, self.display[1] or 480
            return (area[0] * width, area[1] * height, area[2] * width, area[3] * height)
        return area

    def find_text(self, text, timeout=5, in_title=False, required=True, area=None):
        """The first element showing text (OCR errors aside), waited for up to timeout
        seconds. The title bar is left out: touching the title closes the page. With area
        (x, y, w, h, see _area()), only the texts centered in it."""
        zone = self._area(area)

        def inside(found):
            cx, cy = found.center
            if zone is not None:
                x, y, w, h = zone
                if not (x <= cx < x + w and y <= cy < y + h):
                    return False
            return in_title or cy > self._title_bottom()

        deadline = time.time() + timeout
        while True:
            for found in self.read_screen():
                if inside(found) and ethos_ocr.matches(found.text, text):
                    return found
            if time.time() > deadline:
                if required:
                    where = f' in {tuple(round(v) for v in zone)}' if zone is not None else ' on the screen'
                    seen = [t.text for t in self.texts if inside(t)]
                    self.fail(f'No "{text}"{where}, only: {", ".join(seen) or "nothing"}')
                return None
            time.sleep(0.2)

    def has_text(self, text, timeout=0, in_title=False, area=None):
        return self.find_text(text, timeout, in_title, required=False, area=area) is not None

    def read_title_bar(self):
        """The texts of the title bar now (its OCR alone is much faster than the screen one)."""
        path = self._capture()
        band = (0, 0, self.display[0] or 800, self._title_bottom())
        image = ethos_ocr.load(path)[:band[3]]
        digest = hashlib.sha1(image.tobytes()).digest()
        if digest != self._title_texts[1]:
            self._title_texts = (ethos_ocr.read(path, band), digest)
        return self._title_texts[0]

    def title(self):
        """The title of the page (the leftmost text of the title bar, not the ETHOS logo), or None."""
        texts = [t for t in self.read_title_bar() if not ethos_ocr.matches(t.text, 'ETHOS')]
        return min(texts, key=lambda t: t.x).text if texts else None

    def has_title(self, text, timeout=0):
        """True when the title bar shows text (the page opened)."""
        deadline = time.time() + timeout
        while True:
            if any(ethos_ocr.matches(t.text, text) for t in self.read_title_bar()):
                return True
            if time.time() > deadline:
                return False
            time.sleep(0.2)

    def title_changed(self, before, timeout=1):
        """True when the title is no more before (the page changed) within timeout seconds."""
        deadline = time.time() + timeout
        while True:
            if self.title() != before:
                return True
            if time.time() > deadline:
                return False
            time.sleep(0.2)

    def touch_text(self, text, ms=100, timeout=5, area=None, focus_steps=0):
        """Touches the element showing text (with area, the one in this part of the screen,
        see find_text()). Without touch screen, it is reached with the rotary encoder and
        ENTER is pressed (see enter_text_element()). With focus_steps, an element the OCR of
        the whole screen doesn't read (a big "+" alone...) is looked for that way too: the OCR
        of the element which has the focus alone reads it."""
        found = self.find_text(text, timeout, area=area, required=not focus_steps)
        if found is None:
            self.trace(f'"{text}" not read on the screen: looked for with the focus (rotary encoder)')
            self.enter_text_element(text, focus_steps)
            return None
        if self.no_touch:
            self.enter_text_element(text)
            return found
        self._remember_touch(*found.center, text, found)
        self._touch_or_enter(found.center, text, label=text)
        return found

    def touch_button(self, right_of=None, area=None, empty=False, ms=100, last=False):
        """Touches a button found by its frame (an icon button: "+", "⋮"...): the nearest one on
        the right of the label right_of, or the first one (top-left) in area (see find_text()).
        With empty, only the buttons without text (the icons). With last, the last one of the area
        (the bottom-most, then the right-most) instead of the first."""
        path = self._capture()
        boxes = ethos_ocr.buttons(path, self._title_bottom())
        texts = self.read_screen()
        if empty:
            boxes = [b for b in boxes if not any(b[0] <= t.center[0] < b[0] + b[2] and b[1] <= t.center[1] < b[1] + b[3]
                                                 and re.search('[0-9A-Za-z]', t.text) for t in texts)]
        what = 'button'
        if right_of:
            label = self.find_text(right_of)
            cy = label.center[1]
            boxes = [b for b in boxes if b[0] >= label.x + label.w and b[1] <= cy < b[1] + b[3]]
            what = f'button right of "{right_of}"'
        zone = self._area(area)
        if zone is not None:
            x, y, w, h = zone
            boxes = [b for b in boxes if x <= b[0] + b[2] / 2 < x + w and y <= b[1] + b[3] / 2 < y + h]
            what = f'{"icon " if empty else ""}button in {tuple(round(v) for v in zone)}'
        if not boxes:
            self.fail(f'No {what}')
        if right_of:
            box = min(boxes, key=lambda b: (b[0], b[1]))
        else:
            box = max(boxes, key=lambda b: (b[1], b[0])) if last else boxes[0]
        center = (box[0] + box[2] // 2, box[1] + box[3] // 2)
        self._remember_touch(*center, what)
        self.touch(*center, ms=ms, what=what)
        return box

    def _touch_or_enter(self, point, text, ms=100, **trace):
        """Touches point, or, the first time the radio says it has no touch screen, reaches the
        element showing text with the rotary encoder."""
        try:
            self.touch(*point, ms=ms, **trace)
        except TestFailure as e:
            if 'unsupported' not in str(e):
                raise
            self.no_touch = True
            self.trace('no touch screen: the rotary encoder and ENTER instead')
            self.enter_text_element(text)

    def focused_texts(self):
        """The texts of the element which has the focus (orange): its label, the text of an
        icon..."""
        box = ethos_ocr.focused_box(self._capture(), self._title_bottom())
        if box is None:
            return []
        return [t.text for t in ethos_ocr.read(os.path.join(self.remote.shots_dir, 'ocr.png'), box)]

    def enter_text_element(self, text, max_steps=30):
        """Moves the focus with the rotary encoder (forward, then backward) until the element
        showing text has it, and presses ENTER."""
        for direction, steps in ((1, max_steps), (-1, 2 * max_steps)):
            for _ in range(steps):
                if any(ethos_ocr.matches(focused, text) for focused in self.focused_texts()):
                    self.press('ENTER')
                    return
                self.turn_rotary_encoder(direction)
        self.fail(f'The focus never reaches "{text}"')

    def _remember_touch(self, x, y, label, found=None):
        """The screen the touch was decided on (the last one read), to show it on a failure."""
        screen = os.path.join(self.remote.shots_dir, 'ocr.png')
        before = os.path.join(self.remote.shots_dir, 'last-touch.png')
        if os.path.isfile(screen):
            shutil.copyfile(screen, before)
        self.last_touch = (before if os.path.isfile(screen) else None, x, y, label, found)

    def save_failure_screens(self):
        """The screen at the failure, and the one of the last touch with a cross where it
        touched (and a frame around the text found)."""
        files = [self.screenshot('failure')]
        if self.last_touch:
            before, x, y, label, found = self.last_touch
            self.remote.shot_index += 1
            out = os.path.join(self.remote.shots_dir, f'{self.remote.shot_index:02d}-failure-last-touch.png')
            files.append(ethos_ocr.mark_touch(before or files[0], out, x, y, label, found))
        return files

    def value_of(self, label, timeout=5):
        """The text shown on the right of label, on the same line (the value of a field),
        or None."""
        found = self.find_text(label, timeout)
        value = self._value_element(found)
        return value.text if value else None

    def _value_element(self, label):
        _, cy = label.center
        # not the icons of the fields, read as 目, 三...: at least a latin letter or a digit
        right = [t for t in self.texts if t.x > label.x + label.w and abs(t.center[1] - cy) < label.h
                 and re.search('[0-9A-Za-z]', t.text)]
        return min(right, key=lambda t: t.x) if right else None

    def scroll_to_end(self, max_scrolls=60):
        """Scrolls the page down to its end (swipes, or the rotary encoder without touch screen):
        until the screen doesn't change any more (a long list: the models...)."""
        width, height = self.display[0] or 800, self.display[1] or 480
        previous = None
        for _ in range(max_scrolls):
            # the screen below the title bar, as a whole: icons and cards are not read by the OCR
            image = ethos_ocr.load(self._capture())[self._title_bottom():]
            digest = hashlib.sha1(image.tobytes()).digest()
            if digest == previous:
                return
            previous = digest
            if self.no_touch:
                self.turn_rotary_encoder(10)
            else:
                self.swipe(width // 2, height * 85 // 100, width // 2, height * 3 // 10)
            self.wait(0.3)
        self.fail('The end of the page is not reached')

    def scroll_to_text(self, text, max_scrolls=8):
        """The element showing text, the page scrolled down (swipes, or the rotary encoder
        without touch screen) until it is shown, then up."""
        width, height = self.display[0] or 800, self.display[1] or 480
        for direction in (1, -1):
            previous = None
            for _ in range(max_scrolls):
                found = self.find_text(text, timeout=0, required=False)
                if found:
                    return found
                shown = [(t.text, t.y) for t in self.texts]
                if shown == previous:  # the end of the page
                    break
                previous = shown
                if self.no_touch:
                    self.turn_rotary_encoder(5 * direction)
                else:
                    top, bottom = height * 3 // 10, height * 85 // 100
                    self.swipe(width // 2, bottom if direction > 0 else top, width // 2, top if direction > 0 else bottom)
                self.wait(0.3)
        self.fail(f'No "{text}" on the page')

    def toggle_is_on(self, label):
        """The state of the OFF / ON switch on the line of label, and its ON text. The text
        of the state is the bright one, the other is dimmed: ON is white when on, and orange
        only when the switch has the focus, as OFF is when off with the focus."""
        found = self.find_text(label)
        line = [t for t in self.texts if t.x > found.x + found.w and abs(t.center[1] - found.center[1]) < found.h]
        on = next((t for t in line if t.text.strip().upper() == 'ON'), None)
        if on is None:
            self.fail(f'No OFF / ON switch on the "{label}" line')
        screen = os.path.join(self.remote.shots_dir, 'ocr.png')
        off = next((t for t in line if t.text.strip().upper() == 'OFF'), None)
        if off is None:  # the OFF text was not read: on with the focus is still told by its color
            return ethos_ocr.has_color(screen, (on.x, on.y, on.w, on.h), ethos_ocr.FOCUS_COLOR), on
        return (ethos_ocr.brightness(screen, (on.x, on.y, on.w, on.h))
                > ethos_ocr.brightness(screen, (off.x, off.y, off.w, off.h))), on

    def set_toggle(self, label, on=True):
        """Sets the OFF / ON switch on the line of label, touched only when it is not in this state."""
        state, on_text = self.toggle_is_on(label)
        if state == on:
            self.trace(f'{label}: already {"ON" if on else "OFF"}')
            return
        x = on_text.x - on_text.w  # the switch, between OFF and ON
        self._touch_or_enter((x, on_text.center[1]), label, what=f'"{label}" switch')
        deadline = time.time() + 3
        while self.toggle_is_on(label)[0] != on:
            if time.time() > deadline:
                self.fail(f'{label}: not {"ON" if on else "OFF"}')
            time.sleep(0.2)

    def choose_in_list(self, open_text, choice, label='list', max_turns=10):
        """Opens a list by touching open_text (the value of a choice, an entry of a roller...)
        and touches choice in it (the texts shown with the list only)."""
        self.find_text(open_text)
        background = {(t.text, t.x, t.y) for t in self.texts}
        self.touch_text(open_text)
        item = self._find_list_item(label, choice, background, max_turns)
        self._remember_touch(*item.center, choice, item)
        if self.no_touch:
            self.enter_text_element(choice)
        else:
            self.touch(*item.center, ms=100, label=choice)

    def select_choice(self, x, y, steps, label=None):
        """Opens the choice at x, y (label: its name, for the trace) and moves its selection by steps."""
        self.touch(x, y, ms=100, what=f'choice of "{label}" line' if label else None)
        self.turn_rotary_encoder(steps)
        self.press('ENTER')

    def select_choice_of(self, label, choice, max_turns=10):
        """Selects choice (its text) in the choice on the line of label: nothing done when it
        is already shown. The list is opened, scrolled with the rotary encoder until choice
        is shown, and choice is touched."""
        found = self.find_text(label)
        value = self._value_element(found)
        if value and ethos_ocr.matches(value.text, choice):
            self.trace(f'{label}: "{choice}" already selected')
            return
        x = value.center[0] if value else (self.display[0] or 800) * 3 // 4  # no value shown: the right half
        background = {(t.text, t.x, t.y) for t in self.texts}  # still shown around the list
        what = f'choice of "{label}" line'
        self._remember_touch(x, found.center[1], what, value or found)
        if self.no_touch:
            self.enter_text_element(value.text if value else label)
        else:
            self._touch_or_enter((x, found.center[1]), value.text if value else label, what=what)
        item = self._find_list_item(label, choice, background, max_turns)
        self._remember_touch(*item.center, choice, item)
        if self.no_touch:
            self.enter_text_element(choice)
        else:
            self.touch(*item.center, ms=100, label=choice)
        deadline = time.time() + 3
        while True:
            shown = self.value_of(label)
            if shown and ethos_ocr.matches(shown, choice):
                return
            if time.time() > deadline:
                self.fail(f'{label}: "{shown}" shown after choosing "{choice}"')
            time.sleep(0.2)

    def _find_list_item(self, label, choice, background, max_turns):
        """The item choice of the list just opened (the texts not in background), scrolled
        down then up with the rotary encoder until it is shown."""
        deadline = time.time() + 3
        while not [t for t in self.read_screen() if (t.text, t.x, t.y) not in background]:
            if time.time() > deadline:
                self.fail(f'The {label} list did not open')
            time.sleep(0.2)
        for direction in (1, -1):
            previous = None
            for _ in range(max_turns):
                items = [t for t in self.read_screen() if (t.text, t.x, t.y) not in background]
                for item in items:
                    if ethos_ocr.matches(item.text, choice):
                        return item
                shown = [(t.text, t.y) for t in items]
                if shown == previous:  # the end of the list
                    break
                previous = shown
                self.turn_rotary_encoder(4 * direction)
                self.wait(0.3)
        self.fail(f'No "{choice}" in the {label} list')

    # ---------- Common steps ----------

    def install_script(self, local_dir, radio_dir, exclude=()):
        """Copies a local script folder to the radio, its files read back."""
        local_dir = os.path.abspath(local_dir)
        if not os.path.isfile(os.path.join(local_dir, 'main.lua')):
            self.fail(f'No script in {local_dir}')
        self.remote.copy_directory(local_dir, radio_dir, exclude)

    def reload_all(self):
        """Reload everything: home screen"""
        self.reset_backlight_timer()
        self.reload('ALL')
        self.wait(1)
        self.screenshot('home')

    def close_checklist_warning(self):
        """Close the checklist warning"""
        if self.has_text('Checklist warning', timeout=2, in_title=True):
            self.touch_text('OK')
        else:
            self.trace('no checklist warning')
        self.screenshot('home-no-warning')

    def go_home(self, presses=3):
        """Home screen (long RTN)

        A long RTN closes all the pages opened from the home screen. It is pressed again
        while the title changes: on the home screen, it changes nothing."""
        for _ in range(presses):
            before = self.title()
            self.press('RTN', ms=600)  # from 540 ms a long press: the page is closed before it is released
            if not self.title_changed(before, timeout=0.5):
                return
        self.fail('The home screen is not reached')

    def open_menu(self, key, entry, title=None):
        """Opens the menu of key (SYS, MDL...) and its entry, looked for on its pages. With
        title (the title of the menu), nothing is done when the menu is already shown, else
        the key is pressed from the home screen (on some pages, as Info, it does nothing)."""
        if title and self.has_title(title):
            self.trace(f'already in the {title} menu')
        else:
            self.go_home()
            self.press(key)
            if title and not self.has_title(title, timeout=3):
                self.fail(f'No {title} menu')
        count, current = self.pages()
        self.trace(f'{count} page(s), on the page {current + 1}')
        for _ in range(count):
            if self.has_text(entry):
                self.touch_text(entry)
                return
            self.press('PAGE')  # the next page, back to the first one after the last one
            self.wait(0.3)
        self.fail(f'No "{entry}" on the {count} page(s) of the {key} menu')

    def open_system_menu(self, entry):
        """System menu entry"""
        self.open_menu('SYS', entry, 'System')

    def open_model_menu(self, entry):
        """Model menu entry"""
        self.open_menu('MDL', entry, 'Model')

    def open_device_config(self, category, device):
        """System / Device config / category / device"""
        self.open_system_menu('Device config')
        self.touch_text(category)
        self.open_entry(device)

    def open_entry(self, text, expect=None):
        """Opens the line text of a list, until the page opened is shown: its title is
        text, or expect (one of its texts) is shown. A first touch can select the line only."""
        def opened(timeout):
            return self.has_text(expect, timeout) if expect else self.has_title(text, timeout)

        self.touch_text(text)
        if opened(3):
            return
        if self.has_text(text):  # still on the list
            self.touch_text(text)
        if not opened(5):
            self.fail(f'"{text}" did not open')

    def back(self, presses=3):
        """Close the page (RTN)

        RTN is pressed again while the title stays the same (a first RTN can only take the
        focus away from a button). False when the page stays open."""
        title = self.title()
        for _ in range(presses):
            self.press('RTN')
            if not title or self.title_changed(title):
                return True
            self.trace(f'still on "{title}"')
        return False

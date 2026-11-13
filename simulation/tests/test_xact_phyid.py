"""XAct configuration script: a Physical ID change must be kept, and the servo found
again on its new ID. The script logs (print()) are used to follow the S.Port exchanges.

Options (run_tests.py --option):
  xact_dir=DIR   local XAct script, default the ETHOS-Feedback-Community one next to ethos-tools
  keep=1         don't put the Physical ID back
"""

import os
import re
import time

from ethos_test import EthosBaseTest

XACT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'ethos', 'src',
                        'thirdparty', 'ETHOS-Feedback-Community', 'lua', 'frsky', 'XAct')

# Same table as config/ui.lua: the S.Port physical ID byte of each Physical ID
PHYS_ID_TABLE = [0x00, 0xA1, 0x22, 0x83, 0xE4, 0x45, 0xC6, 0x67, 0x48, 0xE9, 0x6A, 0xCB, 0xAC, 0x0D, 0x8E, 0x2F,
                 0xD0, 0x71, 0xF2, 0x53, 0x34, 0x95, 0x16, 0xB7, 0x98, 0x39, 0xBA, 0x1B, 0x7C, 0xDD, 0x5E, 0xFF]
PHYID_MAX = 0x1A

CONNECTED = r'Connected phyId: 0x\s*([0-9A-F]{2})'
RECEIVED = r'sensor:getParameter\(\): 0x\s*([0-9A-F]{8})'


class XActPhyIdTest(EthosBaseTest):
    TITLE = 'XAct: Physical ID change'

    initial = None    # the parameters read at the first opening, by page address
    found = None      # the Physical ID shown at the last opening
    original = None   # the Physical ID at the start
    current = None    # the Physical ID of the servo

    def is_available(self):
        """needs a radio with an XAct servo on its S.Port connector"""
        return self.kind == 'radio'

    def steps(self):
        return [
            self.copy_script,
            self.reload_all,
            self.close_checklist_warning,
            self.open_xact_config_and_get_physical_id,
            self.change_phyid,
            self.save_to_flash,
            self.close_xact_config,
            self.reopen_xact_config,
            self.check_new_phyid,
            self.revert_phyid,
            self.save_to_flash,
            self.close_xact_config,
            self.check_all,
        ]

    def copy_script(self):
        """Copy the XAct script"""
        self.install_script(self.option('xact_dir', XACT_DIR), '/scripts/XAct', exclude=('notes.md',))

    def open_xact_config_and_get_physical_id(self):
        """System / Device config / Servos / XAct, on the S.Port interface"""
        self.original = self.current = self.open_xact_config()

    def change_phyid(self):
        """Change the Physical ID (the other parameters are still being read)"""
        self.set_phyid(self.original + 1 if self.original < PHYID_MAX else self.original - 1)

    def save_to_flash(self):
        """Save to flash"""
        self.touch_text('Save to Flash')  # a line of the form or a floating button
        if self.wait_log(r'Save to flash: (done|cancelled)', 15).group(1) == 'cancelled':
            self.fail('The save was cancelled')
        # A toast, which closes itself (2 s): nothing to acknowledge
        if self.has_text('Save successfully!', timeout=2):
            self.trace('toast shown')
        else:
            self.trace('no toast seen (shown 2 s only)')
        self.screenshot('saved')

    def close_xact_config(self):
        """Close the XAct page, back to the System menu"""
        if self.initial is None:
            # The parameters of the first opening, the reference of check_all(): the script reads them
            # while the Physical ID is changed and saved
            self.initial = self.read_params(first_values=True)
            self.trace(f'{len(self.initial)} parameters read at the first opening')
        closed = self.back()
        if self.log_contains(r'Unsaved changes, confirm before leaving'):
            self.fail('The XAct page asks to save the changes before leaving: they were not saved')
        if not closed:
            self.fail('The XAct page did not close')
        # Up to the System menu: on Device config, the XAct line keeps the focus and its name is
        # shown again on the right, with its version
        if not self.back():
            self.fail('Device config did not close')

    def reopen_xact_config(self):
        """Reopen the XAct page"""
        self.found = self.open_xact_config()

    def check_new_phyid(self):
        """Check that the servo is on the new Physical ID"""
        if self.found != self.current:
            self.fail(f'Reopened on Physical ID {self.found:02X}, {self.current:02X} expected')

    def revert_phyid(self):
        """Put the previous Physical ID back"""
        if self.option('keep'):
            self.trace('kept (--option keep=1)')
        else:
            self.set_phyid(self.original)

    def check_all(self):
        """Open XAct again, check all its parameters"""
        self.open_xact_config()
        params = self.read_params()
        expected = {**self.initial, 0x00: self.current}
        errors = [f'{page:02X}: {params[page]} read, {value} expected' if page in params else f'{page:02X}: not read'
                  for page, value in sorted(expected.items()) if params.get(page) != value]
        errors += [f'{page:02X}: read, not at the first opening' for page in sorted(set(params) - set(expected))]
        if errors:
            self.fail('Parameters: ' + ', '.join(errors))
        self.trace(f'{len(params)} parameters as at the first opening')

    # ---------- Helpers ----------

    def open_xact_config(self):
        """Opens System / Device config / Servos / XAct, on S.Port, and returns the Physical ID as
        soon as it is shown (the other parameters are read after it), checked with the log."""
        self.watch_log()
        self.open_device_config('Servos', 'XAct')
        self.wait_log(r'Enter config page')
        # Without an RF module able to reach the servo, the script selects S.Port itself
        self.select_choice_of('Connection Type', 'S.Port interface')
        if self.wait_log(r'S\.Port interface is activated|Failed to activate', 5).group(0).startswith('Failed'):
            self.fail('The S.Port interface could not be opened')
        value = self.wait_shown_phyid(timeout=30)
        # The log: the servo sent this value, on the S.Port ID the script is connected to
        phys_id = int(self.wait_log(CONNECTED, 5).group(1), 16)
        sent = self.wait_phyid_value()
        if sent != value:
            self.fail(f'Physical ID {value:02X} shown, the servo sent {sent:02X}')
        if PHYS_ID_TABLE[sent] != phys_id:
            self.fail(f'Connected on 0x{phys_id:02X}, the servo says it is on {sent:02X}')
        self.trace(f'servo on Physical ID {value:02X} (S.Port 0x{phys_id:02X})')
        self.screenshot('xact')
        return value

    def wait_shown_phyid(self, timeout):
        """The value on the right of the "Physical ID" label, once it is read ("---" before)."""
        deadline = time.time() + timeout
        while True:
            shown = (self.value_of('Physical ID') or '').upper().replace('O', '0')  # the OCR can read 0 as O
            if re.fullmatch(r'[0-9A-F]{2}', shown):
                return int(shown, 16)
            if time.time() > deadline:
                self.fail(f'No Physical ID shown after {timeout} s ({shown or "nothing"})')
            time.sleep(0.2)

    def read_params(self, first_values=False):
        """The parameters read from the servo since the page was opened (raw values by page
        address), once they are all read or skipped after their retries: no more request.
        The first value of each one, or the last one. Only the pages requested by the script: the
        servo also answers the save commands (0x15, 0x30) of Save to Flash."""
        self.collect_log(RECEIVED, activity=r'sensor:(request|get)Parameter|Request timeout', timeout=60)
        requested = {int(m.group(1), 16) for m in self.log.find_all(r'sensor:requestParameter\(0x([0-9A-F]{2})\)')}
        params = {}
        for match in self.log.find_all(RECEIVED):
            received = int(match.group(1), 16)
            page, value = received & 0xFF, received >> 8
            if page in requested and not (first_values and page in params):
                params[page] = value
        return params

    def wait_phyid_value(self):
        """The Physical ID (page address 0x00) sent by the servo."""
        while True:
            received = int(self.wait_log(RECEIVED, 15).group(1), 16)
            if received & 0xFF == 0x00:
                return received >> 8

    def set_phyid(self, value):
        """Moves the Physical ID choice to value, checks that it is written and read back on the new ID."""
        self.select_choice_of('Physical ID', f'{value:02X}')
        written = int(self.wait_log(r'sensor:writeParameter\(0x00\): ([0-9A-F]+)', 5).group(1), 16)
        if written != value:
            self.fail(f'Physical ID {written:02X} written, {value:02X} expected')
        phys_id = int(self.wait_log(CONNECTED, 5).group(1), 16)
        if phys_id != PHYS_ID_TABLE[value]:
            self.fail(f'The script follows the servo on 0x{phys_id:02X}, 0x{PHYS_ID_TABLE[value]:02X} expected')
        if self.wait_phyid_value() != value:
            self.fail(f'The servo does not answer {value:02X} on its new ID')
        self.current = value
        self.trace(f'Physical ID {value:02X} written, the servo answers on 0x{phys_id:02X}')
        self.screenshot('xact-changed')

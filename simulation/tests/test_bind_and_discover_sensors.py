"""A new model (non stabilized receiver), the S.Port connector powered by SA-, the internal
module in TD mode bound to RX1, then the discovery of the telemetry sensors.

On a radio, SA is moved by the operator when the test asks for it (the receiver is powered by
the S.Port connector). The simulator binds its simulated receivers (SR10...) and moves SA itself.

Options (run_tests.py --option):
  receiver=NAME    the receiver to bind in the Select device list (default: the first one)
  min_sensors=N    the sensors the discovery must find (default 1 on a radio, 0 on a simulator)
"""

import re
import time

from ethos_remote import TestFailure
from ethos_test import EthosBaseTest

SA_INDEX = 0          # setSwitch() index of SA (simulator)
SA_MIDDLE = 0         # setSwitch() value of SA-


class BindAndDiscoverSensorsTest(EthosBaseTest):
    TITLE = 'New model, bind RX1 (TD mode), discover the sensors'

    receiver = None   # the receiver bound

    def steps(self):
        return [
            self.create_model,
            self.sport_power_on_sa,
            self.internal_module_on,
            self.td_mode,
            self.bind_rx1,
            self.discover_sensors,
        ]

    def setup(self):
        """FS4 off: the receiver is not powered yet (a previous run can leave it on)"""
        if self.function_switch('FS4'):
            self.reset_function_switches()
            self.wait_function_switch('FS4', False)

    def teardown(self):
        """FS4 off, even after a failure: the receiver is not powered any more"""
        # in the 6-Pos mode of a new model, a press of FS4 leaves it on: all of them turned off
        self.reset_function_switches()
        self.wait_function_switch('FS4', False)

    def create_model(self):
        """Model / Model select / + / Create model"""
        self.open_model_menu('Model select')
        self.scroll_to_end()  # many models: the "+" tile is after the last one
        self.touch_button(area=(0.0, 0.2, 1.0, 0.8), empty=True, last=True)
        self.touch_text('Create model')
        if not self.has_title('Create model', timeout=3):
            self.fail('The model wizard did not start')
        for _ in range(10):
            if not self.has_title('Create model'):
                return
            self.press('PAGE')
            self.wait(0.3)
        self.fail('The model wizard did not end')

    def sport_power_on_sa(self):
        """Model / Edit model: S.Port connector power (5V) on FS4"""
        self.open_model_menu('Edit model')
        label = self.scroll_to_text('S.Port connector power (5V)')
        x = (self.display[0] or 800) * 3 // 4  # its value, "---" (not read)
        self._remember_touch(x, label.center[1], 'S.Port connector power value', label)
        self.touch(x, label.center[1], ms=100, what='value of "S.Port connector power (5V)" line')
        self.touch_text('Function switches')
        self.touch_text('FS4')
        shown = self.value_of('S.Port connector power (5V)')
        if shown != 'FS4':
            self.fail(f'S.Port connector power: {shown} shown, FS4 expected')

    def internal_module_on(self):
        """Model / RF system: Internal module State ON"""
        self.open_model_menu('RF system')
        self.touch_text('Internal module')  # unfolds it
        self.set_toggle('State', True)

    def td_mode(self):
        """Internal module: Protocol TD Mode"""
        self.select_choice_of('Protocol', 'TD Mode')

    def bind_rx1(self):
        """RX1: bind"""
        self.scroll_to_text('RX1')
        self.touch_text('RX1')
        self.find_text('Waiting for receiver', timeout=5)
        self.press('FS4')  # the S.Port connector powers the receiver
        self.wait_function_switch('FS4', True)
        self.find_text('Select device', timeout=120)  # the receivers answering
        wanted = self.option('receiver')
        if wanted:
            self.receiver = wanted
        else:
            title = self.find_text('Select device')
            items = [t for t in self.texts if t.y > title.y + title.h and abs(t.center[0] - title.center[0]) < title.w]
            if not items:
                self.fail('No receiver in the Select device list')
            self.receiver = items[0].text
        self.touch_text(self.receiver)
        # RX1 is replaced by the name of the receiver bound
        deadline = time.time() + 10
        while not (self.has_text(self.receiver) and not self.has_text('RX1')):
            if time.time() > deadline:
                self.fail(f'RX1 does not show {self.receiver} after the bind')
            time.sleep(0.5)
        self.trace(f'RX1 bound to {self.receiver}')
        self.screenshot('bound')

    def discover_sensors(self):
        """Model / Telemetry: Discover new sensors"""
        self.open_model_menu('Telemetry')
        self.touch_button(right_of='Source')
        self.touch_text('Discover new sensors')
        found = 0
        deadline = time.time() + 120
        while self.has_text('Discover new sensors', timeout=1):  # the dialog, closed at 100%
            for text in self.texts:
                match = re.search(r'Sensors\s*:\s*(\d+)', text.text)
                if match:
                    found = max(found, int(match.group(1)))
            if time.time() > deadline:
                self.fail('The discovery did not end')
        minimum = int(self.option('min_sensors', 1 if self.kind == 'radio' else 0))
        self.trace(f'{found} sensor(s) discovered')
        self.screenshot('sensors')
        if found < minimum:
            self.fail(f'{found} sensor(s) discovered, {minimum} at least expected')

    def wait_function_switch(self, name, on, timeout=2):
        """Waits until the radio says the function switch is on (or off)."""
        self.function_switch(name)  # fails clearly when the states are not available
        try:
            self.wait_controls(lambda controls: controls['functionSwitches'][name] == on, timeout)
        except TestFailure:
            self.fail(f'{name} stays {"off" if on else "on"}')

    def function_switch(self, name):
        """The state of a function switch of the current model."""
        states = self.function_switches()
        if states is None:
            self.fail('The states of the function switches are not available (firmware before the remote '
                      'protocol 11, or a radio without function switches)')
        return states[name]


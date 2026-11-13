"""System / Info: the firmware shown (radio, version, country protocol, git revision) must
be the one the target gives (getInformation: the HELLO of a radio, the boot trace of a
simulator)."""

import re

from ethos_test import EthosBaseTest

# "Ethos X20RS 26.2.0-RC11 FCC #53b00c58": the country protocol is not shown by all the radios
FIRMWARE = re.compile(r'Ethos\s+(?P<board>\S+)\s+(?P<version>\d+\.\d+\.\d+(?:-\S+)?)'
                      r'(?:\s+(?P<rfProtocol>[A-Z]+))?\s*#\s*(?P<gitHash>\w+)', re.IGNORECASE)


def hex_text(text):
    """A git hash as read by the OCR: O for 0, l for 1."""
    return text.lower().replace('o', '0').replace('l', '1')


class FirmwareInfoTest(EthosBaseTest):
    TITLE = 'System / Info: firmware version'

    shown = None

    def steps(self):
        return [
            ('System / Info', lambda: self.open_system_menu('Info')),
            self.read_firmware,
            self.check_firmware,
            self.back,
        ]

    def read_firmware(self):
        """Read the Firmware line"""
        text = self.value_of('Firmware')
        match = FIRMWARE.search(text or '')
        if not match:
            self.fail(f'Firmware line not understood: {text!r}')
        self.shown = match.groupdict()
        self.trace(f'shown: {text}')
        self.screenshot('info')

    def check_firmware(self):
        """Check it with the target information"""
        info = self.info
        errors = []
        if self.shown['board'].upper() != (info.get('board') or '').upper():
            errors.append(f'radio {self.shown["board"]}, {info.get("board")} expected')
        version = info.get('version') or ''
        if self.shown['version'] != version:
            # A radio before the remote protocol 11 doesn't give the suffix (RC11...) of its version
            if '-' not in version and self.shown['version'].split('-')[0] == version:
                self.trace(f'version {self.shown["version"]}: the radio gives {version} only (remote protocol < 11)')
            else:
                errors.append(f'version {self.shown["version"]}, {version} expected')
        if info.get('rfProtocol') and self.shown['rfProtocol'] and self.shown['rfProtocol'] != info['rfProtocol']:
            errors.append(f'country protocol {self.shown["rfProtocol"]}, {info["rfProtocol"]} expected')
        expected_hash = hex_text(info.get('gitHash') or '')
        if expected_hash and not expected_hash.startswith(hex_text(self.shown['gitHash'])[:7]):
            errors.append(f'git revision {self.shown["gitHash"]}, {info.get("gitHash")} expected')
        if errors:
            self.fail(', '.join(errors))
        self.trace(f'{self.shown["board"]} {self.shown["version"]} {self.shown["rfProtocol"] or ""} '
                   f'#{self.shown["gitHash"]} as given by the {self.kind}')

"""Helpers for the tests that drive Ethos through the command server of
radio_remote.py (a radio on a serial port) or run_wasm.js (a WASM simulator: any
radio, release or debug, any country protocol). Both servers take the same
commands (see radio_remote.md and run_wasm.md), so a test runs on both.

    target = Target.simulator('build/X20RS/simulator/X20RS_FCC.js')   # or Target.radio('COM3'), Target.server(8766)
    remote = target.start()
    remote.press('SYS')
    log = remote.log_watcher()            # the log lines from now on
    remote.touch(330, 91)
    match = log.wait_for(r'Connected phyId: 0x\\s*([0-9A-F]{2})', timeout=10)
    target.stop()

No dependency besides the Python standard library (radio_remote.py needs pyserial).
"""

import base64
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

SIMULATION_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class TestFailure(Exception):
    pass


class TestSkipped(Exception):
    pass


# ---------- Targets ----------

def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class Target:
    """Where the tests run: a simulator started with run_wasm.js, a radio on a serial
    port through radio_remote.py, or a command server already running."""

    def __init__(self, kind, name, command=None, port=None, root=None, log_file=None, start_timeout=60):
        self.kind = kind            # 'simulator' or 'radio'
        self.name = name
        self.command = command      # None: a server already running on port
        self.port = port or free_port()
        self.root = root            # simulator storage, a fresh one when None
        self.fresh_root = False
        self.log_file = log_file
        self.start_timeout = start_timeout
        self.process = None
        self.remote = None

    @classmethod
    def simulator(cls, js_path, root=None):
        """A simulator .js file."""
        js_path = os.path.abspath(js_path)
        name = os.path.splitext(os.path.basename(js_path))[0]  # X20RS_FCC
        target = cls('simulator', name, root=root)
        target.simulator_args = [js_path]
        return target

    @classmethod
    def release_simulator(cls, radio, release, root=None):
        """The simulator of radio (X20RS-FCC...) of a release on GitHub, downloaded by
        run_wasm.js unless it is in the cache (see simulators.js)."""
        target = cls('simulator', f'{radio}@{release}', root=root, start_timeout=360)  # the download
        target.simulator_args = ['--radio', radio, '--release', release]
        return target

    @staticmethod
    def list_release_simulators(pattern, release='latest'):
        """(release tag, radios) of the release simulators matching pattern (X20RS, X20*, X20RS-*...)."""
        result = subprocess.run(['node', os.path.join(SIMULATION_DIR, 'run_wasm.js'), '--list-simulators',
                                 '--radio', pattern, '--release', release],
                                capture_output=True, text=True, encoding='utf-8', errors='replace', check=False)
        try:
            body = json.loads(result.stdout)
        except ValueError as e:
            raise TestFailure(f'run_wasm.js --list-simulators: {result.stdout or result.stderr}') from e
        if not body.get('ok'):
            raise TestFailure(body.get('error'))
        return body['release'], body['radios']

    @classmethod
    def radio(cls, com=None, accept_timeout=120):
        target = cls('radio', com or 'usb', start_timeout=accept_timeout + 10)
        target.com = com
        target.accept_timeout = accept_timeout
        return target

    @classmethod
    def server(cls, port):
        """A radio_remote.py or run_wasm.js --serve already running."""
        return cls(None, f'port {port}', port=port)

    def start(self, shots_dir):
        if self.kind == 'simulator':
            if self.root is None:
                self.root = tempfile.mkdtemp(prefix=f'ethos-{self.name}-')
                self.fresh_root = True
            self.command = ['node', os.path.join(SIMULATION_DIR, 'run_wasm.js'), *self.simulator_args, '--serve',
                            '--port', str(self.port), '--root-directory', self.root, '--shots-dir', shots_dir]
        elif self.kind == 'radio':
            self.command = [sys.executable, os.path.join(SIMULATION_DIR, 'radio_remote.py'), '--serve',
                            '--port', str(self.port), '--shots-dir', shots_dir,
                            '--accept-timeout', str(self.accept_timeout)] + (['--com', self.com] if self.com else [])
        if self.command:
            self._start_server()
        self.remote = Remote(self.port, shots_dir)
        info = self.remote.information()
        if self.kind is None:
            self.kind = 'simulator' if info.get('target') == 'simulator' else 'radio'
        if self.fresh_root:
            self.first_boot()
        return self.remote

    def _start_server(self):
        if self.kind == 'radio':
            print(f'[{self.name}] accept the connection on the radio screen...', flush=True)
        ready = threading.Event()
        log = open(self.log_file, 'w', encoding='utf-8', errors='replace') if self.log_file else None
        self.process = subprocess.Popen(self.command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, encoding='utf-8', errors='replace')

        def drain():
            # The server output (the radio log) is read all along, the pipe must never fill up
            for line in self.process.stdout:
                if log:
                    log.write(line)
                    log.flush()
                if line.startswith('[run_wasm] ') and ('downloading' in line or ' downloaded: ' in line):
                    print(f'[{self.name}] {line[11:].strip()}', flush=True)  # a simulator of a release
                if 'serving on' in line:
                    ready.set()
            if log:
                log.close()

        threading.Thread(target=drain, daemon=True).start()
        deadline = time.time() + self.start_timeout
        while not ready.wait(0.2):
            if self.process.poll() is not None or time.time() > deadline:
                self.stop()
                raise TestFailure(f'{self.name}: the command server did not start'
                                  + (f', see {self.log_file}' if self.log_file else ''))

    # The screens of a first boot, by their title, and the key which closes them
    FIRST_BOOT_SCREENS = [
        ('Select language', 'ENTER'),    # OK has the focus, English
        ('Radio data load', 'ENTER'),    # no radio.bin: the default settings
        ('Model data load', 'ENTER'),    # no model: the model wizard is started
        ('Create model', 'RTN'),         # leaves the model wizard
    ]

    def first_boot(self, timeout=30):
        """A simulator on an empty storage: each screen of the first boot is read (OCR) and
        closed with its key, until none of them is shown: the home screen."""
        import ethos_ocr  # pylint: disable=import-outside-toplevel
        r = self.remote
        path = os.path.join(r.shots_dir, 'boot.png')
        deadline = time.time() + timeout
        seen = set()
        while time.time() < deadline:
            os.makedirs(r.shots_dir, exist_ok=True)
            r.call('screenshot', out=path)
            texts = [t.text for t in ethos_ocr.read(path)]
            screen = next(((title, key) for title, key in self.FIRST_BOOT_SCREENS
                           if title not in seen and any(ethos_ocr.matches(t, title) for t in texts)), None)
            if screen:
                title, key = screen
                others = [t for t in texts if not ethos_ocr.matches(t, title)]
                r.trace(f'first boot: "{title}" ({", ".join(others[:6])}) -> {key}')
                r.press(key)
                seen.add(title)
            elif seen:
                r.trace(f'first boot: home screen ({", ".join(texts[:6])})')
                return
            time.sleep(0.5)  # the first screen is not shown yet
        raise TestFailure(f'first boot: no home screen after {timeout} s, seen: {", ".join(sorted(seen)) or "nothing"}')

    def stop(self):
        if self.process:
            try:
                if self.remote:
                    self.remote.call('quit', check=False)
                self.process.wait(5)
            except (TestFailure, subprocess.TimeoutExpired, OSError):
                self.process.kill()
            self.process = None
        if self.fresh_root and self.root:
            shutil.rmtree(self.root, ignore_errors=True)

    def describe(self, info):
        parts = [info.get('board'), info.get('version'), info.get('rfProtocol'),
                 info.get('gitHash') and f'git {info["gitHash"]}',
                 info.get('protocolVersion') and f'remote protocol {info["protocolVersion"]}', f'({self.kind})']
        return ' '.join(p for p in parts if p)


# ---------- Commands ----------

class Remote:
    # After an input, the time (ms) the server waits before answering (a radio answers once it repainted,
    # a simulator waits for a new frame, at most this time): none, the tests read the screen again until
    # it shows what they wait for
    SETTLE = 0

    def __init__(self, port, shots_dir='screenshots', verbose=True):
        self.port = port
        self.shots_dir = os.path.abspath(shots_dir)
        self.verbose = verbose
        self.shot_index = 0

    def call(self, cmd, check=True, **params):
        """Sends a command (arguments in a JSON body) and returns the JSON answer.
        With check, an answer with ok = false raises TestFailure."""
        data = json.dumps({k: v for k, v in params.items() if v is not None}).encode()
        request = urllib.request.Request(f'http://127.0.0.1:{self.port}/{cmd}', data=data,
                                         headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=120) as res:
                body = json.loads(res.read())
        except urllib.error.HTTPError as e:
            body = json.loads(e.read())
        except urllib.error.URLError as e:
            raise TestFailure(f'No command server on port {self.port} ({e.reason})') from e
        if check and not body.get('ok'):
            raise TestFailure(f'{cmd} {params}: {body.get("error") or body.get("status")}')
        return body

    def _input(self, cmd, **params):
        """An input on the screen: when the screen was off, it only turned it back on
        (status "woke up"), as for the user, so it is sent again."""
        params.setdefault('settle', self.SETTLE)
        body = self.call(cmd, check=False, **params)
        if not body.get('ok') and str(body.get('status', '')).startswith('woke up'):
            body = self.call(cmd, check=False, **params)
        if not body.get('ok'):
            raise TestFailure(f'{cmd} {params}: {body.get("error") or body.get("status")}')
        return body

    def press(self, name, ms=20):
        self.trace(f'press {name}' + (' (long)' if ms >= 540 else ''))  # from 540 ms a long press
        return self._input('press', name=name, ms=ms)

    def touch(self, x, y, ms=20, label=None, what=None):
        """For the trace, label: the text touched (touch "OK" at x,y), or what: a description
        of what is touched (touch choice of "Mode" line at x,y)."""
        if what:
            self.trace(f'touch {what} at {x},{y}')
        else:
            self.trace(f'touch "{label}" at {x},{y}' if label else f'touch {x},{y}')
        return self._input('touch', x=x, y=y, ms=ms)

    def swipe(self, x1, y1, x2, y2):
        self.trace(f'swipe {x1},{y1} -> {x2},{y2}')
        return self._input('swipe', x1=x1, y1=y1, x2=x2, y2=y2)

    def turn_rotary_encoder(self, steps):
        self.trace(f'turnRotaryEncoder {steps}')
        return self._input('turnRotaryEncoder', steps=steps)

    def enter_text(self, text):
        return self.call('enterText', text=text)

    def reset_backlight_timer(self):
        return self.call('resetBacklightTimer')

    def reload(self, what='SCRIPTS'):
        self.trace(f'reload {what}')
        return self.call('reload', what=what)

    def information(self):
        return self.call('getInformation')

    def channels_outputs(self):
        """The mixer outputs of the channels used by the model, in % (CH1 first)."""
        return self.call('getChannelsOutputs')['outputs']

    def controls(self, since=None, timeout=30):
        """The switches ({"SA": -1 up, 0 middle, 1 down}), trims (in %: rudder, elevator, throttle,
        aileron, T5, T6), function switches ({"FS1": on}, as their LEDs) and their version, as the
        radio sends them when they change. With since (a version), once they changed (timeout s at
        most). None when the radio doesn't send them (firmware before the protocol version 11)."""
        body = self.call('getControls', check=False, since=since, timeout=int(timeout * 1000))
        return body if body.get('ok') else None

    def wait_controls(self, condition, timeout=30):
        """The controls once condition(controls) is true (TestFailure after timeout seconds)."""
        deadline = time.time() + timeout
        controls = self.controls()
        while controls is not None and not condition(controls):
            if time.time() > deadline:
                raise TestFailure(f'Controls: still {controls} after {timeout} s')
            controls = self.controls(controls['version'], max(deadline - time.time(), 0.1))
        if controls is None:
            raise TestFailure('The radio sends no controls (firmware before the remote protocol 11)')
        return controls

    def trims(self):
        """The trims of the current flight mode, in % (rudder, elevator, throttle, aileron, T5, T6)."""
        return self.call('getTrims')['trims']

    def switches(self):
        """{"SA": -1 up, 0 middle, 1 down, ...}: the switches installed (None, see controls())."""
        controls = self.controls()
        return controls['switches'] if controls else None

    def function_switches(self):
        """{"FS1": on, ...}: the function switches of the current model, as their LEDs (None, see
        controls())."""
        controls = self.controls()
        return controls['functionSwitches'] if controls else None

    def reset_function_switches(self):
        """Turns all the function switches of the current model off, as for a new model."""
        self.trace('resetFunctionSwitches')
        return self.call('resetFunctionSwitches')

    def wait(self, seconds):
        time.sleep(seconds)

    # ---------- Files ----------

    def list_files(self, path):
        return self.call('listFiles', path=path)['files']

    def read_file(self, path):
        body = self.call('readFile', path=path)
        return body['text'].encode('utf-8') if 'text' in body else base64.b64decode(body['base64'])

    def write_file(self, path, data):
        return self.call('writeFile', path=path, base64=base64.b64encode(data).decode('ascii'))

    def create_directory(self, path):
        """Its parent must exist. No error when the directory already exists."""
        body = self.call('createDirectory', check=False, path=path)
        if not body.get('ok'):
            parent, name = path.rstrip('/').rsplit('/', 1)
            if not any(f['name'] == name and f['isDirectory'] for f in self.list_files(parent or '/')):
                raise TestFailure(f'createDirectory {path}: {body.get("error") or body.get("status")}')

    def delete_file(self, path):
        return self.call('deleteFile', path=path)

    def copy_directory(self, local_dir, radio_dir, exclude=()):
        """Copies a local folder to the radio (its sub-folders too), its parents
        created, and reads each file back."""
        self.trace(f'copy {local_dir} -> {radio_dir}')
        parts = radio_dir.strip('/').split('/')
        for i in range(1, len(parts) + 1):
            self.create_directory('/' + '/'.join(parts[:i]))
        for root, dirs, files in os.walk(local_dir):
            dirs.sort()
            relative = os.path.relpath(root, local_dir).replace('\\', '/')
            target = radio_dir if relative == '.' else f'{radio_dir}/{relative}'
            if relative != '.':
                self.create_directory(target)
            for name in sorted(files):
                if name in exclude:
                    continue
                with open(os.path.join(root, name), 'rb') as f:
                    data = f.read()
                self.write_file(f'{target}/{name}', data)
                if self.read_file(f'{target}/{name}') != data:
                    raise TestFailure(f'{target}/{name}: the file read back differs from the one written')

    # ---------- Screen and log ----------

    def screenshot(self, name):
        """Saves the screen as <shots_dir>/<index>-<name>.png and returns the file."""
        self.shot_index += 1
        out = os.path.join(self.shots_dir, f'{self.shot_index:02d}-{name}.png')
        os.makedirs(self.shots_dir, exist_ok=True)
        return self.call('screenshot', out=out)['file']

    def log_watcher(self):
        return LogWatcher(self)

    def trace(self, text):
        if self.verbose:
            print(f'      {text}', flush=True)


class LogWatcher:
    """The log lines (Lua print() and errors) received after its creation. A Lua
    error (a line matching fail_on) fails the test."""

    LUA_ERROR = r'error: .*\.lua:\d+:'

    def __init__(self, remote, fail_on=LUA_ERROR):
        self.remote = remote
        self.fail_on = re.compile(fail_on) if fail_on else None
        self.next = remote.call('getLog')['next']
        self.lines = []
        self.position = 0  # lines already searched by wait_for

    def poll(self):
        """Reads the lines added since the previous poll: getLog gives 200 at most per
        call, and says with more that others follow."""
        lines = []
        while True:
            body = self.remote.call('getLog', since=self.next)
            if body.get('truncated'):
                print('      warning: some log lines were lost, poll more often', file=sys.stderr)
            lines += body['lines']
            self.next = body['next']
            if not body.get('more'):
                break
        self.lines += lines
        if self.fail_on:
            for line in lines:
                if self.fail_on.search(line):
                    raise TestFailure(f'Lua error: {line.strip()}')

    def wait_for(self, pattern, timeout=10, consume=True):
        """Waits for a line matching pattern (a regex) and returns its match. With
        consume, the next wait_for searches the lines after it only."""
        regex = re.compile(pattern)
        deadline = time.time() + timeout
        while True:
            for index in range(self.position, len(self.lines)):
                match = regex.search(self.lines[index])
                if match:
                    if consume:
                        self.position = index + 1
                    return match
            if time.time() > deadline:
                raise TestFailure(f'No log line matching {pattern!r} within {timeout} s')
            time.sleep(0.2)
            self.poll()

    def collect(self, pattern, activity=None, quiet=3, timeout=30):
        """The matches of pattern in the lines from now on, until no line matches
        activity (a regex, pattern by default) for quiet seconds."""
        regex = re.compile(pattern)
        active = re.compile(activity) if activity else regex
        matches = []
        deadline = time.time() + timeout
        last_activity = time.time()
        while True:
            for line in self.lines[self.position:]:
                match = regex.search(line)
                if match:
                    matches.append(match)
                if active.search(line):
                    last_activity = time.time()
            self.position = len(self.lines)
            if time.time() - last_activity > quiet:
                return matches
            if time.time() > deadline:
                raise TestFailure(f'Still log lines matching {active.pattern!r} after {timeout} s')
            time.sleep(0.2)
            self.poll()

    def find_all(self, pattern):
        """The matches of pattern in the lines received so far."""
        self.poll()
        regex = re.compile(pattern)
        return [m for m in (regex.search(line) for line in self.lines) if m]

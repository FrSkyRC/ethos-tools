#!/usr/bin/env python3
"""Drives a physical Ethos radio over USB with the remote control protocol.

  python radio_remote.py mode serial|storage
      Asks the radio, over USB HID, to switch its USB port to Serial (debug) or storage mode.

  python radio_remote.py --serve [--com COMx] [--port N] [--shots-dir DIR]
      Opens the radio's serial port, waits for the connection to be accepted on the
      radio screen, then keeps it open behind a small HTTP server on 127.0.0.1.

  python radio_remote.py <command> [args...] [--port N]
      Client: sends one command (screenshot, press, tap...) to a serving instance.

Requires pyserial (serial) and hidapi (hid, only for `mode`). See radio_remote.md.
"""

import argparse
import json
import os
import re
import struct
import sys
import threading
import time
import urllib.parse
import urllib.request
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ETHOS_VID = 0x0483
ETHOS_PID = 0x5750
DEFAULT_PORT = 8766

# USB HID mode request, as used by the Ethos suite updaters
HID_USB_MODE_REQUEST = 0x81
HID_USB_MODE = {'serial': 0x68, 'storage': 0x69}

SYNC = 0xE7
CMD = {
    'HELLO': 0x01,
    'PRESS_KEY': 0x10,
    'TOUCH': 0x11,
    'SWIPE': 0x12,
    'TURN_ROTARY_ENCODER': 0x13,
    'ENTER_TEXT': 0x14,
    'RESET_BACKLIGHT_TIMER': 0x15,
    'PRESS_FUNCTION_SWITCH': 0x16,
    'RESET_INACTIVITY_TIMER': 0x1B,
    'SCREENSHOT': 0x20,
    'LIST_FILES': 0x30,
    'READ_FILE': 0x31,
    'WRITE_FILE': 0x32,
    'DELETE_FILE': 0x33,
    'LOAD_MODEL': 0x40,
    'CHANNELS_OUTPUTS': 0x50,
    'TRIMS': 0x51,
}
EVENT_STATE = 0xC1
EVENT_CONNECTION = 0xC2
EVENT_LOG = 0xC3
STATUS = {0: 'success', -1: 'unsupported', -2: 'refused', -3: 'invalid argument',
          -4: 'I/O error', -5: 'busy', -6: 'timeout'}
KEYS = ['SYS', 'MDL', 'DISP', 'PAGE', 'RTN', 'ENTER', 'UP', 'DOWN', 'LEFT', 'RIGHT']
TRIM_KEY = re.compile(r'T[1-6][+-]')  # trims are pressed as keys: T1+ moves trim 1 up
DISPLAYS = {'main': 0, 'top': 1, 'glasses': 2}

# Client commands and the names of their positional arguments
CLIENT_COMMANDS = {
    'status': [],
    'screenshot': ['out', 'display'],
    'press': ['key', 'hold'],
    'tap': ['x', 'y'],
    'longpress': ['x', 'y'],
    'swipe': ['x1', 'y1', 'x2', 'y2'],
    'wheel': ['steps'],
    'text': ['text'],
    'fswitch': ['index', 'hold'],
    'ls': ['path'],
    'read': ['path', 'out'],
    'write': ['src', 'path'],
    'rm': ['path'],
    'load': ['path'],
    'trims': [],
    'log': ['n'],
    'wait': ['ms'],
    'quit': [],
}


def crc16(data):
    """CRC-16/XMODEM: polynomial 0x1021, initial value 0."""
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def cstr(text):
    return text.encode('utf-8') + b'\0'


class Reader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def u8(self):
        self.pos += 1
        return self.data[self.pos - 1]

    def u32(self):
        self.pos += 4
        return struct.unpack_from('<I', self.data, self.pos - 4)[0]

    def string(self):
        end = self.data.index(0, self.pos)
        text = self.data[self.pos:end].decode('utf-8', 'replace')
        self.pos = end + 1
        return text

    def at_end(self):
        return self.pos >= len(self.data)


# ---------- Screenshot decoding ----------

def decode_qoi(data):
    """QOI image, decoded to RGB565. Tiles from SCREENSHOT are sent in this format."""
    if len(data) < 22:
        return None
    width, height, channels = struct.unpack_from('>IIB', data, 4)
    pixels = [0] * (width * height)
    index = bytearray(256)
    r = g = b = 0
    a = 255
    run = 0
    p = 14
    end = len(data) - 8
    for x in range(width * height):
        if run > 0:
            run -= 1
        elif p < end:
            s = data[p]
            p += 1
            if s == 0xFE:
                r, g, b = data[p], data[p + 1], data[p + 2]
                p += 3
            elif s == 0xFF:
                r, g, b, a = data[p], data[p + 1], data[p + 2], data[p + 3]
                p += 4
            elif s & 0xC0 == 0x00:
                k = (s & 0x3F) * 4
                r, g, b, a = index[k], index[k + 1], index[k + 2], index[k + 3]
            elif s & 0xC0 == 0x40:
                r = (r + ((s >> 4) & 3) - 2) & 255
                g = (g + ((s >> 2) & 3) - 2) & 255
                b = (b + (s & 3) - 2) & 255
            elif s & 0xC0 == 0x80:
                q = data[p]
                p += 1
                v = (s & 0x3F) - 32
                r = (r + v - 8 + ((q >> 4) & 15)) & 255
                g = (g + v) & 255
                b = (b + v - 8 + (q & 15)) & 255
            else:
                run = s & 0x3F
            k = ((r * 3 + g * 5 + b * 7 + a * 11) % 64) * 4
            index[k:k + 4] = bytes((r, g, b, a))
        else:
            return None
        pixels[x] = (r >> 3) << 11 | (g >> 2) << 5 | (b >> 3)
    return (width, height, pixels) if channels in (3, 4) else None


def decode_tiles(data, previous):
    """SCREENSHOT payload (protocol >= 4): only the tiles that changed since the last one.

    width (2), height (2), tile size (1), bitmap of changed tiles, then one QOI image
    holding the changed tiles stacked vertically. Returns (width, height, pixels).
    """
    if len(data) < 5:
        return None
    width, height, tile = struct.unpack_from('<HHB', data)
    if not tile:
        return None
    tiles_x = -(-width // tile)
    tiles_y = -(-height // tile)
    bitmap_len = -(-tiles_x * tiles_y // 8)
    if len(data) < 5 + bitmap_len:
        return None
    changed = [i for i in range(tiles_x * tiles_y) if data[5 + (i >> 3)] & (1 << (i & 7))]
    reuse = previous is not None and previous[0] == width and previous[1] == height
    if len(changed) < tiles_x * tiles_y and not reuse:
        return None
    pixels = list(previous[2]) if reuse else [0] * (width * height)
    if changed:
        image = decode_qoi(data[5 + bitmap_len:])
        if not image or image[0] != tile or image[1] != tile * len(changed):
            return None
        tile_pixels = image[2]
        for n, i in enumerate(changed):
            x = (i % tiles_x) * tile
            y = (i // tiles_x) * tile
            w = min(tile, width - x)
            for row in range(tile):
                if y + row >= height:
                    break
                src = (n * tile + row) * tile
                dst = (y + row) * width + x
                pixels[dst:dst + w] = tile_pixels[src:src + w]
    return width, height, pixels


def encode_png(width, height, rgb565):
    def chunk(kind, body):
        payload = kind + body
        return struct.pack('>I', len(body)) + payload + struct.pack('>I', zlib.crc32(payload) & 0xFFFFFFFF)

    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for p in rgb565[y * width:(y + 1) * width]:
            raw += bytes((((p >> 11) & 31) * 255 // 31, ((p >> 5) & 63) * 255 // 63, (p & 31) * 255 // 31))
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(bytes(raw)))
            + chunk(b'IEND', b''))


# ---------- Protocol client ----------

class Radio:
    def __init__(self, com, log):
        import serial  # pylint: disable=import-outside-toplevel
        self.serial = serial.Serial(com, 115200, timeout=0.05)
        self.log = log
        self.seq = 0
        self.pending = {}
        self.lock = threading.Lock()
        self.accepted = threading.Event()
        self.refused = False
        self.screens = {}
        self.hello = None
        threading.Thread(target=self._read_loop, daemon=True).start()

    def _read_loop(self):
        state, frame, length = 'sync', bytearray(), 0
        while True:
            for byte in self.serial.read(4096):
                if state == 'sync':
                    if byte == SYNC:
                        frame, state = bytearray(), 'header'
                elif state == 'header':
                    frame.append(byte)
                    if len(frame) == 4:
                        length = frame[2] | frame[3] << 8
                        state = 'sync' if length > 1024 else ('payload' if length else 'crc')
                elif state == 'payload':
                    frame.append(byte)
                    if len(frame) == 4 + length:
                        state = 'crc'
                else:
                    frame.append(byte)
                    if len(frame) == 6 + length:
                        state = 'sync'
                        if frame[4 + length] | frame[5 + length] << 8 == crc16(frame[:4 + length]):
                            self._on_frame(frame[0], frame[1], bytes(frame[4:4 + length]))

    def _on_frame(self, command, seq, payload):
        if seq == 0:
            if command == EVENT_CONNECTION:
                if payload and payload[0]:
                    self.accepted.set()
                else:
                    self.refused = True
            elif command == EVENT_LOG:
                self.log(payload.decode('utf-8', 'replace'))
            return
        request = self.pending.get(seq)
        if not request or not command & 0x80 or len(payload) < 2:
            return
        request['chunks'].append(payload[2:])
        if payload[1] & 0x01:
            request['status'] = struct.unpack('b', payload[:1])[0]
            request['done'].set()

    def request(self, command, payload=b'', timeout=10):
        with self.lock:
            self.seq = self.seq % 255 + 1
            seq = self.seq
            body = bytes((command, seq, len(payload) & 255, len(payload) >> 8)) + payload
            crc = crc16(body)
            request = {'chunks': [], 'status': -6, 'done': threading.Event()}
            self.pending[seq] = request
            self.serial.write(bytes((SYNC,)) + body + bytes((crc & 255, crc >> 8)))
        request['done'].wait(timeout)
        self.pending.pop(seq, None)
        return request['status'], b''.join(request['chunks'])

    def connect(self, accept_timeout):
        status, data = self.request(CMD['HELLO'], bytes((6,)))
        if status:
            raise RuntimeError(f'HELLO failed: {STATUS.get(status, status)}')
        r = Reader(data)
        self.hello = {'protocol': r.u8(), 'firmware': f'{r.u8()}.{r.u8()}.{r.u8()}',
                      'board': r.string(), 'region': r.string(), 'gitHash': r.string()}
        # Already accepted earlier in this connection: no prompt on the radio
        if self.request(CMD['RESET_INACTIVITY_TIMER'])[0] == 0:
            self.accepted.set()
        if not self.accepted.wait(accept_timeout):
            raise RuntimeError('The connection was not accepted on the radio screen'
                               if not self.refused else 'The connection was refused on the radio screen')
        return self.hello

    def screenshot(self, display=0):
        for _ in range(2):
            full = display not in self.screens
            status, data = self.request(CMD['SCREENSHOT'], bytes((display, 1 if full else 0)))
            if status:
                self.screens.pop(display, None)
                return status, None
            image = decode_tiles(data, self.screens.get(display))
            if image:
                self.screens[display] = image
                return 0, image
            self.screens.pop(display, None)
        return -4, None

    def press_key(self, key, hold_ms=20):
        return self.request(CMD['PRESS_KEY'], struct.pack('<I', hold_ms) + cstr(key))[0]

    def press_function_switch(self, index, hold_ms=20):
        return self.request(CMD['PRESS_FUNCTION_SWITCH'], struct.pack('<I', hold_ms) + cstr(f'FS{index}'))[0]

    def touch(self, x, y, long_press=False):
        return self.request(CMD['TOUCH'], struct.pack('<HHB', x, y, 1 if long_press else 0))[0]

    def swipe(self, x1, y1, x2, y2):
        return self.request(CMD['SWIPE'], struct.pack('<HHHH', x1, y1, x2, y2))[0]

    def wheel(self, steps):
        return self.request(CMD['TURN_ROTARY_ENCODER'], struct.pack('<h', steps))[0]

    def enter_text(self, text):
        return self.request(CMD['ENTER_TEXT'], cstr(text))[0]

    def list_files(self, path):
        status, data = self.request(CMD['LIST_FILES'], cstr(path))
        files = []
        r = Reader(data)
        while status == 0 and not r.at_end():
            is_dir = r.u8() != 0
            size = r.u32()
            files.append({'name': r.string(), 'dir': is_dir, 'size': size})
        return status, files

    def read_file(self, path, offset=0):
        return self.request(CMD['READ_FILE'], struct.pack('<I', offset) + cstr(path), timeout=60)

    def write_file(self, path, data):
        room = 1024 - len(struct.pack('<I', 0) + cstr(path))
        offset = 0
        while True:
            part = data[offset:offset + room]
            status = self.request(CMD['WRITE_FILE'], struct.pack('<I', offset) + cstr(path) + part)[0]
            if status:
                return status
            offset += len(part)
            if offset >= len(data):
                return 0

    def delete_file(self, path):
        return self.request(CMD['DELETE_FILE'], cstr(path))[0]

    def load_model(self, path):
        return self.request(CMD['LOAD_MODEL'], cstr(path))[0]

    def trims(self):
        status, data = self.request(CMD['TRIMS'])
        values = [round(v * 100 / 256) for v in struct.unpack(f'<{len(data) // 2}h', data)] if status == 0 else []
        return status, values


def switch_usb_mode(mode):
    import hid  # pylint: disable=import-outside-toplevel
    device = hid.device()
    device.open(ETHOS_VID, ETHOS_PID)
    try:
        device.write(bytes((0x00, HID_USB_MODE_REQUEST, HID_USB_MODE[mode])))
    finally:
        device.close()


def find_com_port():
    from serial.tools import list_ports  # pylint: disable=import-outside-toplevel
    for port in list_ports.comports():
        if port.vid == ETHOS_VID and port.pid == ETHOS_PID:
            return port.device
    return None


# ---------- Server ----------

class Session:
    def __init__(self, radio, shots_dir):
        self.radio = radio
        self.shots_dir = shots_dir
        self.lines = []

    def handle(self, cmd, q):
        radio = self.radio
        num = lambda k, d=None: int(q[k]) if k in q else d  # noqa: E731
        result = lambda status, **extra: {'ok': status == 0, 'status': STATUS.get(status, status), **extra}  # noqa: E731
        settle = num('settle', 300) / 1000
        if cmd == 'status':
            return {'ok': True, **radio.hello}
        if cmd == 'screenshot':
            status, image = radio.screenshot(DISPLAYS.get(q.get('display', 'main'), 0))
            if status:
                return result(status)
            out = os.path.abspath(q.get('out') or os.path.join(self.shots_dir, f'radio-{int(time.time() * 1000)}.png'))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, 'wb') as f:
                f.write(encode_png(*image))
            return result(0, file=out, width=image[0], height=image[1])
        if cmd == 'press':
            key = q['key'].upper()
            if key not in KEYS and not TRIM_KEY.fullmatch(key):
                return {'ok': False, 'error': f'Unknown key {key}, expected one of: {", ".join(KEYS)}, or a trim T1+ ... T6-'}
            status = radio.press_key(key, num('hold', 100))
        elif cmd == 'tap':
            status = radio.touch(num('x'), num('y'))
        elif cmd == 'longpress':
            status = radio.touch(num('x'), num('y'), True)
        elif cmd == 'swipe':
            status = radio.swipe(num('x1'), num('y1'), num('x2'), num('y2'))
        elif cmd == 'wheel':
            status = radio.wheel(num('steps', 1))
        elif cmd == 'text':
            status = radio.enter_text(q['text'])
        elif cmd == 'fswitch':
            status = radio.press_function_switch(num('index'), num('hold', 100))
        elif cmd == 'ls':
            status, files = radio.list_files(q.get('path', '/'))
            return result(status, files=files)
        elif cmd == 'read':
            status, data = radio.read_file(q['path'])
            if status == 0 and q.get('out'):
                with open(q['out'], 'wb') as f:
                    f.write(data)
                return result(status, file=os.path.abspath(q['out']), size=len(data))
            return result(status, size=len(data), text=data.decode('utf-8', 'replace'))
        elif cmd == 'write':
            with open(q['src'], 'rb') as f:
                status = radio.write_file(q['path'], f.read())
        elif cmd == 'rm':
            status = radio.delete_file(q['path'])
        elif cmd == 'load':
            status = radio.load_model(q['path'])
        elif cmd == 'trims':
            status, values = radio.trims()
            return result(status, trims=values)
        elif cmd == 'log':
            return {'ok': True, 'lines': self.lines[-num('n', 50):]}
        elif cmd == 'wait':
            time.sleep(num('ms', 500) / 1000)
            return {'ok': True}
        elif cmd == 'quit':
            threading.Timer(0.1, lambda: os._exit(0)).start()
            return {'ok': True}
        else:
            return {'ok': False, 'error': f'Unknown command {cmd}'}
        # Let the radio redraw, so a following screenshot shows the result
        time.sleep(settle)
        return result(status)


def serve(args):
    com = args.com or find_com_port()
    if not com:
        sys.exit('No Ethos radio serial port found: set its USB mode to Serial (or run `mode serial`)')
    lines = []
    radio = Radio(com, lambda text: (lines.append(text), sys.stdout.write(text)))
    print(f'[radio_remote] {com}: accept the connection on the radio screen...', flush=True)
    hello = radio.connect(args.accept_timeout)
    print(f'[radio_remote] {hello["board"]} firmware {hello["firmware"]} connected', flush=True)
    session = Session(radio, os.path.abspath(args.shots_dir))
    session.lines = lines
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # pylint: disable=invalid-name
            url = urllib.parse.urlparse(self.path)
            q = dict(urllib.parse.parse_qsl(url.query))
            # Commands run one at a time, in arrival order
            with lock:
                try:
                    body = session.handle(url.path.lstrip('/'), q)
                except Exception as e:  # pylint: disable=broad-except
                    body = {'ok': False, 'error': str(e)}
            data = json.dumps(body).encode()
            self.send_response(200 if body.get('ok') else 400)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_):
            pass

    print(f'[radio_remote] serving on http://127.0.0.1:{args.port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()


def client(cmd, values, port):
    params = {name: value for name, value in zip(CLIENT_COMMANDS[cmd], values)}
    if cmd == 'write' and 'src' in params:
        params['src'] = os.path.abspath(params['src'])
    if cmd == 'read' and 'out' in params:
        params['out'] = os.path.abspath(params['out'])
    url = f'http://127.0.0.1:{port}/{cmd}?{urllib.parse.urlencode(params)}'
    try:
        with urllib.request.urlopen(url, timeout=120) as res:
            body = res.read()
    except urllib.error.HTTPError as e:
        body = e.read()
    except urllib.error.URLError as e:
        sys.exit(f'No radio_remote server on port {port} ({e.reason}): start one with --serve')
    print(body.decode())
    sys.exit(0 if json.loads(body).get('ok') else 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', nargs='?', help='mode, or a client command: ' + ', '.join(CLIENT_COMMANDS))
    parser.add_argument('args', nargs='*')
    parser.add_argument('--serve', action='store_true', help='connect to the radio and serve commands')
    parser.add_argument('--com', help='serial port (default: the first port with the Ethos USB id)')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT, help=f'HTTP port (default {DEFAULT_PORT})')
    parser.add_argument('--shots-dir', default='screenshots', help='default folder for screenshots')
    parser.add_argument('--accept-timeout', type=float, default=60, help='seconds to wait for the on-screen accept')
    args = parser.parse_args()

    if args.serve:
        serve(args)
    elif args.command == 'mode' and args.args and args.args[0] in HID_USB_MODE:
        switch_usb_mode(args.args[0])
        print(json.dumps({'ok': True, 'mode': args.args[0]}))
    elif args.command in CLIENT_COMMANDS:
        client(args.command, args.args, args.port)
    else:
        parser.print_help()
        sys.exit(2)


if __name__ == '__main__':
    main()

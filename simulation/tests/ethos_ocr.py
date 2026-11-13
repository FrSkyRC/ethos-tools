"""Texts of a screenshot, with their position, by OCR (RapidOCR): the tests find what
to touch by its text, on any display size, without coordinates.

    pip install rapidocr onnxruntime
"""

import difflib
import re

_engine = None


class ScreenText:
    def __init__(self, text, box, score):
        self.text = text
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        self.x, self.y = int(min(xs)), int(min(ys))
        self.w, self.h = int(max(xs)) - self.x, int(max(ys)) - self.y
        self.score = score

    @property
    def center(self):
        return self.x + self.w // 2, self.y + self.h // 2

    def __repr__(self):
        return f'{self.text!r} at {self.x},{self.y} {self.w}x{self.h}'


def engine():
    global _engine  # pylint: disable=global-statement
    if _engine is None:
        try:
            from rapidocr import RapidOCR  # pylint: disable=import-outside-toplevel
        except ImportError as e:
            raise ImportError('The tests read the screen by OCR: python -m pip install rapidocr onnxruntime') from e
        # No angle classifier: the texts of the radio are never rotated, and it turns "09" into "60"
        _engine = RapidOCR(params={'Global.log_level': 'error', 'Global.use_cls': False})
    return _engine


def load(png_path):
    """A screenshot as an image (BGR array)."""
    import cv2  # pylint: disable=import-outside-toplevel
    import numpy as np  # pylint: disable=import-outside-toplevel
    return cv2.imdecode(np.fromfile(png_path, dtype=np.uint8), cv2.IMREAD_COLOR)


def read(png_path, box=None):
    """The texts of a screenshot, from top to bottom and left to right. With box (x, y, w, h),
    only the texts of this part of it (faster), at their position on the screen."""
    x, y = 0, 0
    image = png_path
    if box is not None:
        x, y, w, h = box
        image = load(png_path)[y:y + h, x:x + w]
    result = engine()(image)
    texts = [ScreenText(t, [(px + x, py + y) for px, py in b], s)
             for t, b, s in zip(result.txts or (), result.boxes if result.boxes is not None else (), result.scores or ())]
    return sorted(texts, key=lambda t: (t.y, t.x))


def page_dots(png_path, top):
    """The page indicator of a screenshot, the dots under the title bar (top: its bottom):
    (pages count, current page index from 0), (1, 0) without indicator. The current page
    is the brightest dot."""
    import cv2  # pylint: disable=import-outside-toplevel
    import numpy as np  # pylint: disable=import-outside-toplevel
    image = cv2.imdecode(np.fromfile(png_path, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    height = image.shape[0]
    band = image[top:min(height, top + max(24, height // 10))]
    _, mask = cv2.threshold(band, 80, 255, cv2.THRESH_BINARY)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    dots = []
    for i in range(1, count):
        x, y, w, h, area = stats[i]
        # a small disc: as wide as high, about pi/4 of its box filled
        if 5 <= w <= 24 and abs(int(w) - int(h)) <= 2 and 0.6 <= area / (w * h) <= 0.95:
            dots.append((x + w / 2, y + h / 2, float(band[labels == i].mean())))
    # The dots of the indicator: on one line, evenly spaced
    rows = {}
    for dot in dots:
        rows.setdefault(round(dot[1] / 4), []).append(dot)
    row = sorted(max(rows.values(), key=len, default=[]))
    if len(row) < 2:
        return 1, 0
    gaps = [b[0] - a[0] for a, b in zip(row, row[1:])]
    if max(gaps) - min(gaps) > 3:
        return 1, 0
    return len(row), max(range(len(row)), key=lambda i: row[i][2])


# The background of the element which has the focus, the frame of the buttons (the default theme)
FOCUS_COLOR = (255, 178, 57)
BUTTON_FRAME_COLOR = (65, 76, 82)


def has_color(png_path, box, color, tolerance=30, ratio=0.1):
    """True when at least ratio of the pixels of box (x, y, w, h) have color (RGB)."""
    import cv2  # pylint: disable=import-outside-toplevel
    import numpy as np  # pylint: disable=import-outside-toplevel
    x, y, w, h = box
    r, g, b = color
    image = load(png_path)[y:y + h, x:x + w]
    mask = cv2.inRange(image, np.array((max(b - tolerance, 0), max(g - tolerance, 0), max(r - tolerance, 0))),
                       np.array((min(b + tolerance, 255), min(g + tolerance, 255), min(r + tolerance, 255))))
    return mask.size > 0 and np.count_nonzero(mask) >= ratio * mask.size


def buttons(png_path, top=0):
    """(x, y, w, h) of the buttons of a screenshot (their frame), below top (the title bar)."""
    import cv2  # pylint: disable=import-outside-toplevel
    import numpy as np  # pylint: disable=import-outside-toplevel
    r, g, b = BUTTON_FRAME_COLOR
    mask = cv2.inRange(load(png_path), np.array((b - 16, g - 16, r - 16)), np.array((b + 16, g + 16, r + 16)))
    mask[:top] = 0
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    # a frame: big enough, and hollow
    return sorted((int(x), int(y), int(w), int(h)) for x, y, w, h, area in (stats[i] for i in range(1, count))
                  if w >= 24 and h >= 24 and area < 0.5 * w * h)


def focused_box(png_path, top=0):
    """(x, y, w, h) of the element which has the focus (the biggest area of the focus color),
    below top (the title bar), or None."""
    import cv2  # pylint: disable=import-outside-toplevel
    import numpy as np  # pylint: disable=import-outside-toplevel
    image = load(png_path)
    r, g, b = FOCUS_COLOR
    mask = cv2.inRange(image, np.array((b - 30, g - 30, r - 30)), np.array((min(b + 30, 255), min(g + 30, 255), 255)))
    mask[:top] = 0
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    boxes = [stats[i] for i in range(1, count) if stats[i][2] >= 16 and stats[i][3] >= 12]
    if not boxes:
        return None
    x, y, w, h, _ = max(boxes, key=lambda s: s[4])
    return int(x), int(y), int(w), int(h)


def mark_touch(png_path, out_path, x, y, label=None, box=None):
    """Writes the screenshot with a red cross where it was touched, and a green frame
    around the text found (box: a ScreenText)."""
    import cv2  # pylint: disable=import-outside-toplevel
    import numpy as np  # pylint: disable=import-outside-toplevel
    image = cv2.imdecode(np.fromfile(png_path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if box is not None:
        cv2.rectangle(image, (box.x - 2, box.y - 2), (box.x + box.w + 2, box.y + box.h + 2), (0, 200, 0), 2)
    size = max(8, image.shape[0] // 40)
    for color, thickness in (((255, 255, 255), 5), ((0, 0, 255), 2)):  # outlined, visible on any background
        cv2.line(image, (x - size, y - size), (x + size, y + size), color, thickness)
        cv2.line(image, (x - size, y + size), (x + size, y - size), color, thickness)
    if label:
        # under the cross, on a dark background
        (w, h), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        left = max(0, min(x - w // 2, image.shape[1] - w - 6))
        top = min(y + size + 6, image.shape[0] - h - baseline - 6)
        cv2.rectangle(image, (left, top), (left + w + 6, top + h + baseline + 6), (40, 40, 40), cv2.FILLED)
        cv2.putText(image, label, (left + 3, top + h + 3), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (80, 80, 255), 2, cv2.LINE_AA)
    cv2.imencode('.png', image)[1].tofile(out_path)
    return out_path


def normalize(text):
    # The OCR knows Chinese too: the "+" of a button can be read as 十
    return re.sub(r'\s+', ' ', text.replace('十', '+')).strip().casefold()


def matches(found, wanted):
    """The same text, OCR errors aside (one letter in a word...)."""
    found, wanted = normalize(found), normalize(wanted)
    if found == wanted or difflib.SequenceMatcher(None, found, wanted).ratio() >= 0.85:
        return True
    # The O read for a 0 (or the opposite) in a short text: "0A" read "OA"
    return len(wanted) <= 4 and found.replace('o', '0') == wanted.replace('o', '0')

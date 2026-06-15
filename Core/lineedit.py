# Desc: Pure line-editor state machine shared by the RPCortex shells
# File: /Core/lineedit.py
# Lang: MicroPython (also CPython-importable), English
# Version: v1.0.0 "Vela"
#
# A self-contained, I/O-FREE editor state machine: buffer, cursor, history
# navigation, ghost completion, and word/char editing — with NO imports and no
# MicroPython-only calls, so it unit-tests under CPython in isolation.
#
# The async shell driver decodes raw terminal bytes into the logical TOKENS
# below, calls feed(token), then renders the editor's state. This is what lets
# the async shell reach feature parity with the proven ~300-line synchronous
# _shell_input WITHOUT copy-pasting it (CLAUDE.md forbids duplicating that
# reader). The sync reader keeps its own inline editor for now and is migrated
# onto this class only once it is hardware-proven.
#
# Tokens the driver feeds (a printable character is fed as its 1-char string):
ENTER     = 'ENTER'       # accept the line
CANCEL    = 'CANCEL'      # Ctrl+C — abandon the line
TAB       = 'TAB'         # accept ghost / complete
BACKSPACE = 'BACKSPACE'   # delete char before cursor
WORD_BACK = 'WORD_BACK'   # delete word before cursor (Ctrl+W / Ctrl+Backspace)
DELETE    = 'DELETE'      # delete char under cursor
WORD_DEL  = 'WORD_DEL'    # delete word after cursor (Ctrl+Del)
LEFT      = 'LEFT'
RIGHT     = 'RIGHT'
WORD_LEFT = 'WORD_LEFT'   # Ctrl+Left
WORD_RIGHT = 'WORD_RIGHT' # Ctrl+Right
HOME      = 'HOME'
END       = 'END'
UP        = 'UP'          # older history
DOWN      = 'DOWN'        # newer history


def default_word_left(buf, cursor):
    """Index at the start of the word left of cursor (skip spaces, then word)."""
    i = cursor
    while i > 0 and buf[i - 1] == ' ':
        i -= 1
    while i > 0 and buf[i - 1] != ' ':
        i -= 1
    return i


def default_word_right(buf, cursor):
    """Index at the end of the word right of cursor (skip spaces, then word)."""
    n = len(buf)
    i = cursor
    while i < n and buf[i] == ' ':
        i += 1
    while i < n and buf[i] != ' ':
        i += 1
    return i


class LineEditor:
    """One editable input line. Create a fresh editor per line (like the sync
    reader creates fresh buf/cursor/hist_pos each call).

      history    shared list of past command strings (read for Up/Down nav; the
                 CALLER appends the accepted line — the editor never mutates it,
                 matching _shell_input).
      completer  callable(str) -> completion suffix (cheap command-name scan);
                 used for ghost text and Tab. Optional.
      word_left / word_right  word-boundary helpers (default ones provided).
    """

    def __init__(self, history=None, completer=None, word_left=None, word_right=None):
        self.history = history if history is not None else []
        self._complete = completer
        self._wl = word_left or default_word_left
        self._wr = word_right or default_word_right
        self.buf = []                       # list of 1-char strings
        self.cursor = 0                     # 0..len(buf)
        self.hist_pos = len(self.history)   # past the end == 'new input'
        self.ghost = ''                     # completion suffix shown right of cursor

    # -- queries ------------------------------------------------------------
    def line(self):
        return ''.join(self.buf)

    def is_empty(self):
        return not self.buf

    # -- internal -----------------------------------------------------------
    def _recompute_ghost(self):
        self.ghost = ''
        if self._complete and self.cursor == len(self.buf):
            s = ''.join(self.buf)
            if s and ' ' not in s:
                try:
                    self.ghost = self._complete(s) or ''
                except Exception:
                    self.ghost = ''

    def _load_hist(self):
        if 0 <= self.hist_pos < len(self.history):
            self.buf = list(self.history[self.hist_pos])
        else:
            self.buf = []
        self.cursor = len(self.buf)
        self.ghost = ''

    # -- the one entry point ------------------------------------------------
    def feed(self, token):
        """Apply one logical key. Returns 'submit', 'cancel', or None."""
        t = token

        if t == ENTER:
            return 'submit'
        if t == CANCEL:
            return 'cancel'

        if t == TAB:
            if self.cursor == len(self.buf):
                suffix = self.ghost
                if not suffix and self._complete:
                    try:
                        suffix = self._complete(self.line()) or ''
                    except Exception:
                        suffix = ''
                for c in suffix:
                    self.buf.insert(self.cursor, c)
                    self.cursor += 1
                self._recompute_ghost()
            return None

        # Printable character (1-char string >= space). Named tokens are all
        # multi-char, so they never match this guard.
        if isinstance(t, str) and len(t) == 1 and ord(t) >= 32:
            self.buf.insert(self.cursor, t)
            self.cursor += 1
            self._recompute_ghost()
            return None

        if t == BACKSPACE:
            if self.cursor > 0:
                del self.buf[self.cursor - 1]
                self.cursor -= 1
            self._recompute_ghost()
        elif t == WORD_BACK:
            start = self._wl(self.buf, self.cursor)
            if start < self.cursor:
                del self.buf[start:self.cursor]
                self.cursor = start
            self._recompute_ghost()
        elif t == DELETE:
            if self.cursor < len(self.buf):
                del self.buf[self.cursor]
            self._recompute_ghost()
        elif t == WORD_DEL:
            end = self._wr(self.buf, self.cursor)
            if end > self.cursor:
                del self.buf[self.cursor:end]
            self._recompute_ghost()
        elif t == LEFT:
            if self.cursor > 0:
                self.cursor -= 1
            self.ghost = ''
        elif t == RIGHT:
            if self.cursor < len(self.buf):
                self.cursor += 1
            self._recompute_ghost()
        elif t == WORD_LEFT:
            self.cursor = self._wl(self.buf, self.cursor)
            self.ghost = ''
        elif t == WORD_RIGHT:
            self.cursor = self._wr(self.buf, self.cursor)
            self._recompute_ghost()
        elif t == HOME:
            self.cursor = 0
            self.ghost = ''
        elif t == END:
            self.cursor = len(self.buf)
            self._recompute_ghost()
        elif t == UP:
            if self.history and self.hist_pos > 0:
                self.hist_pos -= 1
                self._load_hist()
        elif t == DOWN:
            if self.hist_pos < len(self.history) - 1:
                self.hist_pos += 1
                self._load_hist()
            else:
                self.hist_pos = len(self.history)
                self.buf = []
                self.cursor = 0
                self.ghost = ''
        return None

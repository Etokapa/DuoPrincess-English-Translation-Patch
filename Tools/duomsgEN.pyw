"""
Drag-and-drop GUI encoder/decoder for the game's MSG <-> TXT format.

----------------------------------------------------------------------
SETTINGS
----------------------------------------------------------------------

BLOCK_SEPARATOR = '###'

TAG_NEWLINES = True
    If True: the TXT will show visible tags for newlines so 0x000A and 0x8016
    are distinguishable. The encoder will look for these tags before each '\n'.
    If False: both codes decode to plain '\n', and encoding uses DEFAULT_ENCODE_NEWLINE.

NL_TAG_8016 = '<NL_8016>'
NL_TAG_000A = '<NL_000A>'
    The literal tokens written into TXT (when TAG_NEWLINES=True) to mark newline
    codes. The decoder writes them immediately *before* each newline character,
    so visually they appear at the end of the line.

DEFAULT_ENCODE_NEWLINE = 0x8016
    When encoding a TXT line break that does NOT have an explicit tag, this code
    will be used for the newline in the MSG. (0x8016 or 0x000A)

----------------------------------------------------------------------
NOTES
----------------------------------------------------------------------
- All multi-byte integers are big-endian 16-bit.
- MSG structure:
    u16 file_size_bytes
    u16 block_count
    (block_count times) { u16 offset_in_code_units, u16 length_in_code_units }
    text_area: concatenation of 16-bit code units across all blocks
- Text encoding rules per code unit:
    * 0x000A or 0x8016 => newline
    * >= 0x0080 => subtract 0x80 and interpret as character
    * otherwise interpret as ASCII code
"""

from __future__ import annotations

# ------------------------------
# SETTINGS
# ------------------------------
BLOCK_SEPARATOR = '###'
TAG_NEWLINES = True
NL_TAG_8016 = '/n'
NL_TAG_000A = '/nn'
DEFAULT_ENCODE_NEWLINE = 0x8016  # set to 0x000A if you prefer that as default

# ------------------------------
# Imports
# ------------------------------
import os
import sys
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import List, Iterable, Tuple, Optional

# GUI imports
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    # Optional: drag-and-drop support (recommended)
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _HAS_DND = True
except Exception:
    _HAS_DND = False

# ------------------------------
# Core codec
# ------------------------------
NEWLINE_000A = 0x000A
NEWLINE_8016 = 0x8016

@dataclass
class Entry:
    offset: int  # in 16-bit code units, relative to text start
    length: int  # in 16-bit code units


def _decode_units_to_text(units: Iterable[int], tag_newlines: bool = TAG_NEWLINES) -> str:
    """
    Convert 16-bit code units to a Python string using the discovered rules.

    When tag_newlines=True, we append NL_TAG_* immediately before each '\n'.
    Example line end in TXT:  "Hello world<NL_8016>\n"
    """
    out: List[str] = []
    for v in units:
        if v == NEWLINE_000A:
            if tag_newlines:
                out.append(NL_TAG_000A)
            out.append('\n')
        elif v == NEWLINE_8016:
            if tag_newlines:
                out.append(NL_TAG_8016)
            out.append('\n')
        elif v >= 0x80:
            out.append(chr(v - 0x80))
        else:
            out.append(chr(v))
    return ''.join(out)


def _encode_text_to_units(text: str, default_newline_code: int = DEFAULT_ENCODE_NEWLINE) -> List[int]:
    """
    Convert a Python string back into 16-bit code units.

    Behavior for newline:
      - If the text contains "<NL_8016>\\n", that pair becomes code 0x8016
      - If the text contains "<NL_000A>\\n", that pair becomes code 0x000A
      - A bare '\\n' (without a tag immediately before it) becomes default_newline_code

    Lowercase ASCII 'a'..'z' are stored as ord+0x80. Everything else is stored as raw ord().
    """
    units: List[int] = []
    i = 0
    n = len(text)

    while i < n:
        # Tagged newline detection (tag immediately followed by '\n')
        if TAG_NEWLINES and text.startswith(NL_TAG_8016, i) and i + len(NL_TAG_8016) < n and text[i + len(NL_TAG_8016)] == '\n':
            units.append(NEWLINE_8016)
            i += len(NL_TAG_8016) + 1
            continue
        if TAG_NEWLINES and text.startswith(NL_TAG_000A, i) and i + len(NL_TAG_000A) < n and text[i + len(NL_TAG_000A)] == '\n':
            units.append(NEWLINE_000A)
            i += len(NL_TAG_000A) + 1
            continue

        ch = text[i]
        if ch == '\n':
            units.append(default_newline_code)
            i += 1
            continue

        oc = ord(ch)
        if 'a' <= ch <= 'z':
            units.append(oc + 0x80)
        else:
            units.append(oc)
        i += 1

    return units


def decode_msg_to_blocks(msg_path: Path, tag_newlines: bool = TAG_NEWLINES) -> List[str]:
    """
    Read a .msg file and return a list of decoded text blocks (strings).
    """
    data = msg_path.read_bytes()
    if len(data) < 4:
        raise ValueError("File too small to be a valid MSG")

    file_size_be, count_be = struct.unpack(">HH", data[:4])
    count = count_be

    entries: List[Entry] = []
    off = 4
    for _ in range(count):
        if off + 4 > len(data):
            raise ValueError("Truncated block table")
        o, l = struct.unpack(">HH", data[off:off + 4])
        entries.append(Entry(o, l))
        off += 4

    text_start = off
    blocks: List[str] = []
    for e in entries:
        start = text_start + e.offset * 2
        end = text_start + (e.offset + e.length) * 2
        if not (text_start <= start <= end <= len(data)):
            raise ValueError("Invalid block offset/length")
        raw = data[start:end]
        units = list(struct.unpack(f'>{len(raw)//2}H', raw)) if raw else []
        blocks.append(_decode_units_to_text(units, tag_newlines=tag_newlines))
    return blocks


def encode_blocks_to_msg(blocks: List[str], default_newline_code: int = DEFAULT_ENCODE_NEWLINE) -> bytes:
    """
    Encode a list of text blocks into the .msg binary format.
    """
    all_units: List[int] = []
    entries: List[Entry] = []
    cur_off = 0
    for b in blocks:
        units = _encode_text_to_units(b, default_newline_code=default_newline_code)
        entries.append(Entry(cur_off, len(units)))
        all_units.extend(units)
        cur_off += len(units)

    text_bytes = struct.pack(f'>{len(all_units)}H', *all_units) if all_units else b''
    count = len(entries)
    header_table = b''.join(struct.pack('>HH', e.offset, e.length) for e in entries)
    full_size = 4 + len(header_table) + len(text_bytes)
    header = struct.pack('>HH', full_size, count)
    return header + header_table + text_bytes


def split_txt_into_blocks(txt_text: str) -> List[str]:
    """
    Split a TXT file into blocks separated by a line that's exactly BLOCK_SEPARATOR.
    Preserves all content (including tagged newlines) within blocks.
    """
    parts: List[str] = []
    cur: List[str] = []
    for line in txt_text.splitlines():
        if line.strip() == BLOCK_SEPARATOR:
            parts.append('\n'.join(cur))
            cur = []
        else:
            cur.append(line)
    if cur or not parts:
        parts.append('\n'.join(cur))
    # Drop trailing empty block if it came from a final separator
    if parts and parts[-1] == '' and len(parts) > 1:
        parts = parts[:-1]
    return parts


def make_txt_from_blocks(blocks: List[str]) -> str:
    """
    Join blocks back to a TXT string with the BLOCK_SEPARATOR on its own line between blocks.
    """
    return ('\n' + BLOCK_SEPARATOR + '\n').join(b.rstrip('\n') for b in blocks) + f'\n{BLOCK_SEPARATOR}\n'


# ------------------------------
# File I/O helpers
# ------------------------------
def decode_msg_file_to_txt_path(msg_path: Path) -> Path:
    blocks = decode_msg_to_blocks(msg_path, tag_newlines=TAG_NEWLINES)
    txt_text = make_txt_from_blocks(blocks)

    out = msg_path.with_suffix('.txt')
    if out.exists():
        out = msg_path.with_name(msg_path.stem + '.txt')
    out.write_text(txt_text, encoding='utf-8')
    return out


def encode_txt_file_to_msg_path(txt_path: Path) -> Path:
    txt_text = txt_path.read_text(encoding='utf-8')
    blocks = split_txt_into_blocks(txt_text)
    msg_bytes = encode_blocks_to_msg(blocks, default_newline_code=DEFAULT_ENCODE_NEWLINE)

    out = txt_path.with_suffix('.bin')
    if out.exists():
        out = txt_path.with_name(txt_path.stem + '.bin')
    out.write_bytes(msg_bytes)
    return out


# ------------------------------
# GUI
# ------------------------------
DARK_BG = '#121212'
DARK_FG = '#E6E6E6'
DARK_ACCENT = '#3D7BFD'
DARK_SUBTLE = '#1E1E1E'
DARK_BORDER = '#2A2A2A'


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("MSG <-> TXT Converter")
        self.root.geometry('720x360')
        self.root.minsize(560, 360)

        self._apply_dark_theme()

        self.container = ttk.Frame(root, padding=16)
        self.container.pack(fill='both', expand=True)

        self.drop_area = tk.Label(
            self.container,
            text=("Drop .MSG or .TXT files here\n\n"),
            bg=DARK_SUBTLE, fg=DARK_FG, bd=1, relief='solid', highlightthickness=0,
            padx=24, pady=24, font=('Segoe UI', 12), justify='center'
        )
        self.drop_area.pack(fill='both', expand=True)

        self.drop_area.bind('<Button-1>', self._browse_files)

        if _HAS_DND and isinstance(self.root, TkinterDnD.Tk):
            self.drop_area.drop_target_register(DND_FILES)
            self.drop_area.dnd_bind('<<Drop>>', self._on_drop)
        else:
            # No DnD available
            pass

        # Status log
        self.log = tk.Text(self.container, height=8, bg=DARK_BG, fg=DARK_FG,
                           insertbackground=DARK_FG, relief='flat')
        self.log.pack(fill='x', expand=False, pady=(12, 0))
        self._log("Ready.")
        if not _HAS_DND:
            self._log("Drag-and-drop not available. Install: pip install tkinterdnd2")

    def _apply_dark_theme(self):
        try:
            style = ttk.Style(self.root)
            if 'clam' in style.theme_names():
                style.theme_use('clam')
            style.configure('.', background=DARK_BG, foreground=DARK_FG)
            style.configure('TFrame', background=DARK_BG)
            style.configure('TLabel', background=DARK_BG, foreground=DARK_FG)
            style.configure('TButton', background=DARK_SUBTLE, foreground=DARK_FG, bordercolor=DARK_BORDER)
            style.map('TButton', background=[('active', DARK_SUBTLE)], relief=[('pressed', 'sunken')])
            self.root.configure(bg=DARK_BG)
        except Exception:
            # Fallback
            self.root.configure(bg=DARK_BG)

    def _log(self, msg: str):
        self.log.insert('end', msg + '\n')
        self.log.see('end')

    def _browse_files(self, _event=None):
        paths = filedialog.askopenfilenames(
            title="Choose MSG or TXT files",
            filetypes=[("MSG/TXT", "*.msg *.txt"), ("MSG", "*.msg"), ("TXT", "*.txt"), ("All", "*.*")]
        )
        if not paths:
            return
        for p in self._normalize_paths_string(' '.join(paths)):
            self._handle_path(Path(p))

    def _on_drop(self, event):
        for p in self._normalize_paths_string(event.data):
            self._handle_path(Path(p))

    def _normalize_paths_string(self, data: str) -> List[str]:
        """
        Convert a DND_FILES (or file dialog) string to a list of paths.
        tkinterdnd2 (Tk) can give space-separated paths, possibly wrapped in braces.
        """
        # Use Tk's built-in splitlist if available to correctly parse brace-wrapped paths
        try:
            parts = list(self.root.tk.splitlist(data))
        except Exception:
            parts = data.split()
        return parts

    def _handle_path(self, path: Path):
        try:
            if not path.exists():
                self._log(f"Skipping (not found): {path}")
                return
            ext = path.suffix.lower()
            if ext == '.msg':
                self._log(f"Decoding MSG → TXT: {path}")
                out = decode_msg_file_to_txt_path(path)
                self._log(f"  ✓ Wrote: {out}")
            elif ext == '.txt':
                self._log(f"Encoding TXT → MSG: {path}")
                out = encode_txt_file_to_msg_path(path)
                self._log(f"  ✓ Wrote: {out}")
            else:
                self._log(f"Skipping (unsupported extension): {path}")
        except Exception as e:
            self._log(f"  ✗ Error processing {path.name}: {e}")

def main():
    if _HAS_DND:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    App(root)
    root.mainloop()

if __name__ == '__main__':
    main()
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Drag-and-drop GUI encoder/decoder for the game's MSG <-> TXT format,
now with per-file Japanese glyph mapping support.

HOW IT WORKS (quick):
- English: Same rules as before (lowercase ASCII stored as ord+0x80, others raw).
- Japanese: The .MSG stores small 16-bit *indices* (e.g., 269, 272...) that map
  to kanji/kana. Those indices *change per file*. We learn a mapping by aligning
  a .MSG with its matching .TXT (block-by-block), then save it as `basename.map.json`.
  When a mapping exists, decoding and encoding are lossless.

USAGE:
- Drop a .MSG: if a sibling .TXT exists (same stem), we auto-train and decode.
  Otherwise, we decode using whatever mapping exists; missing codes show as <U+XXXX>.
- Drop a .TXT: we encode, using the sibling map if present; if not present but a
  sibling .MSG exists, we can auto-train first.
- Menu -> Tools -> Train mapping… lets you pick a pair manually.

NEWLINES:
- We can *tag* newline codes to distinguish 0x000A vs 0x8016 in TXT output.
- The encoder recognizes those tags right before '\n' and emits the matching code.

----------------------------------------------------------------------
USER-TWEAKABLE SETTINGS
----------------------------------------------------------------------

# Block separator line in TXT:
BLOCK_SEPARATOR = '###'

# Show newline code-tags (so 0x000A vs 0x8016 become distinguishable)?
TAG_NEWLINES = True
NL_TAG_8016 = '<NL_8016>'
NL_TAG_000A = '<NL_000A>'

# Default newline when TXT has a bare '\n' with no tag:
DEFAULT_ENCODE_NEWLINE = 0x8016   # choose 0x000A or 0x8016

# Auto-learn per-file Japanese mapping if both MSG and TXT are present?
AUTO_TRAIN_JP = True

# Where to store per-file mapping (next to the files):
MAP_FILE_SUFFIX = '.map.json'

# Fallback behavior for unknown glyphs during encode:
#   'error' -> stop with a clear error
#   'placeholder' -> write <UNK> code units (0x003F '?') or ord() as-is
#   'skip' -> skip unknown chars
ENCODE_UNKNOWN_POLICY = 'error'   # recommended: 'error'

----------------------------------------------------------------------

MSG FORMAT (16-bit big-endian):
  u16 file_size_bytes
  u16 block_count
  (block_count times) { u16 offset_in_code_units, u16 length_in_code_units }
  text area: concatenated 16-bit code units for all blocks

CODE UNITS:
  - Newline: 0x000A or 0x8016 (both mean '\n' when decoding)
  - English (as before): lowercase 'a'..'z' stored as ord+0x80; everything else
    (digits, spaces, punctuation, uppercase) stored as raw ord()
  - Japanese: per-file index table (learned), mapping small integers -> Unicode chars
"""

from __future__ import annotations

# ------------------------------
# SETTINGS
# ------------------------------
BLOCK_SEPARATOR = '###'
TAG_NEWLINES = True
NL_TAG_8016 = '<NL_8016>'
NL_TAG_000A = '<NL_000A>'
DEFAULT_ENCODE_NEWLINE = 0x8016
AUTO_TRAIN_JP = True
MAP_FILE_SUFFIX = '.map.json'
ENCODE_UNKNOWN_POLICY = 'error'   # 'error' | 'placeholder' | 'skip'

# ------------------------------
# Imports
# ------------------------------
import os
import sys
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Tuple, Iterable, Optional

# GUI
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _HAS_DND = True
except Exception:
    _HAS_DND = False

# ------------------------------
# Core constants & helpers
# ------------------------------
NEWLINE_000A = 0x000A
NEWLINE_8016 = 0x8016

DARK_BG = '#121212'
DARK_FG = '#E6E6E6'
DARK_ACCENT = '#3D7BFD'
DARK_SUBTLE = '#1E1E1E'
DARK_BORDER = '#2A2A2A'


@dataclass
class Entry:
    offset: int  # in 16-bit code units, relative to text start
    length: int  # in 16-bit code units


def _is_ascii_printable(u: int) -> bool:
    return 0x20 <= u <= 0x7E or u == 0x09


def _decode_units_english(units: Iterable[int], tag_newlines: bool) -> str:
    """
    English rules only (for ASCII text):
      - newline: 0x000A or 0x8016
      - lowercase letters 'a'..'z' saved as ord+0x80
      - others stored raw
    """
    out: List[str] = []
    for v in units:
        if v == NEWLINE_000A:
            if tag_newlines: out.append(NL_TAG_000A)
            out.append('\n')
        elif v == NEWLINE_8016:
            if tag_newlines: out.append(NL_TAG_8016)
            out.append('\n')
        else:
            if v >= 0x80 and _is_ascii_printable(v - 0x80):
                out.append(chr(v - 0x80))
            else:
                out.append(chr(v))
    return ''.join(out)


# ------------------------------
# Per-file Japanese mapping I/O
# ------------------------------
def map_path_for(file: Path) -> Path:
    return file.with_suffix(MAP_FILE_SUFFIX)


def load_mapping_if_any(file: Path) -> Optional[Dict[str, Dict[str, int]]]:
    p = map_path_for(file)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            return None
    return None


def save_mapping(file: Path, unit_to_char: Dict[int, str], char_to_unit: Dict[str, int],
                 source_msg: Path, source_txt: Path) -> Path:
    data = {
        "unit_to_char": {str(k): v for k, v in unit_to_char.items()},
        "char_to_unit": char_to_unit,
        "source": {"msg": str(source_msg), "txt": str(source_txt)},
        "count": len(unit_to_char)
    }
    p = map_path_for(file)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return p


# ------------------------------
# TXT <-> blocks
# ------------------------------
def split_txt_into_blocks(txt_text: str) -> List[str]:
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
    if parts and parts[-1] == '' and len(parts) > 1:
        parts = parts[:-1]
    return parts


def make_txt_from_blocks(blocks: List[str]) -> str:
    return ('\n' + BLOCK_SEPARATOR + '\n').join(b.rstrip('\n') for b in blocks) + f'\n{BLOCK_SEPARATOR}\n'


# ------------------------------
# MSG parsing
# ------------------------------
def read_msg_entries_and_textstart(data: bytes) -> Tuple[List[Entry], int]:
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
    return entries, off


def decode_msg_to_blocks_english(msg_path: Path, tag_newlines: bool) -> List[str]:
    data = msg_path.read_bytes()
    entries, text_start = read_msg_entries_and_textstart(data)
    blocks: List[str] = []
    for e in entries:
        start = text_start + e.offset * 2
        end = text_start + (e.offset + e.length) * 2
        raw = data[start:end]
        units = list(struct.unpack(f'>{len(raw)//2}H', raw)) if raw else []
        blocks.append(_decode_units_english(units, tag_newlines=tag_newlines))
    return blocks


# ------------------------------
# Japanese mapping trainer
# ------------------------------
def _strip_nl_tags_keep_newline(s: str) -> str:
    return s.replace(NL_TAG_8016, '').replace(NL_TAG_000A, '')


def _read_all_blocks_units(msg_path: Path) -> List[List[int]]:
    data = msg_path.read_bytes()
    entries, text_start = read_msg_entries_and_textstart(data)
    result: List[List[int]] = []
    for e in entries:
        start = text_start + e.offset * 2
        end = text_start + (e.offset + e.length) * 2
        raw = data[start:end]
        units = list(struct.unpack(f'>{len(raw)//2}H', raw)) if raw else []
        result.append(units)
    return result


def learn_mapping_from_pair(msg_path: Path, txt_path: Path) -> Tuple[Dict[int, str], Dict[str, int]]:
    """
    Learn a per-file mapping by aligning units (excluding newline codes) to the
    characters of the TXT (excluding newline tags but keeping '\n').
    Assumes the block counts match (as in your sample pairs).
    """
    msg_blocks_units = _read_all_blocks_units(msg_path)
    txt_blocks = split_txt_into_blocks(txt_path.read_text(encoding='utf-8'))
    if len(msg_blocks_units) != len(txt_blocks):
        raise ValueError("MSG/TXT block counts differ; cannot train mapping.")

    from collections import defaultdict, Counter
    counts: Dict[int, Counter] = defaultdict(Counter)

    for ub, tb in zip(msg_blocks_units, txt_blocks):
        ref = _strip_nl_tags_keep_newline(tb)
        j = 0
        for u in ub:
            if u in (NEWLINE_000A, NEWLINE_8016):
                if j < len(ref) and ref[j] == '\n':
                    j += 1
                continue
            # advance to next non-newline char on the TXT side
            while j < len(ref) and ref[j] == '\n':
                j += 1
            if j >= len(ref):
                break
            ch = ref[j]
            counts[u][ch] += 1
            j += 1

    # Choose the most frequent character per unit index
    unit_to_char: Dict[int, str] = {}
    for u, ctr in counts.items():
        ch, _ = ctr.most_common(1)[0]
        unit_to_char[u] = ch

    # Build preferred reverse map (char -> most frequent unit)
    from collections import defaultdict
    rev_counts: Dict[str, Counter] = defaultdict(Counter)
    for u, ctr in counts.items():
        for ch, n in ctr.items():
            rev_counts[ch][u] += n
    char_to_unit: Dict[str, int] = {ch: c.most_common(1)[0][0] for ch, c in rev_counts.items()}

    return unit_to_char, char_to_unit


# ------------------------------
# Decode/encode using a mapping
# ------------------------------
def decode_msg_to_blocks_with_map(msg_path: Path, unit_to_char: Dict[int, str],
                                  tag_newlines: bool) -> List[str]:
    data = msg_path.read_bytes()
    entries, text_start = read_msg_entries_and_textstart(data)
    blocks: List[str] = []
    for e in entries:
        start = text_start + e.offset * 2
        end = text_start + (e.offset + e.length) * 2
        raw = data[start:end]
        units = list(struct.unpack(f'>{len(raw)//2}H', raw)) if raw else []
        s: List[str] = []
        for u in units:
            if u == NEWLINE_000A:
                if tag_newlines: s.append(NL_TAG_000A)
                s.append('\n')
            elif u == NEWLINE_8016:
                if tag_newlines: s.append(NL_TAG_8016)
                s.append('\n')
            else:
                if u in unit_to_char:
                    s.append(unit_to_char[u])
                else:
                    # English fallback
                    if u >= 0x80 and _is_ascii_printable(u - 0x80):
                        s.append(chr(u - 0x80))
                    else:
                        s.append(f'<U+{u:04X}>')  # visible placeholder
        blocks.append(''.join(s))
    return blocks


def encode_blocks_with_map(blocks: List[str], char_to_unit: Dict[str, int],
                           default_newline: int = DEFAULT_ENCODE_NEWLINE) -> bytes:
    all_units: List[int] = []
    entries: List[Entry] = []
    for block in blocks:
        units: List[int] = []
        i = 0
        while i < len(block):
            if TAG_NEWLINES and block.startswith(NL_TAG_8016, i) and i + len(NL_TAG_8016) < len(block) and block[i + len(NL_TAG_8016)] == '\n':
                units.append(NEWLINE_8016); i += len(NL_TAG_8016) + 1; continue
            if TAG_NEWLINES and block.startswith(NL_TAG_000A, i) and i + len(NL_TAG_000A) < len(block) and block[i + len(NL_TAG_000A)] == '\n':
                units.append(NEWLINE_000A); i += len(NL_TAG_000A) + 1; continue

            ch = block[i]
            if ch == '\n':
                units.append(default_newline); i += 1; continue

            # Prefer JP per-file map
            if ch in char_to_unit:
                units.append(char_to_unit[ch]); i += 1; continue

            # English fallback
            if 'a' <= ch <= 'z':
                units.append(ord(ch) + 0x80); i += 1; continue

            # Last resort: store raw ord (may not be valid for the game without a map)
            if ENCODE_UNKNOWN_POLICY == 'error':
                raise ValueError(f"Unknown glyph for encoding (no mapping): {repr(ch)}")
            elif ENCODE_UNKNOWN_POLICY == 'placeholder':
                units.append(ord('?')); i += 1; continue
            elif ENCODE_UNKNOWN_POLICY == 'skip':
                i += 1; continue
            else:
                units.append(ord(ch)); i += 1; continue

        entries.append(Entry(offset=len(all_units), length=len(units)))
        all_units.extend(units)

    text_bytes = struct.pack(f'>{len(all_units)}H', *all_units) if all_units else b''
    header_table = b''.join(struct.pack('>HH', e.offset, e.length) for e in entries)
    full_size = 4 + len(header_table) + len(text_bytes)
    header = struct.pack('>HH', full_size, len(entries))
    return header + header_table + text_bytes


# ------------------------------
# File-level helpers (decode/encode with auto-map)
# ------------------------------
def decode_msg_file_to_txt_path(msg_path: Path) -> Path:
    # Try to load per-file mapping (if any)
    mapping = load_mapping_if_any(msg_path)
    if mapping:
        unit_to_char = {int(k): v for k, v in mapping.get("unit_to_char", {}).items()}
        blocks = decode_msg_to_blocks_with_map(msg_path, unit_to_char, tag_newlines=TAG_NEWLINES)
    else:
        # English (or raw) fallback
        blocks = decode_msg_to_blocks_english(msg_path, tag_newlines=TAG_NEWLINES)

    txt_text = make_txt_from_blocks(blocks)
    out = msg_path.with_suffix('.txt')
    if out.exists():
        out = msg_path.with_name(msg_path.stem + '.decoded.txt')
    out.write_text(txt_text, encoding='utf-8')
    return out


def encode_txt_file_to_msg_path(txt_path: Path) -> Path:
    blocks = split_txt_into_blocks(txt_path.read_text(encoding='utf-8'))

    # Prefer mapping from a sibling MSG (same stem) or from map file
    stem = txt_path.stem
    candidate_msg = txt_path.with_suffix('.MSG')
    chosen_map = None
    if map_path_for(txt_path).exists():
        chosen_map = load_mapping_if_any(txt_path)
    elif candidate_msg.exists() and map_path_for(candidate_msg).exists():
        chosen_map = load_mapping_if_any(candidate_msg)

    if chosen_map:
        char_to_unit = chosen_map.get("char_to_unit", {})
        msg_bytes = encode_blocks_with_map(blocks, char_to_unit, default_newline=DEFAULT_ENCODE_NEWLINE)
    else:
        # English-only encode (no JP mapping available)
        # -> Use English rules only
        all_units: List[int] = []
        entries: List[Entry] = []
        for block in blocks:
            units: List[int] = []
            i = 0
            while i < len(block):
                if TAG_NEWLINES and block.startswith(NL_TAG_8016, i) and i + len(NL_TAG_8016) < len(block) and block[i + len(NL_TAG_8016)] == '\n':
                    units.append(NEWLINE_8016); i += len(NL_TAG_8016) + 1; continue
                if TAG_NEWLINES and block.startswith(NL_TAG_000A, i) and i + len(NL_TAG_000A) < len(block) and block[i + len(NL_TAG_000A)] == '\n':
                    units.append(NEWLINE_000A); i += len(NL_TAG_000A) + 1; continue
                ch = block[i]
                if ch == '\n':
                    units.append(DEFAULT_ENCODE_NEWLINE); i += 1; continue
                if 'a' <= ch <= 'z':
                    units.append(ord(ch) + 0x80)
                else:
                    units.append(ord(ch))
                i += 1
            entries.append(Entry(offset=len(all_units), length=len(units)))
            all_units.extend(units)
        text_bytes = struct.pack(f'>{len(all_units)}H', *all_units) if all_units else b''
        header_table = b''.join(struct.pack('>HH', e.offset, e.length) for e in entries)
        full_size = 4 + len(header_table) + len(text_bytes)
        header = struct.pack('>HH', full_size, len(entries))
        msg_bytes = header + header_table + text_bytes

    out = txt_path.with_suffix('.MSG')
    if out.exists():
        out = txt_path.with_name(txt_path.stem + '.encoded.MSG')
    out.write_bytes(msg_bytes)
    return out


def auto_train_if_possible(path: Path, log_fn) -> Optional[Path]:
    """
    If we have both MSG and TXT for the same stem and no mapping yet,
    auto-train and write map JSON. Returns the map path if trained.
    """
    if not AUTO_TRAIN_JP:
        return None
    if path.suffix.lower() == '.msg':
        msg = path
        txt = path.with_suffix('.txt')
    elif path.suffix.lower() == '.txt':
        txt = path
        msg = path.with_suffix('.MSG')
    else:
        return None

    if not msg.exists() or not txt.exists():
        return None

    map_p = map_path_for(msg)
    if map_p.exists():
        return map_p

    try:
        unit_to_char, char_to_unit = learn_mapping_from_pair(msg, txt)
        save_mapping(msg, unit_to_char, char_to_unit, msg, txt)
        log_fn(f"  ✓ Trained per-file JP mapping -> {map_p.name} (chars: {len(char_to_unit)})")
        return map_p
    except Exception as e:
        log_fn(f"  ✗ Mapping training failed: {e}")
        return None


# ------------------------------
# GUI
# ------------------------------
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("MSG <-> TXT Converter (JP-enabled)")
        self.root.geometry('780x520')
        self.root.minsize(620, 420)
        self._apply_dark_theme()

        # Menu
        menubar = tk.Menu(self.root, tearoff=False)
        tools = tk.Menu(menubar, tearoff=False)
        tools.add_command(label="Train mapping…", command=self._menu_train_mapping)
        tools.add_separator()
        tools.add_command(label="Open map file…", command=self._open_map_file)
        menubar.add_cascade(label="Tools", menu=tools)
        self.root.config(menu=menubar)

        # Main
        self.container = ttk.Frame(root, padding=16)
        self.container.pack(fill='both', expand=True)

        self.drop_area = tk.Label(
            self.container,
            text=("Drop .MSG or .TXT files here\n\n"
                  "• Drop MSG → TXT (auto-trains JP map if TXT present)\n"
                  "• Drop TXT → MSG (uses per-file map if present)\n\n"
                  "Tip: install 'tkinterdnd2' for drag-and-drop (pip install tkinterdnd2)\n"
                  "You can also click this area to choose files"),
            bg=DARK_SUBTLE, fg=DARK_FG, bd=1, relief='solid', highlightthickness=0,
            padx=24, pady=24, font=('Segoe UI', 12), justify='center'
        )
        self.drop_area.pack(fill='both', expand=True)

        self.drop_area.bind('<Button-1>', self._browse_files)

        if _HAS_DND and isinstance(self.root, TkinterDnD.Tk):
            self.drop_area.drop_target_register(DND_FILES)
            self.drop_area.dnd_bind('<<Drop>>', self._on_drop)

        # Status/log
        self.log = tk.Text(self.container, height=10, bg=DARK_BG, fg=DARK_FG,
                           insertbackground=DARK_FG, relief='flat')
        self.log.pack(fill='x', expand=False, pady=(12, 0))
        self._log("Ready.")
        if not _HAS_DND:
            self._log("Drag-and-drop not available. Install: pip install tkinterdnd2")

        hint = tk.Label(self.container,
                        text="Settings are at the top (BLOCK_SEPARATOR, TAG_NEWLINES, NL_TAG_*, DEFAULT_ENCODE_NEWLINE, AUTO_TRAIN_JP, ENCODE_UNKNOWN_POLICY).",
                        bg=DARK_BG, fg='#bbbbbb', anchor='w')
        hint.pack(fill='x', pady=(6, 0))

    def _apply_dark_theme(self):
        try:
            style = ttk.Style(self.root)
            if 'clam' in style.theme_names():
                style.theme_use('clam')
            style.configure('.', background=DARK_BG, foreground=DARK_FG)
            style.configure('TFrame', background=DARK_BG)
            style.configure('TLabel', background=DARK_FG)
            style.configure('TButton', background=DARK_SUBTLE, foreground=DARK_FG, bordercolor=DARK_BORDER)
            style.map('TButton', background=[('active', DARK_SUBTLE)], relief=[('pressed', 'sunken')])
            self.root.configure(bg=DARK_BG)
        except Exception:
            self.root.configure(bg=DARK_BG)

    def _log(self, msg: str):
        self.log.insert('end', msg + '\n')
        self.log.see('end')

    # ---- Menus ----
    def _menu_train_mapping(self):
        msg = filedialog.askopenfilename(title="Pick MSG", filetypes=[("MSG", "*.msg *.MSG")])
        if not msg: return
        txt = filedialog.askopenfilename(title="Pick matching TXT", filetypes=[("TXT", "*.txt")])
        if not txt: return
        msgp, txtp = Path(msg), Path(txt)
        try:
            unit_to_char, char_to_unit = learn_mapping_from_pair(msgp, txtp)
            mp = save_mapping(msgp, unit_to_char, char_to_unit, msgp, txtp)
            self._log(f"✓ Mapping trained and saved: {mp}")
        except Exception as e:
            messagebox.showerror("Training failed", str(e))
            self._log(f"✗ Training failed: {e}")

    def _open_map_file(self):
        p = filedialog.askopenfilename(title="Open map file", filetypes=[("Mapping JSON", f"*{MAP_FILE_SUFFIX}")])
        if not p: return
        try:
            data = json.loads(Path(p).read_text(encoding='utf-8'))
            info = f"Map {Path(p).name}\nCharacters: {len(data.get('char_to_unit', {}))}\nUnits: {len(data.get('unit_to_char', {}))}\nSource: {data.get('source')}"
            messagebox.showinfo("Map info", info)
            self._log(info)
        except Exception as e:
            messagebox.showerror("Open failed", str(e))
            self._log(f"✗ Open failed: {e}")

    # ---- DnD / Browse ----
    def _browse_files(self, _event=None):
        paths = filedialog.askopenfilenames(
            title="Choose MSG or TXT files",
            filetypes=[("MSG/TXT", "*.msg *.MSG *.txt"), ("MSG", "*.msg *.MSG"), ("TXT", "*.txt"), ("All", "*.*")]
        )
        if not paths: return
        for p in self._normalize_paths(' '.join(paths)):
            self._handle_path(Path(p))

    def _on_drop(self, event):
        for p in self._normalize_paths(event.data):
            self._handle_path(Path(p))

    def _normalize_paths(self, data: str) -> List[str]:
        try:
            parts = list(self.root.tk.splitlist(data))
        except Exception:
            parts = data.split()
        return parts

    # ---- Core handler ----
    def _handle_path(self, path: Path):
        try:
            if not path.exists():
                self._log(f"Skipping (not found): {path}")
                return
            # Optionally auto-train if both files are present
            auto_train_if_possible(path, self._log)

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
                self._log(f"Skipping (unsupported): {path}")
        except Exception as e:
            self._log(f"  ✗ Error: {e}")


def main():
    if _HAS_DND:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()

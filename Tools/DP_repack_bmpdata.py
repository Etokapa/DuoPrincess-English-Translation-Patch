#!/usr/bin/env python3
import argparse, os, sys
from typing import Dict, List, Tuple

ENTRY_SIZE = 32
NAME_LEN   = 20

# ---------- LZW 12-bit (matches the other game's tool) ----------
def lzw_compress(data: bytes) -> List[int]:
    """12-bit LZW without CLEAR code; codes 0..255 are literals, next IDs start at 257."""
    if not data:
        return []
    out: List[int] = []
    dic: Dict[bytes, int] = {}
    next_id = 257

    prev = bytes([data[0]])
    for b in data[1:]:
        b = bytes([b])
        seq = prev + b
        if seq in dic:
            prev = seq
        else:
            out.append(dic[prev] if len(prev) > 1 else prev[0])
            if next_id < 4096:
                dic[seq] = next_id
                next_id += 1
            prev = b
    out.append(dic[prev] if len(prev) > 1 else prev[0])
    return out

def lzw_write_12bit(codes: List[int]) -> bytes:
    """Pack 12-bit codes into bytes: 3 bytes -> 2 codes pattern (nibble interleave)."""
    phase = True
    buf = 0
    res = bytearray()
    for code in codes:
        if phase:
            res.append((code >> 4) & 0xFF)
            buf = code & 0x0F
        else:
            res.append(((buf << 4) | (code >> 8)) & 0xFF)
            res.append(code & 0xFF)
        phase = not phase
    if len(codes) % 2 != 0:
        res.append((buf << 4) & 0xF0)
    return bytes(res)

# ---------- Archive helpers ----------
def read_template_header(path: str):
    """Parse an existing BMPDATA.BIN header (big-endian)."""
    with open(path, "rb") as f:
        blob = f.read()
    if len(blob) < 4:
        raise ValueError("Template is too small")

    count = int.from_bytes(blob[:4], "big")
    base = 4 + count * ENTRY_SIZE
    if len(blob) < base:
        raise ValueError("Template truncated before data section")

    entries = []
    for i in range(count):
        off = 4 + i * ENTRY_SIZE
        name = blob[off:off+NAME_LEN].split(b"\x00", 1)[0].decode("ascii", "ignore")
        fmt1 = int.from_bytes(blob[off+20:off+22], "big")
        fmt2 = int.from_bytes(blob[off+22:off+24], "big")
        rel  = int.from_bytes(blob[off+24:off+28], "big")
        size = int.from_bytes(blob[off+28:off+32], "big")
        entries.append((name, fmt1, fmt2, rel, size))
    return count, base, entries

def sanitize_name(name: str) -> bytes:
    n = name.encode("ascii", "ignore")
    if len(n) > NAME_LEN:
        raise ValueError(f"Filename '{name}' exceeds {NAME_LEN} bytes in ASCII.")
    return n.ljust(NAME_LEN, b"\x00")

def compute_fmt_words(name: str, data: bytes, template_map=None) -> Tuple[int, int]:
    """Compute (fmt1, fmt2) for this game.
    If template_map has (fmt1,fmt2) for this name, prefer those (recommended)."""
    if template_map and name in template_map:
        return template_map[name]

    lower = name.lower()
    if lower.endswith(".bmp"):
        # For this game, the two words mirror the decompressed BMP file size (split 16/16).
        full = len(data)
        return (full >> 16) & 0xFFFF, full & 0xFFFF

    if lower.endswith(".tga"):
        # TGA width/height from header at 12..15 (little-endian)
        if len(data) < 16:
            raise ValueError(f"TGA '{name}' is too small to read width/height.")
        width  = int.from_bytes(data[12:14], "little")
        height = int.from_bytes(data[14:16], "little")

        # Two encodings seen in the original:
        #  A) fmt1 = width // 16 (when width is a multiple of 16), fmt2 = height
        #  B) fmt1 = 0, fmt2 = ((width & 0xFF) << 8) | (height & 0xFF)  (used by some small images)
        if width % 16 == 0:
            return (width // 16) & 0xFFFF, height & 0xFFFF
        # Fallback for odd widths (rare here); best-effort packing compatible with observed scheme.
        if width < 256 and height < 256:
            return 0, ((width & 0xFF) << 8) | (height & 0xFF)
        # Last resort: approximate the common scheme.
        return (width // 16) & 0xFFFF, height & 0xFFFF

    # Unknown: just store the decompressed size like BMPs (safe default).
    full = len(data)
    return (full >> 16) & 0xFFFF, full & 0xFFFF

def gather_inputs(root: str) -> List[Tuple[str, bytes]]:
    items = []
    for dirpath, _, files in os.walk(root):
        for fn in files:
            path = os.path.join(dirpath, fn)
            # Normalize to archive-friendly path (flat, like the original)
            rel = os.path.relpath(path, root)
            if os.sep != "/":
                rel = rel.replace(os.sep, "/")
            with open(path, "rb") as f:
                data = f.read()
            items.append((rel, data))
    if not items:
        raise ValueError("No files found to pack.")
    return items

def build_header(entries: List[Tuple[str, int, int, int, int]]) -> bytes:
    """entries: list of (name, fmt1, fmt2, rel_off, comp_size)."""
    count = len(entries)
    head = bytearray()
    head += count.to_bytes(4, "big")
    for name, fmt1, fmt2, rel, comp_size in entries:
        head += sanitize_name(name)
        head += fmt1.to_bytes(2, "big")
        head += fmt2.to_bytes(2, "big")
        head += rel.to_bytes(4, "big")
        head += comp_size.to_bytes(4, "big")
    return bytes(head)

def repack(input_dir: str, out_path: str, template_path: str = None, strict: bool = False):
    # Load files to pack
    files = gather_inputs(input_dir)

    # Optionally read a template to (a) fix order and (b) copy fmt words
    template_entries = []
    template_map = {}
    template_order = []
    if template_path:
        count, base, template_entries = read_template_header(template_path)
        template_order = [t[0] for t in template_entries]
        template_map = {t[0]: (t[1], t[2]) for t in template_entries}

    # Decide order
    if template_order:
        # Use template order; missing files => error unless --strict=0
        name_to_data = {n: d for (n, d) in files}
        ordered_files: List[Tuple[str, bytes]] = []
        missing = []
        for n in template_order:
            if n in name_to_data:
                ordered_files.append((n, name_to_data[n]))
            else:
                missing.append(n)
        if missing and strict:
            raise ValueError(f"Missing {len(missing)} file(s) required by template (strict mode): {missing[:5]}...")
        # If not strict, allow missing by packing only present ones (count changes).
        files = ordered_files if ordered_files else files
    else:
        # Deterministic order if no template
        files.sort(key=lambda x: x[0])

    # Compress and collect metadata
    compressed_chunks = []
    meta: List[Tuple[str, int, int, int, int]] = []  # name, fmt1, fmt2, rel_off, comp_size
    for name, data in files:
        codes = lzw_compress(data)
        comp = lzw_write_12bit(codes)
        compressed_chunks.append(comp)
        fmt1, fmt2 = compute_fmt_words(name, data, template_map)
        # rel_off will be filled after we know the header size
        meta.append((name, fmt1, fmt2, 0, len(comp)))

    # Compute offsets
    header_len = 4 + len(meta) * ENTRY_SIZE
    cursor = 0
    fixed_meta = []
    for (name, f1, f2, _rel, csz), comp in zip(meta, compressed_chunks):
        rel = cursor  # Relative to end-of-header
        fixed_meta.append((name, f1, f2, rel, csz))
        cursor += len(comp)

    # Build header
    header = build_header(fixed_meta)

    # Write archive
    with open(out_path, "wb") as outf:
        outf.write(header)
        for comp in compressed_chunks:
            outf.write(comp)

    print(f"Packed {len(fixed_meta)} file(s) into: {out_path}")
    print(f"Header size: {header_len} bytes, Data size: {cursor} bytes, Total: {header_len + cursor} bytes")

def main():
    ap = argparse.ArgumentParser(description="Repacker for this game's BMPDATA.BIN")
    ap.add_argument("input_dir", help="Folder with files to pack (BMP/TGA/etc.)")
    ap.add_argument("output", help="Path to output BMPDATA.BIN")
    ap.add_argument("--template", help="Optional: path to an existing BMPDATA.BIN to copy fmt words and file order")
    ap.add_argument("--strict", action="store_true", help="Require every template entry to be present in input_dir")
    args = ap.parse_args()

    repack(args.input_dir, args.output, args.template, args.strict)

if __name__ == "__main__":
    main()

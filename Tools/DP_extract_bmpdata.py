#!/usr/bin/env python3
import argparse
import os
from typing import List, Tuple

ENTRY_SIZE = 32          # bytes per TOC entry
NAME_LEN   = 20          # bytes, ASCII, NUL-padded

def parse_header(blob: bytes):
    """Parse the archive header.

    Layout:
      u32_be count
      count * 32-byte entries:
        name[20] (ASCII, NUL-padded)
        u16_be fmt1
        u16_be fmt2
        u32_be rel_offset  (from end-of-header)
        u32_be comp_size
    """
    if len(blob) < 4:
        raise ValueError("File too small for header")
    count = int.from_bytes(blob[:4], "big")
    toc_len = 4 + count * ENTRY_SIZE
    if len(blob) < toc_len:
        raise ValueError("File too small for TOC")

    entries = []
    for i in range(count):
        off = 4 + i * ENTRY_SIZE
        name = blob[off:off+NAME_LEN].split(b"\x00", 1)[0].decode("ascii")
        fmt1 = int.from_bytes(blob[off+20:off+22], "big")
        fmt2 = int.from_bytes(blob[off+22:off+24], "big")
        rel_off = int.from_bytes(blob[off+24:off+28], "big")
        size = int.from_bytes(blob[off+28:off+32], "big")
        entries.append((name, fmt1, fmt2, rel_off, size))
    data_base = toc_len
    return count, data_base, entries

def lzw_read_12bit(data: bytes) -> List[int]:
    """Unpack 12-bit codes from bytes: three bytes → two codes.
       Same as Elepaper Action?
    """
    res = []
    phase = 0
    buf = 0
    for b in data:
        if phase == 0:
            buf = b << 4
        elif phase == 1:
            res.append(buf | (b >> 4))
            buf = (b & 0x0F) << 8
        else:
            res.append(buf | b)
        phase = (phase + 1) % 3
    return res

def lzw_decompress_12bit(codes: List[int]) -> bytes:
    """12-bit LZW with CLEAR=256 and code space up to 4095.
       Dictionary indices 257.. map to our dic[0..].
    """
    if not codes:
        return b""
    out = bytearray()
    dic = [None] * 3839  # 4096 - 257 = 3839
    dict_size = 0
    prev_code = codes[0]
    out.append(prev_code)

    def get(idx: int) -> bytes:
        if idx < 256:
            return bytes([idx])
        elif idx - 257 == dict_size:
            # KwKwK special case
            res = get(prev_code)
            return res + bytes([res[0]])
        else:
            return dic[idx - 257]

    for code in codes[1:]:
        if code == 256:  # CLEAR
            dict_size = 0
            continue
        temp = get(code)
        out.extend(temp)
        if dict_size < 3839:
            dic[dict_size] = get(prev_code) + bytes([temp[0]])
            dict_size += 1
        prev_code = code
    return bytes(out)

def extract(bin_path: str, out_dir: str, list_only: bool = False) -> None:
    with open(bin_path, "rb") as f:
        blob = f.read()

    count, base, entries = parse_header(blob)

    # Log header
    print(f'{"#":>3} {"Name":<30} {"Comp":>8} {"->":>2} {"Hdr/Size":>8} {"@":>2} {"Offset":>10}')
    print("-" * 70)

    for idx, (name, fmt1, fmt2, rel_off, comp_size) in enumerate(entries, 1):
        comp = blob[base + rel_off: base + rel_off + comp_size]

        # Peek at post-decompression header dword for the pretty log (no need to fully save if listing)
        hdr_hex = ""
        if not list_only:
            codes = lzw_read_12bit(comp)
            decomp = lzw_decompress_12bit(codes)
            if name.lower().endswith(".bmp") and len(decomp) >= 6 and decomp[:2] == b"BM":
                # BMP: DWORD file size at bytes 2..5 (little-endian)
                hdr_hex = f"{int.from_bytes(decomp[2:6], 'little'):X}"
            elif name.lower().endswith(".tga") and len(decomp) >= 0x10:
                # TGA (uncompressed truecolor often has 0,0,2,0,0, etc.)
                # For consistency with log, show width/height word-pair (little-endian)
                w = int.from_bytes(decomp[12:14], "little") if len(decomp) >= 14 else 0
                h = int.from_bytes(decomp[14:16], "little") if len(decomp) >= 16 else 0
                hdr_hex = f"{(w<<16)|h:X}"
            else:
                # Fallback: show the two fmt words from the TOC
                hdr_hex = f"{(fmt1<<16)|fmt2:X}"

            # Write file
            out_path = os.path.join(out_dir, name)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "wb") as outf:
                outf.write(decomp)
        else:
            hdr_hex = f"{(fmt1<<16)|fmt2:X}"

        print(f"{idx:>3} {name:<30} {comp_size:>8X} -> {hdr_hex:>8} @ 0x{rel_off:>08X}")

def main():
    ap = argparse.ArgumentParser(description="Extractor for BMPDATA.BIN (12-bit LZW).")
    ap.add_argument("bin", help="Path to BMPDATA.BIN")
    ap.add_argument("outdir", help="Directory to write extracted files")
    ap.add_argument("--list", action="store_true", help="List contents without extracting")
    args = ap.parse_args()
    extract(args.bin, args.outdir, list_only=args.list)

if __name__ == "__main__":
    main()
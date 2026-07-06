#!/usr/bin/env python3
"""Verify OHOS lld --code-sign fs-verity Merkle root hash of an ELF binary.

Reimplements lld/ELF/CodeSign.cpp (fixed version) and compares the computed
root hash with the one stored in the .codesign section.
"""
import hashlib
import struct
import sys

CHUNK = 4096
DIGEST = 32


def find_codesign_offset(data):
    # Parse ELF section headers to find .codesign file offset.
    assert data[:4] == b"\x7fELF"
    is64 = data[4] == 2
    assert is64
    e_shoff = struct.unpack_from("<Q", data, 0x28)[0]
    e_shentsize = struct.unpack_from("<H", data, 0x3A)[0]
    e_shnum = struct.unpack_from("<H", data, 0x3C)[0]
    e_shstrndx = struct.unpack_from("<H", data, 0x3E)[0]
    sh = lambda i: data[e_shoff + i * e_shentsize : e_shoff + (i + 1) * e_shentsize]
    strtab_off = struct.unpack_from("<Q", sh(e_shstrndx), 0x18)[0]
    for i in range(e_shnum):
        name_off = struct.unpack_from("<I", sh(i), 0)[0]
        end = data.index(b"\x00", strtab_off + name_off)
        name = data[strtab_off + name_off : end].decode()
        if name == ".codesign":
            return struct.unpack_from("<Q", sh(i), 0x18)[0]
    raise SystemExit("no .codesign section")


def sha256_chunk(buf):
    if len(buf) < CHUNK:
        buf = buf + b"\x00" * (CHUNK - len(buf))
    return hashlib.sha256(buf).digest()


def merkle_root(data, size, cs_offset):
    cs_index = -(-cs_offset // CHUNK)  # ceil, matches getChunkCount(csOffset)
    level = bytearray()
    n = -(-size // CHUNK)
    for i in range(n):
        if i == cs_index:
            level += b"\x00" * DIGEST
        else:
            level += sha256_chunk(data[i * CHUNK : min((i + 1) * CHUNK, size)])
    if size <= CHUNK:
        return bytes(level[:DIGEST])
    # pad level to CHUNK multiple, then hash upward
    while len(level) > CHUNK:
        if len(level) % CHUNK:
            level += b"\x00" * (CHUNK - len(level) % CHUNK)
        nxt = bytearray()
        for i in range(0, len(level), CHUNK):
            nxt += sha256_chunk(level[i : i + CHUNK])
        level = nxt
    if len(level) % CHUNK:
        level += b"\x00" * (CHUNK - len(level) % CHUNK)
    return hashlib.sha256(level[:CHUNK]).digest()


def main(path):
    data = open(path, "rb").read()
    cs_off = find_codesign_offset(data)
    hdr = data[cs_off : cs_off + 0x38]
    stored_size = struct.unpack_from("<Q", hdr, 0x10)[0]
    stored_root = hdr[0x18:0x38]
    print(f"file={path}")
    print(f"  file size      = {len(data):#x}")
    print(f"  .codesign off  = {cs_off:#x}")
    print(f"  signed size    = {stored_size:#x}")
    print(f"  stored root    = {stored_root.hex()}")
    root = merkle_root(data, stored_size, cs_off)
    print(f"  computed root  = {root.hex()}")
    print(f"  MATCH: {root == stored_root}")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        main(p)

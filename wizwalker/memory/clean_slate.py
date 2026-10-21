"""Put the game's code back the way the exe on disk has it.

An earlier hook session can leave jumps and a filled code cave behind (Deimos restarted, an unhook that died halfway), and the next
hook then can't find its cave. This compares the game's .text with the file and puts back only what hooks changed.
"""

import asyncio
import ctypes
import struct

import pymem.process
import regex
from loguru import logger

from wizwalker import MemoryReadError

_CHUNK = 0x100000
_GAP = 8
_MAX_RUNS = 200


def _exe_path(process_handle) -> str:
    buf = ctypes.create_unicode_buffer(1024)
    ctypes.windll.psapi.GetModuleFileNameExW(ctypes.c_void_p(process_handle), None, buf, len(buf))
    return buf.value


def _text_section(data: bytes):
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    count = struct.unpack_from("<H", data, pe + 6)[0]
    optional_size = struct.unpack_from("<H", data, pe + 20)[0]
    table = pe + 24 + optional_size

    for i in range(count):
        name, vsize, va, raw_size, raw_ptr = struct.unpack_from("<8sIIII", data, table + 40 * i)
        if name.rstrip(b"\0") == b".text":
            size = min(vsize, raw_size)
            return va, data[raw_ptr : raw_ptr + size]

    raise RuntimeError("no .text section in the exe")


def _runs(offsets):
    runs = []
    for off in offsets:
        if runs and off - runs[-1][1] <= _GAP:
            runs[-1][1] = off + 1
        else:
            runs.append([off, off + 1])
    return runs


async def find_fixes(text: bytes, read, cave_pattern: bytes, cave_size: int):
    """
    Work out what hooks changed in the live .text

    Args:
        text: The .text section from the exe file
        read: Async function (offset, size) -> live bytes at that .text offset
        cave_pattern: Pattern of the code cave function
        cave_size: Size of the code cave

    Returns:
        (list of (start, end) jump and nop spots, (start, end) of the cave or None)
    """
    # a regex over the whole .text takes a while, keep it off the event loop
    matches = await asyncio.get_event_loop().run_in_executor(
        None, lambda: [m.start() for m in regex.finditer(cave_pattern, text, regex.DOTALL)]
    )
    cave = matches[0] if len(matches) == 1 else None
    cave_end = cave + cave_size if cave is not None else None

    changed = []
    for start in range(0, len(text), _CHUNK):
        original = text[start : start + _CHUNK]
        try:
            live = await read(start, len(original))
        except MemoryReadError:
            continue

        if live != original:
            changed += [start + i for i, (a, b) in enumerate(zip(live, original)) if a != b]

    runs = _runs(changed)
    if len(runs) > _MAX_RUNS:
        logger.warning(f"{len(runs)} changed spots in the game's code, not touching it")
        return [], None

    spots = []
    cave_dirty = False
    for start, end in runs:
        if cave is not None and cave <= start and end <= cave_end:
            cave_dirty = True
            continue

        read_start = max(0, start - 4)
        live = await read(read_start, end - read_start + 4)
        original = text[start:end]
        shift = start - read_start

        fix_start = None
        if cave is not None:
            for p in range(len(live) - 4):
                if live[p] == 0xE9:
                    target = read_start + p + 5 + struct.unpack_from("<i", live, p + 1)[0]
                    if cave <= target < cave_end:
                        fix_start = min(start, read_start + p)
                        break

        nopped_je = live[shift : shift + end - start] == b"\x90" * (end - start) and (
            original[0] == 0x74 or original[:2] == b"\x0F\x84"
        )
        if fix_start is None and nopped_je:
            fix_start = start

        if fix_start is None:
            logger.debug(f"leaving unknown change at .text+{start:#x} alone")
            continue

        spots.append((fix_start, end))

    return spots, (cave, cave_end) if cave_dirty else None


async def restore_original_code(handler) -> int:
    """
    Undo hook patches left in the game's code

    Args:
        handler: The HookHandler of the client

    Returns:
        How many patched spots were put back
    """
    process = handler.process
    base = pymem.process.module_from_name(process.process_handle, "WizardGraphicalClient.exe").lpBaseOfDll

    with open(_exe_path(process.process_handle), "rb") as f:
        text_va, text = _text_section(f.read())

    text_addr = base + text_va

    async def read(offset, size):
        return await handler.read_bytes(text_addr + offset, size)

    spots, cave = await find_fixes(text, read, handler.AUTOBOT_PATTERN, handler.AUTOBOT_SIZE)

    # the jumps go first so nothing new enters the cave, then give a running hook time to leave it
    for start, end in spots:
        await handler.write_bytes(text_addr + start, text[start:end])

    if cave:
        if spots:
            await asyncio.sleep(0.5)
        await handler.write_bytes(text_addr + cave[0], text[cave[0] : cave[1]])

    fixed = len(spots) + int(cave is not None)
    if fixed:
        logger.info(f"Put back {fixed} spots an earlier hook session left in the game's code")

    return fixed

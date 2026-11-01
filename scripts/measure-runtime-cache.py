"""Measure physical file allocation once per NTFS file ID (including hard links)."""
import ctypes
import json
import os
from pathlib import Path
import sys


def measure(directory):
    root = Path(directory).resolve(strict=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCompressedFileSizeW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
    kernel.GetCompressedFileSizeW.restype = ctypes.c_uint32
    seen = set()
    result = {"directory": str(root), "directoryEntryBytes": 0, "uniqueContentBytes": 0,
              "allocatedFileBytes": 0, "filePaths": 0, "uniqueFiles": 0}
    for folder, _, names in os.walk(root, followlinks=False):
        for name in names:
            file = Path(folder) / name
            stat = file.stat()
            result["filePaths"] += 1
            result["directoryEntryBytes"] += stat.st_size
            identity = (stat.st_dev, stat.st_ino)
            if identity in seen:
                continue
            seen.add(identity)
            result["uniqueFiles"] += 1
            result["uniqueContentBytes"] += stat.st_size
            high = ctypes.c_uint32()
            ctypes.set_last_error(0)
            low = kernel.GetCompressedFileSizeW(str(file), ctypes.byref(high))
            if low == 0xFFFFFFFF and ctypes.get_last_error():
                raise ctypes.WinError(ctypes.get_last_error())
            result["allocatedFileBytes"] += (high.value << 32) | low
    return result


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps([measure(path) for path in sys.argv[1:]], ensure_ascii=False, indent=2))

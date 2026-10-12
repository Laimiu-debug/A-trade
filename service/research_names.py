"""Recover display names from validated, compressed snapshots one stock at a time."""
import codecs
import json
import re
import zlib


def snapshot_names(blob):
    def chunks():
        decompressor = zlib.decompressobj()
        decoder = codecs.getincrementaldecoder("utf-8")()
        for at in range(0, len(blob), 8192):
            pending = blob[at:at+8192]
            while pending:
                part = decompressor.decompress(pending, 65536)
                pending = decompressor.unconsumed_tail
                if part:
                    yield decoder.decode(part)
        tail = decoder.decode(decompressor.flush(), final=True)
        if tail:
            yield tail
    # Locate the top-level array with a bounded lexical scan. Nested metadata
    # keys or quoted text cannot masquerade as the source securities list.
    # Decode only one security, then discard its bars.
    parts, buffer = iter(chunks()), ""
    depth, quoted, escaped, token, candidate, target = 0, False, False, None, False, False
    for part in parts:
        found = False
        for at, char in enumerate(part):
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped, token = True, None
                elif char == '"':
                    quoted, candidate = False, token == "securities" and depth == 1
                    token = None
                elif token is not None:
                    token = token + char if len(token) < 11 else None
                continue
            if char.isspace():
                continue
            if char == '"':
                quoted, token = True, "" if depth == 1 else None
            elif char == ":":
                target, candidate = candidate, False
            elif char == "[" and depth == 1 and target:
                buffer, found = part[at+1:], True
                break
            else:
                target, candidate = False, False
                if char in "[{":
                    depth += 1
                elif char in "]}":
                    depth -= 1
        if found:
            break
    else:
        return {}
    decoder, names = json.JSONDecoder(), {}
    while True:
        buffer = buffer.lstrip(" \r\n\t,")
        if buffer.startswith("]"):
            return names
        try:
            item, end = decoder.raw_decode(buffer)
        except ValueError:
            if len(buffer) > 32*1024**2:
                raise ValueError("单条证券名称索引读取超出限制") from None
            try:
                buffer += next(parts)
            except StopIteration:
                raise ValueError("冻结名称索引无法读取") from None
            continue
        if not isinstance(item, dict):
            raise ValueError("冻结证券记录无效")
        symbol, name = item.get("symbol"), item.get("name")
        if isinstance(symbol, str) and re.fullmatch(r"(?:sh|sz|bj)\d{6}", symbol) and isinstance(name, str) and name and name not in (symbol, symbol[2:]):
            names[symbol] = name
        buffer = buffer[end:]

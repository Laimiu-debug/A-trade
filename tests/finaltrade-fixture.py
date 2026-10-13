"""Deterministic TDX files, including real-format float-share metadata."""
import datetime as dt
import json
import math
import struct
from pathlib import Path


def create_fixture(directory):
    directory = Path(directory)
    dates = []
    day = dt.date(2023, 1, 2)
    while len(dates) < 540:
        if day.weekday() < 5:
            dates.append(day)
        day += dt.timedelta(days=1)
    for stock in range(40):
        market = 'sh' if stock < 20 else 'sz'
        code = 600000 + stock if market == 'sh' else stock - 19
        folder = directory / 'market' / 'vipdoc' / market / 'lday'
        folder.mkdir(parents=True, exist_ok=True)
        data = bytearray()
        previous = 30 + stock
        for index, date in enumerate(dates):
            base = 30 + stock + index * (.03 + .003 * (stock % 7))
            close = base * (1 + .045 * math.sin(index / (8 + stock % 9) + stock) + .02 * math.sin(index / 2.7))
            if stock % 5 == 0 and index > 520:
                close *= 1 + .005 * (index - 520)
            if stock % 7 == 0 and index in [533, 537]:
                close = previous * 1.1
            opening = previous * .997
            high = max(opening, close) * 1.013
            low = min(opening, close) * .986
            volume = int(12000000 * (1 + .45 * math.sin(index / 5 + stock)))
            data.extend(struct.pack('<IIIIIfII', int(date.strftime('%Y%m%d')), round(opening * 100), round(high * 100), round(low * 100), round(close * 100), float(close * volume), volume, 0))
            previous = close
        (folder / (market + f'{code:06d}' + '.day')).write_bytes(data)
    # DBF fields SC / GPDM / LTAG, float shares measured in 10,000 shares.
    fields = [('SC', 1), ('GPDM', 6), ('LTAG', 12)]
    header_size = 32 + 32 * len(fields) + 1
    record_size = 1 + sum(length for _, length in fields)
    raw = bytearray(struct.pack('<BBBBIHH20x', 3, 125, 1, 24, 38, header_size, record_size))
    for name, length in fields:
        field = bytearray(32)
        field[:len(name)] = name.encode('ascii')
        field[11] = ord('N') if name == 'LTAG' else ord('C')
        field[16] = length
        raw.extend(field)
    raw.append(13)
    for stock in range(38):
        market = '1' if stock < 20 else '0'
        code = 600000 + stock if stock < 20 else stock - 19
        shares = '10000' if stock < 30 else '100000'
        raw.extend((' ' + market + f'{code:06d}' + shares.rjust(12)).encode('ascii'))
    raw.append(26)
    metadata = directory / 'market' / 'T0002' / 'hq_cache'
    metadata.mkdir(parents=True, exist_ok=True)
    (metadata / 'base.dbf').write_bytes(raw)
    for market in ['sh', 'sz']:
        names = bytearray(50)
        for stock in range(20):
            code = 600000 + stock if market == 'sh' else stock + 1
            record = bytearray(360)
            record[:6] = f'{code:06d}'.encode('ascii')
            name = f'测试股票{stock + (1 if market == "sh" else 21):02d}'.encode('gbk')
            record[31:31 + len(name)] = name
            names.extend(record)
        (metadata / (market + 's.tnf')).write_bytes(names)
    fixture = {'start': dates[-21].isoformat(), 'end': dates[-1].isoformat(), 'symbols': 40, 'barsPerSymbol': 540, 'floatSharesSymbols': 38}
    (directory / 'fixture.json').write_text(json.dumps(fixture), encoding='utf-8')
    return fixture


if __name__ == '__main__':
    import sys
    print(json.dumps(create_fixture(sys.argv[1])))

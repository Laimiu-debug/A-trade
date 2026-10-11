"""Bounded, offline import parsing. Parsing never writes to the account ledger."""
from __future__ import annotations

import base64
import binascii
import csv
import hashlib
import io
import json
import posixpath
import re
import unicodedata
import zipfile
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import PurePath
from xml.etree import ElementTree as ET

MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 10_000
MAX_COLUMNS = 100
MAX_XML_BYTES = 40 * 1024 * 1024

ALIASES = {
    'date': ['date', 'tradeDate', 'trade_date', '成交日期', '交易日期', '业务日期', '发生日期', '日期', '成交时间日期'],
    'time': ['time', 'tradeTime', 'trade_time', '成交时间', '交易时间', '时间'],
    'type': ['type', 'side', 'direction', 'action', '业务名称', '业务类型', '交易类别', '操作', '买卖标志', '买卖方向', '买卖', '方向', '证券业务'],
    'code': ['code', 'symbol', 'stockCode', 'stock_code', '证券代码', '股票代码', '代码'],
    'name': ['name', 'stockName', 'stock_name', '证券名称', '股票名称', '名称'],
    'quantity': ['quantity', 'qty', 'volume', '成交数量', '成交股数', '证券数量', '交易数量', '数量'],
    'price': ['price', 'tradePrice', '成交价格', '成交价', '成交均价', '交易价格', '价格'],
    'amount': ['amount', '成交金额', '交易金额', '发生金额', '资金发生额', '金额'],
    'commission': ['commission', 'feeCommission', 'fee_commission', '佣金', '净佣金'],
    'stampTax': ['stampTax', 'stamp_tax', 'feeStamp', 'fee_stamp', '印花税'],
    'transferFee': ['transferFee', 'transfer_fee', 'feeTransfer', 'fee_transfer', '过户费'],
    'otherFees': ['otherFees', 'other_fees', '其他费用', '杂费', '其他费'],
    'totalFees': ['totalFees', 'fees', 'fee', '手续费', '费用合计', '总费用', '总手续费'],
    'reason': ['reason', 'note', '备注', '说明', '交易理由'],
    'externalId': ['externalId', 'external_id', 'tradeId', '成交编号', '成交序号', '流水号'],
    'voided': ['voided', 'isVoided', 'is_voided', '作废', '已作废', '作废标志', '撤销标志', '已撤销', 'status', '状态', '成交状态'],
}
TYPES = {
    'buy': {'buy', 'b', '买', '买入', '证券买入', '股票买入', '普通买入'},
    'sell': {'sell', 's', '卖', '卖出', '证券卖出', '股票卖出', '普通卖出'},
    'deposit': {'deposit', 'in', '入金', '转入', '资金转入', '银行转证券', '银行转入', '银转证', '银行存入'},
    'withdrawal': {'withdrawal', 'withdraw', 'out', '出金', '转出', '资金转出', '证券转银行', '银行转出', '证转银', '银行取出'},
    'dividend': {'dividend', '红利入账', '红利入帐', '股息入账', '股息红利', '现金分红', '红利发放'},
}
FEES = ('commission', 'stampTax', 'transferFee', 'otherFees')
VOIDED_VALUES = {'true', '1', 'yes', 'y', '是', '作废', '已作废', '撤销', '已撤销', '撤单', '已撤单', 'cancelled', 'canceled', 'voided'}


def _key(value):
    text = unicodedata.normalize('NFKC', str(value or '')).strip().lstrip('\ufeff')
    return re.sub(r'[\s_\-()（）]', '', text).lower()


LOOKUP = {_key(alias): field for field, names in ALIASES.items() for alias in names}


def _decode(raw):
    encodings = ['utf-16'] if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else ['utf-8-sig', 'gb18030']
    if b'\x00' in raw[:200] and not raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        encodings = ['utf-16-le', 'utf-16-be']
    for encoding in encodings:
        try:
            result = raw.decode(encoding)
            if '\x00' not in result:
                return result, encoding
        except UnicodeDecodeError:
            pass
    raise ValueError('无法读取文件编码，请另存为 UTF-8、GB18030 或 UTF-16。')


def _xml(raw):
    if len(raw) > MAX_XML_BYTES or re.search(br'<!\s*(DOCTYPE|ENTITY)', raw, re.I):
        raise ValueError('工作簿 XML 超过大小限制或包含不支持的声明。')
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError('工作簿 XML 格式损坏。') from exc


def _xlsx(raw):
    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    warnings = []
    try:
        book = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ValueError('XLSX 文件损坏；旧版 XLS 请先另存为 XLSX 或 CSV。') from exc
    with book:
        infos = book.infolist()
        names = {item.filename for item in infos}
        if len(infos) > 5000 or len(names) != len(infos) or sum(item.file_size for item in infos) > MAX_XML_BYTES:
            raise ValueError('工作簿解压后过大或包含重复条目。')
        if any(item.flag_bits & 1 or item.filename.startswith('/') or '..' in item.filename.split('/') for item in infos):
            raise ValueError('不支持加密或异常路径的工作簿。')
        if 'xl/workbook.xml' not in names or 'xl/_rels/workbook.xml.rels' not in names:
            raise ValueError('缺少 XLSX 工作簿结构。')
        workbook = _xml(book.read('xl/workbook.xml'))
        properties = workbook.find('m:workbookPr', ns)
        epoch = datetime(1904, 1, 1) if properties is not None and properties.get('date1904') in ('1', 'true') else datetime(1899, 12, 30)
        sheets = workbook.findall('m:sheets/m:sheet', ns)
        if not sheets:
            raise ValueError('工作簿没有工作表。')
        if len(sheets) > 1:
            warnings.append('工作簿包含多个工作表，本次仅预览第一个工作表。')
        relationship_id = sheets[0].get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
        relationships = _xml(book.read('xl/_rels/workbook.xml.rels'))
        relation = next((r for r in relationships if r.get('Id') == relationship_id), None)
        if relation is None or relation.get('TargetMode') == 'External':
            raise ValueError('工作表关系无效。')
        target = relation.get('Target', '')
        path = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
        if not path.startswith('xl/worksheets/') or path not in names:
            raise ValueError('工作表路径无效。')
        strings = []
        if 'xl/sharedStrings.xml' in names:
            strings = [''.join(node.itertext()) for node in _xml(book.read('xl/sharedStrings.xml')).findall('m:si', ns)]
        styles = []
        if 'xl/styles.xml' in names:
            style_xml = _xml(book.read('xl/styles.xml'))
            custom = {int(n.get('numFmtId')): n.get('formatCode', '') for n in style_xml.findall('m:numFmts/m:numFmt', ns)}
            for node in style_xml.findall('m:cellXfs/m:xf', ns):
                fmt = int(node.get('numFmtId', '0'))
                code = re.sub(r'"[^\"]*"|\[[^\]]*\]', '', custom.get(fmt, '')).lower()
                styles.append(fmt in range(14, 23) or bool(re.search(r'[yd]', code)))
        matrix = []
        for index, row in enumerate(_xml(book.read(path)).findall('m:sheetData/m:row', ns), 1):
            if index > MAX_ROWS + 30:
                raise ValueError(f'单次最多导入 {MAX_ROWS} 行。')
            cells = []
            for cell in row.findall('m:c', ns):
                ref = re.match(r'^([A-Z]+)', cell.get('r', ''))
                column = 0
                for char in ref.group(1) if ref else '':
                    column = column * 26 + ord(char) - 64
                column = column - 1 if column else len(cells)
                if column >= MAX_COLUMNS:
                    raise ValueError(f'工作表超过 {MAX_COLUMNS} 列。')
                while len(cells) <= column:
                    cells.append('')
                value = cell.findtext('m:v', default='', namespaces=ns)
                cell_type = cell.get('t', '')
                if cell.find('m:f', ns) is not None:
                    warnings.append('文件包含公式，仅使用文件保存的缓存值；请核对预览中的数值。')
                if cell_type == 's':
                    try:
                        value = strings[int(value)]
                    except (ValueError, IndexError) as exc:
                        raise ValueError('工作簿共享字符串索引损坏。') from exc
                elif cell_type == 'inlineStr':
                    value = ''.join(n.text or '' for n in cell.findall('.//m:t', ns))
                elif cell_type == 'e':
                    value = '#EXCEL_ERROR'
                elif value and cell_type != 'str':
                    style = int(cell.get('s', '0'))
                    if style < len(styles) and styles[style]:
                        try:
                            converted = epoch + timedelta(days=float(value))
                            value = converted.strftime('%Y-%m-%d' if converted.time() == datetime.min.time() else '%Y-%m-%d %H:%M:%S')
                        except (ValueError, OverflowError):
                            value = '#INVALID_DATE'
                cells[column] = value
            matrix.append((int(row.get('r', index)), cells))
    return matrix, list(dict.fromkeys(warnings))


def _decimal(value, label, positive=False):
    if value is None or str(value).strip() in ('', '--', '—', 'null'):
        raise ValueError(f'{label}缺失')
    text = unicodedata.normalize('NFKC', str(value)).strip().replace(',', '').replace('，', '')
    text = re.sub(r'^[¥￥$]|元$', '', text).strip()
    if text.startswith('(') and text.endswith(')'):
        text = '-' + text[1:-1]
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f'{label}不是有效数字') from exc
    if not number.is_finite() or abs(number) > Decimal('100000000000000'):
        raise ValueError(f'{label}超出允许范围')
    if positive and number <= 0:
        raise ValueError(f'{label}必须大于 0')
    return number


def _date(value):
    text = str(value or '').strip().lstrip("'")
    if not text:
        raise ValueError('交易日期缺失，请填写截图对应的实际成交日期')
    text = text.replace('年', '-').replace('月', '-').replace('日', '').replace('/', '-').replace('.', '-')
    text = re.split(r'[ T]', text)[0]
    if re.fullmatch(r'\d{8}', text):
        text = text[:4] + '-' + text[4:6] + '-' + text[6:]
    try:
        return datetime.strptime(text, '%Y-%m-%d').date().isoformat()
    except ValueError as exc:
        raise ValueError('交易日期无效，必须包含完整年月日') from exc


def _mapping(columns, mapping):
    result = {}
    for column in columns:
        canonical = LOOKUP.get(_key(column))
        if canonical and canonical not in result:
            result[canonical] = column
    if mapping is not None:
        if not isinstance(mapping, dict):
            raise ValueError('字段映射必须是对象。')
        for key, value in mapping.items():
            if key in ALIASES and value in columns:
                result[key] = value
            elif key in columns and value in ALIASES:
                result[value] = key
            else:
                raise ValueError(f'字段映射无效：{key}')
    return result


def reconcile_import_entry(entry, source_values=None):
    """Keep source amounts and require a deliberate choice for conflicting values.

    Status is always recomputed; a caller cannot bypass a conflict by sending
    ``status=resolved``. Resolutions remain inspectable through canonical previews.
    """
    record = dict(entry)
    if record.get('type') not in ('buy', 'sell'):
        return record, []
    original = source_values if source_values is not None else record.get('sourceValues')
    if original is None:
        original = {'amount': record.get('amount'), 'totalFees': record.get('totalFees', record.get('fees')),
                    'feeComponents': {key: record.get(key) for key in FEES}}
    if not isinstance(original, dict) or not isinstance(original.get('feeComponents', {}), dict):
        raise ValueError('原始金额与费用来源格式无效')
    choices = record.get('reconciliation', {})
    if not isinstance(choices, dict):
        raise ValueError('金额与费用核对格式无效')
    def money(value, label):
        return _decimal(value, label).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    def optional(value, label):
        return None if value in (None, '') else str(money(value, label))
    sources = {'amount': optional(original.get('amount'), '原成交金额'),
               'totalFees': optional(original.get('totalFees'), '原费用合计'),
               'feeComponents': {key: optional(original.get('feeComponents', {}).get(key), '原费用明细') for key in FEES}}
    if sources['amount'] is not None:
        sources['amount'] = str(abs(Decimal(sources['amount'])))
    if sources['totalFees'] is not None and Decimal(sources['totalFees']) < 0:
        raise ValueError('原费用合计不可为负数')
    record['sourceValues'] = sources
    reconciled, errors = {}, []
    expected = (_decimal(record.get('quantity'), '数量', positive=True)
                * _decimal(record.get('price'), '价格', positive=True)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    actual = money(record.get('amount', expected), '成交金额') if record.get('amount') not in (None, '') else expected
    source_amount = Decimal(sources['amount']) if sources['amount'] is not None else None
    for field, allowed in [('amount', {'source', 'calculated', 'corrected'}), ('fees', {'source', 'components', 'corrected'})]:
        choice = choices.get(field, {})
        if not isinstance(choice, dict) or choice.get('resolution') not in allowed | {None, ''}:
            raise ValueError('金额或费用的核对选择无效')
    resolution = choices.get('amount', {}).get('resolution') or None
    amount_conflict = (source_amount is not None and source_amount != expected) or actual != expected
    if amount_conflict:
        if resolution == 'source' and source_amount is not None:
            actual = source_amount
        elif resolution == 'calculated':
            actual = expected
        elif resolution == 'corrected' and actual == expected:
            pass
        else:
            errors.append('成交金额与数量 × 价格存在差异，请明确选择来源金额、计算金额，或修正后确认')
    reconciled['amount'] = {'status': 'conflict' if errors else ('resolved' if resolution else 'matched'),
                            'sourceValue': sources['amount'], 'calculatedValue': str(expected), 'resolution': resolution}
    record['amount'] = str(actual)
    components = {key: money(record.get(key) or 0, key) for key in FEES}
    if any(value < 0 for value in components.values()):
        raise ValueError('费用明细不可为负数')
    total = sum(components.values(), Decimal(0))
    source_total = Decimal(sources['totalFees']) if sources['totalFees'] is not None else None
    resolution = choices.get('fees', {}).get('resolution') or None
    fees_conflict = source_total is not None and source_total != total
    fees_error = False
    if fees_conflict and resolution == 'source':
        # Do not invent a breakdown or subtract a negative difference. Preserve
        # the broker's original components above and label this as total only.
        components = {key: Decimal(0) for key in FEES}
        components['otherFees'] = source_total
    elif fees_conflict and resolution not in {'components', 'corrected'}:
        errors.append('费用合计与明细存在差异，请明确选择来源合计、费用明细，或修正后确认')
        fees_error = True
    reconciled['fees'] = {'status': 'conflict' if fees_error else ('resolved' if resolution else 'matched'),
                          'sourceValue': sources['totalFees'], 'calculatedValue': str(total.quantize(Decimal('.01'))),
                          'resolution': resolution, 'totalOnly': resolution == 'source'}
    if fees_conflict and resolution == 'source':
        record.update({key: str(value) for key, value in components.items()})
    if 'fees' in record:
        record['fees'] = str(sum(components.values(), Decimal(0)).quantize(Decimal('.01')))
    record['reconciliation'] = reconciled
    return record, errors


def _normalize(raw, mapping, row_number, source, inherited_errors=None, inherited_warnings=None):
    errors = list(inherited_errors or [])
    warnings = list(inherited_warnings or [])
    values = {field: raw.get(column) for field, column in mapping.items()}
    record = {'source': source, 'name': str(values.get('name') or '').strip(), 'reason': str(values.get('reason') or '').strip(), 'externalId': str(values.get('externalId') or '').strip()}
    if _key(values.get('voided')) in VOIDED_VALUES:
        record['voided'] = True
        errors.append('原记录已作废或撤销，不能作为有效成交导入；如需恢复完整历史请使用账本备份恢复。')
    # The desktop recognizer supplies image-based provenance. Never trust arbitrary
    # model metadata or keep image bytes in the ledger's import details.
    if source == 'ocr':
        import_key = raw.get('importKey')
        if import_key not in (None, ''):
            if isinstance(import_key, str) and re.fullmatch(r'ocr:[0-9a-f]{64}:[0-9]{1,6}', import_key):
                record['importKey'] = import_key
            else:
                errors.append('截图导入标识无效，请重新识别或移除此标识后核对。')
        details = raw.get('sourceDetails')
        if isinstance(details, dict) and details.get('imageHash') is not None:
            image_hash = details['imageHash']
            if isinstance(image_hash, str) and re.fullmatch(r'[0-9a-f]{64}', image_hash):
                record['sourceDetails'] = {'imageHash': image_hash}
            else:
                errors.append('截图来源哈希无效。')
    direction = _key(values.get('type'))
    record['type'] = next((kind for kind, aliases in TYPES.items() if direction in aliases), '')
    if not record['type']:
        errors.append(f'无法识别业务类型“{values.get("type") or "未填写"}”；仅支持明确买入、卖出、入金、出金或现金分红，持仓/委托不可作成交导入')
    try:
        record['date'] = _date(values.get('date'))
    except ValueError as exc:
        record['date'] = str(values.get('date') or '')
        errors.append(str(exc))
    time = str(values.get('time') or '').strip()
    if not time and re.search(r'[ T]\d\d?:\d\d', str(values.get('date') or '')):
        time = re.split('[ T]', str(values['date']), maxsplit=1)[1]
    if time:
        try:
            if re.fullmatch(r'\d{6}', time):
                time = ':'.join((time[:2], time[2:4], time[4:]))
            time = datetime.strptime(time, '%H:%M:%S' if time.count(':') == 2 else '%H:%M').strftime('%H:%M:%S')
        except ValueError:
            errors.append('成交时间无效，请使用 HH:MM:SS')
    record['time'] = time
    code = str(values.get('code') or '').strip().lstrip("'")
    code = re.sub(r'\.0+$', '', code)
    code = re.sub(r'^(?:SH|SZ|BJ)[.:-]?|[.:-]?(?:SH|SZ|BJ)$', '', code, flags=re.I)
    if code.isascii() and code.isdigit() and len(code) <= 6:
        code = code.zfill(6)
    record['code'] = code
    trade = record['type'] in ('buy', 'sell')
    if trade and not re.fullmatch(r'[0-9]{6}', code):
        errors.append('证券代码必须为六位数字')
    if trade:
        for field, label in [('quantity', '成交数量'), ('price', '成交价格')]:
            try:
                number = _decimal(values.get(field), label, positive=True)
                if field == 'quantity' and number != number.to_integral_value():
                    raise ValueError('成交数量必须为整数股数')
                record[field] = int(number) if field == 'quantity' else str(number)
            except ValueError as exc:
                record[field] = values.get(field)
                errors.append(str(exc))
        if not any(record.get(k) in (None, '') for k in ('quantity', 'price')):
            try:
                expected = _decimal(record['price'], '价格') * _decimal(record['quantity'], '数量')
                record['amount'] = str(expected.quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
                if values.get('amount') not in (None, ''):
                    actual = abs(_decimal(values['amount'], '成交金额'))
                    record['amount'] = str(actual.quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
            except (ValueError, InvalidOperation):
                pass
    elif record['type']:
        try:
            # The explicit direction defines cash-flow sign; broker exports may store withdrawals as negative.
            amount = abs(_decimal(values.get('amount'), '资金金额'))
            if not amount:
                raise ValueError('资金金额必须大于 0')
            record['amount'] = str(amount)
        except ValueError as exc:
            errors.append(str(exc))
    supplied_fees = [field for field in FEES if values.get(field) not in (None, '')]
    has_total = values.get('totalFees') not in (None, '')
    record['feeMissing'] = trade and not has_total and (not supplied_fees or (source == 'ocr' and len(supplied_fees) < len(FEES)))
    for field in FEES:
        if values.get(field) in (None, ''):
            record[field] = None if record['feeMissing'] else '0'
            continue
        try:
            value = _decimal(values[field], field)
            if value < 0:
                raise ValueError(f'{field}不可为负数')
            record[field] = str(value)
        except ValueError as exc:
            record[field] = values[field]
            errors.append(str(exc))
    if has_total:
        try:
            total = _decimal(values['totalFees'], '费用合计')
            if total < 0:
                raise ValueError('费用合计不可为负数')
            if not supplied_fees:
                record['otherFees'] = str(total)
                warnings.append('原文件仅有费用合计，计入其他费用；没有额外叠加佣金或税费。')
        except ValueError as exc:
            errors.append(str(exc))
    if record['feeMissing']:
        warnings.append('费用未提供；请人工核对并填写费用（确认无费用时填写 0）。')
    if trade and not errors:
        try:
            record['reconciliation'] = raw.get('reconciliation', {})
            original = raw.get('sourceValues', {'amount': values.get('amount'), 'totalFees': values.get('totalFees'),
                                               'feeComponents': {key: values.get(key) for key in FEES}})
            record, conflicts = reconcile_import_entry(record, original)
            errors.extend(conflicts)
        except ValueError as exc:
            errors.append(str(exc))
    return {'rowNumber': row_number, 'index': row_number - 1, 'record': record, 'entry': record, 'values': record, 'errors': errors, 'warnings': warnings, 'selected': not errors}


def parse_import(filename, content_base64, mapping=None, kind='trades'):
    """Return a preview, preserving row-level errors. Never guesses missing dates."""
    if not isinstance(filename, str) or not isinstance(content_base64, str):
        raise ValueError('请提供文件名和 Base64 文件内容。')
    if len(content_base64) > MAX_BYTES * 4 // 3 + 8:
        raise ValueError('单次导入文件不能超过 10 MiB。')
    try:
        raw = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError('文件内容编码无效。') from exc
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError('文件为空或超过 10 MiB。')
    suffix = PurePath(filename).suffix.lower()
    if kind in ('reviews', 'review'):
        from .reviews import parse_review_import
        return parse_review_import(filename, raw)
    if kind not in ('trades', 'trade', 'transactions'):
        raise ValueError('不支持此导入类型。')
    warnings, rows, columns = [], [], []
    input_hash = None
    source = 'import'
    if suffix == '.json':
        text, _ = _decode(raw)
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, RecursionError) as exc:
            raise ValueError('JSON 文件格式无效。') from exc
        if isinstance(data, dict):
            source = 'ocr' if data.get('source') in ('ocr', 'screenshot') else 'import'
            if isinstance(data.get('inputHash'), str) and re.fullmatch(r'[0-9a-f]{64}', data['inputHash']):
                input_hash = data['inputHash']
            warnings = [str(w)[:2000] for w in data.get('warnings', [])] if isinstance(data.get('warnings'), list) else []
            data = data.get('rows', data.get('trades', data.get('transactions', data.get('items'))))
        if not isinstance(data, list):
            raise ValueError('JSON 需为交易数组，或包含 rows/trades 数组的对象。')
        if len(data) > MAX_ROWS:
            raise ValueError(f'单次最多导入 {MAX_ROWS} 行。')
        unpacked = []
        for index, item in enumerate(data, 1):
            if not isinstance(item, dict):
                unpacked.append((index, {}, ['此行不是交易对象'], []))
                continue
            record = item.get('record', item.get('entry', item.get('values', item)))
            if not isinstance(record, dict):
                record = {}
            for key in record:
                if key not in columns:
                    columns.append(key)
            source = 'ocr' if record.get('source') in ('ocr', 'screenshot') else source
            inherited_errors = [str(e)[:2000] for e in item.get('errors', [])] if isinstance(item.get('errors'), list) else []
            inherited_warnings = [str(e)[:2000] for e in item.get('warnings', [])] if isinstance(item.get('warnings'), list) else []
            unpacked.append((index, record, inherited_errors, inherited_warnings))
        mapping = _mapping(columns, mapping)
        rows = [_normalize(record, mapping, number, source, errors, notes) for number, record, errors, notes in unpacked]
    else:
        if suffix == '.xlsx':
            matrix, warnings = _xlsx(raw)
        elif suffix in ('.csv', '.tsv', '.txt'):
            text, encoding = _decode(raw)
            try:
                dialect = csv.Sniffer().sniff(text[:8192], delimiters=',\t;')
                delimiter = dialect.delimiter
            except csv.Error:
                delimiter = '\t' if suffix == '.tsv' or '\t' in text[:8192] else ','
            reader = csv.reader(io.StringIO(text), delimiter=delimiter)
            matrix = []
            try:
                for cells in reader:
                    matrix.append((reader.line_num, cells))
                    if len(matrix) > MAX_ROWS + 30:
                        raise ValueError(f'单次最多导入 {MAX_ROWS} 行。')
            except csv.Error as exc:
                raise ValueError('表格内容无效或字段过长。') from exc
            if encoding != 'utf-8-sig':
                warnings.append(f'文件按 {encoding} 编码读取。')
        else:
            raise ValueError('支持 CSV、TSV、XLSX、JSON；截图请先进行识别，再人工核对。')
        matrix = [(number, cells) for number, cells in matrix if any(str(c).strip() for c in cells)]
        if not matrix:
            raise ValueError('文件没有数据。')
        header_index = next((i for i, (_, cells) in enumerate(matrix[:30]) if len({LOOKUP.get(_key(c)) for c in cells} - {None}) >= 2), 0)
        _, columns = matrix[header_index]
        columns = [str(c).strip().lstrip('\ufeff') for c in columns]
        if len(columns) > MAX_COLUMNS or len(set(columns)) != len(columns):
            raise ValueError('表头重复或超过 100 列，请清理后导入。')
        mapping = _mapping(columns, mapping)
        if header_index:
            warnings.append(f'已将前 {header_index} 行视作表格说明，表头从第 {matrix[header_index][0]} 行开始。')
        for number, cells in matrix[header_index + 1:]:
            errors = ['本行列数超出表头，请检查分隔符。'] if len(cells) > len(columns) else []
            rows.append(_normalize(dict(zip(columns, cells)), mapping, number, source, errors))
    if not rows:
        warnings.append('没有识别到可预览的交易记录。')
    contract_columns = [column for column in columns if _key(column) in {_key('合同编号'), _key('委托编号'), _key('orderId')}]
    if contract_columns:
        if mapping.get('externalId') in contract_columns:
            warnings.append('你将委托/合同编号映射为成交编号；同一委托可能有多笔分笔成交，请确认该列确实逐笔唯一，避免合法分笔被去重。')
        else:
            warnings.append('委托/合同编号不作为成交去重编号；同一委托可能对应多笔分笔成交，已保留这些行。')
    if source == 'ocr':
        warnings.insert(0, '截图识别结果仅作为待核对草稿；请逐行核对日期、代码、方向、数量、价格和费用。')
    return {'kind': 'trades', 'fileName': filename, 'fileHash': hashlib.sha256(raw).hexdigest(), 'inputHash': input_hash, 'source': source, 'columns': columns, 'mapping': mapping, 'rows': rows, 'warnings': warnings, 'summary': {'total': len(rows), 'valid': sum(not r['errors'] for r in rows), 'invalid': sum(bool(r['errors']) for r in rows)}}


def preview_import(payload):
    if not isinstance(payload, dict):
        raise ValueError('导入参数必须是对象。')
    content = payload.get('contentBase64', payload.get('content_base64'))
    filename = payload.get('fileName', payload.get('filename', 'trades.json'))
    if content is None and isinstance(payload.get('text'), str):
        content = base64.b64encode(payload['text'].encode('utf-8')).decode()
    if content is None and isinstance(payload.get('rows'), list):
        content = base64.b64encode(json.dumps({'source': payload.get('source'), 'inputHash': payload.get('inputHash'), 'rows': payload['rows'], 'warnings': payload.get('warnings', [])}, ensure_ascii=False).encode('utf-8')).decode()
        filename = 'reviewed-trades.json'
    return parse_import(filename, content, payload.get('mapping'), payload.get('kind', 'trades'))

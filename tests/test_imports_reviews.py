"""Real fixture inputs exercise decoding, ambiguous rows and review completeness."""
import base64
import io
import json
import unittest
import zipfile
from service.imports import parse_import, preview_import
from service.reviews import ITEM_IDS, REVIEW_SCHEMA, calculate_score, export_reviews, validate_review


def parse(text, name='trades.csv', encoding='utf-8', **kwargs):
    raw = text.encode(encoding) if isinstance(text, str) else text
    return parse_import(name, base64.b64encode(raw).decode(), **kwargs)


def workbook(sheet, extra=None):
    stream = io.BytesIO()
    entries = {
        'xl/workbook.xml': '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="成交" sheetId="1" r:id="rId1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        'xl/worksheets/sheet1.xml': '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + sheet + '</sheetData></worksheet>',
    }
    entries.update(extra or {})
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, text in entries.items():
            archive.writestr(name, text)
    return stream.getvalue()


class ImportTests(unittest.TestCase):
    def test_amount_and_fee_conflicts_keep_sources_across_canonical_preview(self):
        result = parse('date,side,code,qty,price,amount,commission,fees\n20261010,buy,600000,300,10.123,3037,5,20\n')
        row = result['rows'][0]
        self.assertEqual(row['entry']['amount'], '3037.00')
        self.assertEqual(row['entry']['sourceValues']['totalFees'], '20.00')
        self.assertEqual(row['entry']['sourceValues']['feeComponents']['commission'], '5.00')
        self.assertEqual(row['entry']['reconciliation']['amount']['status'], 'conflict')
        self.assertEqual(row['entry']['reconciliation']['fees']['status'], 'conflict')
        self.assertFalse(row['selected'])
        repeated = preview_import({'rows': [row['entry']]})['rows'][0]
        self.assertEqual(repeated['entry']['sourceValues'], row['entry']['sourceValues'])
        self.assertEqual(len(repeated['errors']), 2)

    def test_explicit_reconciliation_choices_are_recomputed_not_trusted(self):
        row = parse('date,side,code,qty,price,amount,commission,fees\n20261010,buy,600000,300,10.123,3037,5,20\n')['rows'][0]
        raw = row['entry']
        raw['reconciliation'] = {'amount': {'status': 'resolved'}, 'fees': {'status': 'resolved'}}
        self.assertEqual(len(preview_import({'rows': [raw]})['rows'][0]['errors']), 2)
        raw['reconciliation'] = {'amount': {'resolution': 'source'}, 'fees': {'resolution': 'source'}}
        resolved = preview_import({'rows': [raw]})['rows'][0]
        self.assertEqual(resolved['errors'], [])
        self.assertEqual(resolved['entry']['amount'], '3037.00')
        self.assertEqual(resolved['entry']['otherFees'], '20.00')
        self.assertEqual(resolved['entry']['commission'], '0')
        self.assertEqual(resolved['entry']['sourceValues']['feeComponents']['commission'], '5.00')
        repeated = preview_import({'rows': [resolved['entry']]})['rows'][0]
        self.assertEqual(repeated['errors'], [])
        raw['reconciliation'] = {'amount': {'resolution': 'calculated'}, 'fees': {'resolution': 'components'}}
        calculated = preview_import({'rows': [raw]})['rows'][0]
        self.assertEqual(calculated['errors'], [])
        self.assertEqual(calculated['entry']['amount'], '3036.90')
        self.assertEqual(calculated['entry']['commission'], '5')

    def test_source_fee_total_below_components_never_creates_negative_fee(self):
        raw = parse('date,side,code,qty,price,commission,fees\n20261010,buy,600000,100,10,20,5\n')['rows'][0]['entry']
        raw['reconciliation']['fees']['resolution'] = 'source'
        resolved = preview_import({'rows': [raw]})['rows'][0]
        self.assertEqual(resolved['errors'], [])
        self.assertEqual(resolved['entry']['commission'], '0')
        self.assertEqual(resolved['entry']['otherFees'], '5.00')

    def test_broker_csv_encodings_and_fee_total(self):
        text = '成交日期,成交时间,证券代码,证券名称,买卖标志,成交数量,成交价格,手续费,成交编号\n20261010,093005,1,平安银行,证券买入,100,12.345,5.10,abc-1\n'
        for encoding in ('utf-8-sig', 'gb18030', 'utf-16'):
            with self.subTest(encoding=encoding):
                result = parse(text, encoding=encoding)
                row = result['rows'][0]
                self.assertEqual(row['errors'], [])
                self.assertEqual(row['entry']['code'], '000001')
                self.assertEqual(row['entry']['date'], '2026-10-10')
                self.assertEqual(row['entry']['time'], '09:30:05')
                self.assertEqual(row['entry']['otherFees'], '5.10')
                self.assertEqual(row['entry']['commission'], '0')
                self.assertEqual(row['entry']['amount'], '1234.50')

    def test_header_preamble_and_mapping(self):
        result = parse('交易明细导出\n证券账户已隐藏\n成交日期\t业务名称\t证券代码\tqtyx\tpricez\n2026/10/10\t卖出\t600519.SH\t100\t1600\n', name='broker.tsv', mapping={'quantity': 'qtyx', 'price': 'pricez'})
        self.assertEqual(result['rows'][0]['rowNumber'], 4)
        self.assertEqual(result['rows'][0]['entry']['code'], '600519')
        self.assertEqual(result['rows'][0]['errors'], [])
        self.assertTrue(result['rows'][0]['entry']['feeMissing'])

    def test_unknown_business_and_missing_date_are_not_guessed(self):
        result = parse('date,type,code,quantity,price\n2026-10-10,配股缴款,600000,100,10\n,buy,000001,100,10\n2026-02-30,buy,000001,100,10\n')
        self.assertEqual(result['summary']['invalid'], 3)
        self.assertIn('无法识别业务类型', result['rows'][0]['errors'][0])
        self.assertEqual(result['rows'][1]['entry']['date'], '')
        self.assertIn('日期缺失', result['rows'][1]['errors'][0])

    def test_ocr_preserves_unreadable_and_missing_fees(self):
        data = {'source': 'ocr', 'warnings': ['低清截图'], 'rows': [{'record': {'date': None, 'code': '600000', 'side': 'buy', 'price': 10, 'qty': 100, 'commission': 5}, 'warnings': ['日期被裁切']}, {'date': '2026-10-10', 'code': '600000', 'side': 'hold', 'price': 10, 'qty': 100}]}
        result = parse(json.dumps(data), 'ocr.json')
        self.assertEqual(result['source'], 'ocr')
        self.assertEqual(result['summary']['invalid'], 2)
        self.assertTrue(result['rows'][0]['entry']['feeMissing'])
        self.assertIsNone(result['rows'][0]['entry']['stampTax'])
        self.assertIn('日期被裁切', result['rows'][0]['warnings'])
        self.assertIn('低清截图', result['warnings'])

    def test_cash_flows_use_explicit_direction(self):
        result = parse('日期,业务名称,发生金额\n2026年10月10日,银行转证券,2000\n20261011,证券转银行,-500\n20261011,现金分红,20\n')
        self.assertEqual([r['entry']['type'] for r in result['rows']], ['deposit', 'withdrawal', 'dividend'])
        self.assertEqual(result['rows'][1]['entry']['amount'], '500')
        self.assertEqual(result['summary']['invalid'], 0)

    def test_shared_order_number_does_not_collapse_partial_fills(self):
        text = '成交日期,买卖方向,证券代码,成交数量,成交价格,合同编号\n20261010,买入,000001,100,12,order-1\n20261010,买入,000001,200,12,order-1\n'
        result = parse(text)
        self.assertEqual(result['summary']['valid'], 2)
        self.assertEqual([row['entry']['externalId'] for row in result['rows']], ['', ''])
        self.assertTrue(any('已保留这些行' in warning for warning in result['warnings']))
        explicit = parse(text, mapping={'externalId': '合同编号'})
        self.assertEqual(explicit['rows'][0]['entry']['externalId'], 'order-1')
        self.assertTrue(any('逐笔唯一' in warning for warning in explicit['warnings']))

    def test_numbers_must_be_finite_and_integer_quantity(self):
        result = parse('date,side,code,qty,price\n20261010,buy,000001,100,NaN\n20261010,buy,000001,0.5,10\n20261010,buy,000001,100,Infinity\n')
        self.assertEqual(result['summary']['invalid'], 3)

    def test_xlsx_inline_strings_and_numeric_code(self):
        headers = ['date', 'side', 'code', 'qty', 'price']
        sheet = '<row r="1">' + ''.join(f'<c r="{chr(65+i)}1" t="inlineStr"><is><t>{value}</t></is></c>' for i, value in enumerate(headers)) + '</row>'
        sheet += '<row r="2"><c r="A2" t="inlineStr"><is><t>2026-10-10</t></is></c><c r="B2" t="inlineStr"><is><t>buy</t></is></c><c r="C2"><v>1</v></c><c r="D2"><v>100</v></c><c r="E2"><v>12.5</v></c></row>'
        result = parse(workbook(sheet), 'trades.xlsx')
        self.assertEqual(result['rows'][0]['errors'], [])
        self.assertEqual(result['rows'][0]['entry']['code'], '000001')
        self.assertEqual(result['rows'][0]['entry']['amount'], '1250.00')

    def test_xlsx_rejects_entities_and_paths(self):
        for extra in ({'../evil': 'test'}, {'xl/workbook.xml': '<!DOCTYPE foo [<!ENTITY x "boom">]><foo>&x;</foo>'}):
            with self.subTest(extra=list(extra)):
                with self.assertRaises(ValueError):
                    parse(workbook('', extra), 'trades.xlsx')

    def test_duplicate_headers_and_invalid_base64_rejected(self):
        with self.assertRaises(ValueError):
            parse('date,date,type\n20261010,20261010,buy')
        with self.assertRaises(ValueError):
            parse_import('x.csv', 'not base64!')

    def test_voided_rows_cannot_resurrect_as_live_trades(self):
        for column, marker in [('voided', 'true'), ('isVoided', '1'), ('已作废', '是'), ('作废标志', 'yes'), ('状态', '已撤单')]:
            with self.subTest(column=column, marker=marker):
                result = parse(f'date,side,code,qty,price,{column}\n20261010,buy,000001,100,12,{marker}\n')
                row = result['rows'][0]
                self.assertFalse(row['selected'])
                self.assertTrue(row['entry']['voided'])
                self.assertTrue(any('已作废或撤销' in error for error in row['errors']))
                # Re-previewing the corrected canonical record must not erase its voided marker.
                again = preview_import({'rows': [row['entry']]})
                self.assertTrue(again['rows'][0]['errors'])
        live = parse('date,side,code,qty,price,voided\n20261010,buy,000001,100,12,false\n')
        self.assertEqual(live['rows'][0]['errors'], [])
        live_status = parse('date,side,code,qty,price,状态\n20261010,buy,000001,100,12,全部成交\n')
        self.assertEqual(live_status['rows'][0]['errors'], [])

    def test_reviewed_rows_adapter(self):
        result = preview_import({'source': 'ocr', 'rows': [{'date': '2026-10-10', 'type': 'buy', 'code': '000001', 'quantity': 100, 'price': '12', 'commission': '5', 'stampTax': '0', 'transferFee': '0.01', 'otherFees': '0'}]})
        self.assertEqual(result['rows'][0]['errors'], [])
        self.assertFalse(result['rows'][0]['entry']['feeMissing'])

    def test_ocr_identity_survives_model_wording_changes(self):
        stable_key = 'ocr:' + 'a' * 64 + ':1'
        base = {'date': '2026-10-10', 'type': 'buy', 'code': '000001', 'quantity': 100, 'price': '12', 'importKey': stable_key, 'sourceDetails': {'imageHash': 'b' * 64, 'imageBytes': 'must-not-be-kept'}}
        first = preview_import({'source': 'ocr', 'inputHash': 'a' * 64, 'rows': [{**base, 'warnings': ['核对费用']}]})
        second = preview_import({'source': 'ocr', 'inputHash': 'a' * 64, 'rows': [{**base, 'warnings': ['请核对费用'], 'price': '12.00'}]})
        self.assertNotEqual(first['fileHash'], second['fileHash'])
        self.assertEqual(first['rows'][0]['entry']['importKey'], second['rows'][0]['entry']['importKey'])
        self.assertEqual(first['rows'][0]['entry']['sourceDetails'], {'imageHash': 'b' * 64})
        self.assertEqual(first['inputHash'], 'a' * 64)
        invalid = preview_import({'source': 'ocr', 'rows': [{**base, 'importKey': 'untrusted-id'}]})
        self.assertTrue(invalid['rows'][0]['errors'])


class ReviewTests(unittest.TestCase):
    def test_rubric_has_24_items_and_all_templates(self):
        self.assertEqual(len(ITEM_IDS), 24)
        self.assertEqual({t['id'] for t in REVIEW_SCHEMA['templates']}, {'daily', 'weekly', 'monthly', 'trade', 'idea'})
        self.assertEqual(REVIEW_SCHEMA['version'], 'account-review-v1')

    def test_one_full_item_cannot_make_total_full(self):
        score = calculate_score({'position.budget': {'score': 4, 'evidence': '总仓位42%，低于计划50%。'}})
        self.assertEqual(score['score'], 4.2)
        self.assertEqual(score['coverage'], 4.2)
        self.assertEqual(score['assessedScore'], 100)
        self.assertFalse(score['isComplete'])

    def test_blank_and_no_evidence_do_not_score(self):
        self.assertIsNone(calculate_score({})['score'])
        score = calculate_score({key: {'score': 4} for key in ITEM_IDS})
        self.assertIsNone(score['score'])
        self.assertEqual(score['coverage'], 0)
        self.assertEqual(score['enteredCount'], 24)

    def test_zero_score_is_real_score_and_na_needs_reason(self):
        score = calculate_score({'position.budget': {'score': 0, 'evidence': '实际80%，超过计划50%。'}, 'exit.plan': {'notApplicable': True}})
        self.assertEqual(score['score'], 0)
        self.assertEqual(score['ratedCount'], 1)
        self.assertEqual(score['notApplicableCount'], 0)
        score = calculate_score({'exit.plan': {'notApplicable': True, 'evidence': '该预案尚未产生任何持仓或成交。'}})
        self.assertEqual(score['notApplicableCount'], 1)
        self.assertEqual(score['applicableCount'], 23)

    def test_full_evidenced_score_and_outcome_separation(self):
        review = {'kind': 'daily', 'date': '2026-10-10', 'fields': {'actualOutcome': '亏损100'}, 'scores': {key: {'score': 4, 'evidence': '满足本项规则，详见逐笔记录。'} for key in ITEM_IDS}}
        result = validate_review(review)
        self.assertEqual(result['score']['score'], 100)
        self.assertTrue(result['score']['isComplete'])
        self.assertEqual(result['score']['coverage'], 100)

    def test_invalid_score_versions_and_dates_rejected(self):
        for value in (-1, 5, 2.5, True, '4'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                calculate_score({'position.budget': {'score': value}})
        with self.assertRaises(ValueError):
            validate_review({'date': '2026-02-30'})
        with self.assertRaises(ValueError):
            validate_review({'date': '2026-10-10', 'rubricVersion': 'future-v2'})

    def test_json_export_round_trip_and_markdown_preserved(self):
        review = {'kind': 'trade', 'date': '2026-10-10', 'title': '一笔交易', 'fields': {'tradeRationale': '完整记录'}, 'relatedTradeIds': ['one'], 'scores': {'entry.trigger': {'score': 3, 'evidence': '触发后买入'}}}
        result = parse(export_reviews([review]), 'reviews.json', kind='reviews')
        self.assertEqual(result['rows'][0]['errors'], [])
        self.assertEqual(result['rows'][0]['entry']['fields'], review['fields'])
        markdown = '# 2026-10-10 每日复盘\n\n完整**原文**\n'
        result = parse(markdown, 'note.md', kind='reviews')
        self.assertEqual(result['rows'][0]['entry']['fields']['importedText'], markdown)
        self.assertIsNone(result['rows'][0]['entry']['score']['score'])

    def test_reference_legacy_scores_retained_without_false_mapping(self):
        data = {'daily_reviews': [{'review_date': '2026-10-10', 'market_observation': '震荡', 'scores': '{"position": {"final": 8}}'}]}
        result = parse(json.dumps(data), 'backup.json', kind='reviews')
        row = result['rows'][0]
        self.assertEqual(row['errors'], [])
        self.assertEqual(row['entry']['fields']['marketState'], '震荡')
        self.assertIn('position', row['entry']['fields']['importedLegacyScores'])
        self.assertEqual(row['entry']['scores'], {})

    def test_reference_weekly_and_monthly_dates(self):
        data = {'weekly_reviews': [{'year': 2026, 'week': 41, 'right_things': '遵守计划'}], 'monthly_reviews': [{'year': 2026, 'month': 2, 'system_iteration': '调整规则'}]}
        result = parse(json.dumps(data), 'backup.json', kind='reviews')
        self.assertEqual(result['summary']['invalid'], 0)
        self.assertEqual(result['rows'][0]['entry']['date'], '2026-10-05')
        self.assertEqual(result['rows'][0]['entry']['endDate'], '2026-10-11')
        self.assertEqual(result['rows'][1]['entry']['endDate'], '2026-02-28')
        self.assertIn('importedOriginalRecord', result['rows'][0]['entry']['fields'])


if __name__ == '__main__':
    unittest.main()

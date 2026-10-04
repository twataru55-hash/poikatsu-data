from copy import deepcopy
from types import SimpleNamespace

import pytest

from candidates import normalize, match_existing
from common import content_key, parse_dt, load_json, load_sources, SCHEMA_DIR
from date_evidence import date_present
from test_candidates import RAW, SRC
from test_validate import BASE, ctx, with_, codes
from validate import validate_candidate


def test_distinct_coupon_products_do_not_merge():
    a = normalize(dict(RAW, type='coupon', title='ブリトー300円以下', benefit=dict(RAW['benefit'], rate_text='230マイルで無料引換')), SRC)
    b = normalize(dict(RAW, type='coupon', title='ブリトー301円以上', benefit=dict(RAW['benefit'], rate_text='300マイルで無料引換')), SRC)
    assert match_existing(b, [a]) == ('new', None)


@pytest.mark.parametrize('path,value', [('rate_text','30円引き'), ('conditions','一部店舗は対象外'), ('entry_url','https://paypay.ne.jp/new-entry/')])
def test_material_changes_cannot_be_same(path, value):
    a = normalize(RAW, SRC); b = deepcopy(a)
    if path == 'rate_text': b['benefit']['rate_text'] = value
    elif path == 'entry_url': b['entry']['url'] = value
    else: b[path] = value
    assert content_key(a) != content_key(b)
    assert match_existing(b, [a])[0] == 'changed'


@pytest.mark.parametrize('text,day,expected', [
    ('2026年10月1日(木)、8日(木)、15日(木)', '2026-10-08', True),
    ('2026年10月1日(木)、8日(木)', '2026-11-08', False),
    ('2025年10月1日(木)、8日(木)', '2026-10-08', False),
    ('10月1日。別のキャンペーンは8日', '2026-10-08', False),
    ('10月18日', '2026-10-08', False),
    ('11月8日', '2026-01-08', False),
    ('2026/10/1～2026/10/31', '2026-10-31', True),
    ('2026-10-01～2026-10-31', '2026-10-31', True),
    ('10月31日～11月2日', '2026-11-02', True),
    ('2026/8/3-2026/10/4', '2026-10-04', True),
    ('2026/7/6-2026/10/4', '2026-10-04', True),
    ('2025/7/6-2025/10/4', '2026-10-04', False),
])
def test_dates_are_scoped(text, day, expected):
    assert date_present(text, parse_dt(day)) is expected


def test_both_ends_must_be_grounded():
    page = BASE['evidence_quote'] + '期間2026年10月1日～10月20日'
    decision, reasons = validate_candidate(deepcopy(BASE), ctx(page_text=page))
    assert decision == 'hold' and 'V07' in codes(reasons)


def test_distant_literal_period_quote_without_whole_page_search():
    period = '主企画の対象期間2026年10月1日～2026年10月31日'
    page = BASE['evidence_quote'] + '注意事項' * 400 + period
    c = with_(period_evidence_quote=period)
    assert validate_candidate(c, ctx(page_text=page)) == ('accept', [])
    c['period_evidence_quote'] = '書かれていない期間2026年10月1日～2026年10月31日'
    assert 'V06' in codes(validate_candidate(c, ctx(page_text=page))[1])


def test_same_rechecks_evidence_before_advancing_verified(monkeypatch):
    import pipeline
    existing = normalize(RAW, SRC); existing['last_verified'] = '2026-01-01'
    monkeypatch.setattr(pipeline, 'validate_candidate', lambda c, ctx: ('hold',['V06 missing']))
    monkeypatch.setattr(pipeline, 'file_hold', lambda *args: True)
    stats = pipeline.new_stats('test', {})
    result = pipeline.process_items([RAW], SRC, 'changed official text', [existing],
                                    {'brands': [], 'stores': []}, {}, None, stats, [], None)
    assert result[0]['last_verified'] == '2026-01-01'
    assert stats['held'] == 1 and stats['same'] == 0


def test_partial_coverage_preserves_prior_sources(tmp_path, monkeypatch):
    import crawl_coverage
    monkeypatch.setattr(crawl_coverage, 'WORK_DIR', tmp_path)
    def queue(sid, at):
        return {'run_id':at, 'created_at':at, 'items':[{'source_id':sid}], 'coverage':[],
                'unchanged':[], 'failed':[], 'detail_failed':[], 'deferred':[], 'gave_up':[]}
    crawl_coverage.record_coverage(queue('a','first'), [{'id':'a'}])
    result=crawl_coverage.record_coverage(queue('b','second'), [{'id':'b'}])
    assert result['sources']['a']['measured_at']=='first'
    assert result['sources']['b']['measured_at']=='second'
    assert result['sources']['a']['complete_claim'] is False


def test_config_includes_new_official_routes():
    import re
    sources={s['id']:s for s in load_sources()}
    assert re.search(sources['aeonretail-otoku']['follow']['pattern'], 'https://www.aeonretail.jp/campaign/pointX/')
    assert re.search(sources['iaeon-campaign']['follow']['pattern'], 'https://www.aeon.com/aeonapp/campaign/202610_ereceipt_first30/')
    assert re.search(sources['welcia-offers']['follow']['pattern'], 'https://m.e-welcia.com/drug/202609_vitamin_fair')
    assert not re.search(sources['welcia-offers']['follow']['pattern'], 'https://example.com/campaign/')


def test_new_request_schema_retains_old_answers():
    from jsonschema import Draft202012Validator
    validator=Draft202012Validator(load_json(SCHEMA_DIR/'llm_output.schema.json'))
    validator.validate({'campaigns':[RAW]})
    validator.validate({'campaigns':[dict(RAW,period_evidence_quote='期間2026年10月1日～10月31日')]})


def test_same_coupon_title_on_different_official_pages_has_distinct_id():
    a=normalize(dict(RAW,type='coupon',official_url='https://paypay.ne.jp/a/'), SRC)
    b=normalize(dict(RAW,type='coupon',official_url='https://paypay.ne.jp/b/'), SRC)
    assert a['id'] != b['id']


def test_request_schema_catches_coupon_scope_before_send():
    from jsonschema import Draft202012Validator
    validator=Draft202012Validator(load_json(SCHEMA_DIR/'llm_output.schema.json'))
    coupon=deepcopy(RAW)
    coupon.update(type='coupon',scope={'kind':'payment_wide','store_ids':[], 'prefecture_codes':[], 'municipality':None})
    assert list(validator.iter_errors({'campaigns':[coupon]}))
    coupon['scope'].update(kind='stores',store_ids=['aeon'])
    validator.validate({'campaigns':[coupon]})


def test_coupon_amount_update_with_stable_product_title():
    a=normalize(dict(RAW,type='coupon',title='対象商品の値引券'), SRC)
    b=deepcopy(a);b['benefit']['rate_text']='30円引き'
    assert match_existing(b,[a])[0]=='changed'

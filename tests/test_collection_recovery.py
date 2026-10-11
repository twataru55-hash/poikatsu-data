from copy import deepcopy
from types import SimpleNamespace

import collect
import discovery
from collect import Fetcher, fetch_text, html_to_text, save_page_assets
from common import load_sources


def test_municipality_list_reaches_details_and_keeps_exclusions(tmp_path, monkeypatch):
    source = next(s for s in load_sources() if s['id'] == 'paypay-local')
    source = deepcopy(source)
    source['follow']['max_new'] = 1
    urls = [
        'https://paypay.ne.jp/event/iwate-tono-city-20261001/',
        'https://paypay.ne.jp/event/iwate-oshu-city-gift-vouchers-20261001/',
        'https://paypay.ne.jp/notice/20260915/cp-jichitai/',
        'https://example.com/event/iwate-tono-city-20261001/',
    ]
    page = SimpleNamespace(source=source, links=[('自治体詳細', u) for u in urls])
    class LocalFetcher:
        last_url = None
        def allowed(self, url): return True
        def get_static(self, url):
            return '<main>対象のお店でポイント還元。対象購入期間と条件の詳細を確認してください。' + '公式条件' * 20 + '</main>'
    monkeypatch.setattr(discovery, 'WORK_DIR', tmp_path)
    monkeypatch.setattr(collect, 'WORK_DIR', tmp_path)
    audit = {}
    got, failed = discovery.discover_and_refresh(page, {}, {}, LocalFetcher(), audit=audit)
    assert len(got) == 1 and not failed
    assert got[0][0]['parent'] == 'paypay-local'
    assert [x['url'] for x in audit['discovered']] == urls[:2]
    assert audit['unvisited'] == [urls[1]]
    assert {x['url'] for x in audit['excluded']} == set(urls[2:])


def test_tsuruha_header_alt_in_prompt_and_asset_ledger(tmp_path, monkeypatch):
    source = next(s for s in load_sources() if s['id'] == 'tsuruha-offers')
    detail = discovery.source_for_detail(source, 'https://www.tsuruha-kcp.com/cp/pgcampaign202609/')
    html = ('<html><body><header><img src="header.png" alt="要エントリー 20%還元">'
            '<img src="dates.jpg" alt="対象購入期間2025年9月1日～10月31日 エントリー締切2026年10月31日">'
            '</header><main><p>対象商品</p><img src="products.png"></main></body></html>')
    text = html_to_text(html, detail)
    assert '要エントリー 20%還元' in text
    assert '2025年9月1日' in text and '2026年10月31日' in text  # Preserve contradictions, never correct the year.
    monkeypatch.setattr(collect, 'WORK_DIR', tmp_path)
    assets = save_page_assets(html, detail, detail['url'])
    assert len(assets['images']) == 3
    assert assets['status'] == 'references_only_not_verified_content'


def test_shared_refetcher_preserves_page_intervals_and_robots_cache(monkeypatch):
    clock = [100.0]
    calls = []
    monkeypatch.setattr(collect.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(collect.time, 'sleep', lambda n: clock.__setitem__(0, clock[0] + n))
    class Session:
        def get(self, url, **kwargs):
            calls.append((url, clock[0]))
            text = 'User-agent: *\nAllow: /' if url.endswith('/robots.txt') else '<main>公式本文</main>'
            return SimpleNamespace(status_code=200, text=text, content=text.encode(),
                                   headers={'Content-Type': 'text/html; charset=utf-8'}, url=url)
    monkeypatch.setattr(collect, 'http_session', lambda *args: Session())
    config = {'fetch': {'min_interval_sec': 3}}
    fetcher = Fetcher(config)
    for suffix in ('a', 'b'):
        assert fetch_text({'url': 'https://www.ministop.co.jp/' + suffix}, config, fetcher) == '公式本文'
    assert sum(u.endswith('/robots.txt') for u, _ in calls) == 1
    assert calls[-1][1] - calls[-2][1] >= 3


def test_shared_refetcher_still_respects_robots(monkeypatch):
    class Blocked:
        def allowed(self, url): return False
        def get_static(self, url): raise AssertionError('Must not fetch a forbidden page')
    import pytest
    with pytest.raises(RuntimeError, match='robots'):
        fetch_text({'url':'https://www.ministop.co.jp/blocked'}, {}, Blocked())



def test_js_denial_is_a_fetch_failure_and_closes_browser(monkeypatch):
    import sys
    import pytest
    from contextlib import nullcontext
    for status, html in [(403, '<body>Forbidden</body>'),
                         (200, "<h1>Access Denied</h1>You don't have permission to access this page")]:
        closed = []
        page = SimpleNamespace(goto=lambda *a, **k: SimpleNamespace(status=status),
                               wait_for_timeout=lambda n: None, content=lambda: html,
                               url='https://www.smbc-card.com/memfs/campaign/index.jsp')
        browser = SimpleNamespace(new_page=lambda **k: page, close=lambda: closed.append(True))
        api = SimpleNamespace(chromium=SimpleNamespace(launch=lambda: browser))
        module = SimpleNamespace(sync_playwright=lambda: nullcontext(api))
        monkeypatch.setitem(sys.modules, 'playwright.sync_api', module)
        with pytest.raises(RuntimeError, match='HTTP 403|Access Denied'):
            Fetcher({}).get_js(page.url)
        assert closed == [True]


def test_ministop_dynamic_body_changes_hash_and_reaches_details(tmp_path, monkeypatch):
    import hashlib
    source = next(s for s in load_sources() if s['id'] == 'ministop-waon')
    html = ('<main>毎月10日はAEON Payデー。店頭決済でWAON POINT10倍。'
            '詳しい条件は公式の詳細をご確認ください。'
            '<a href="/campaign/10aeon_pay_waon_point10.html">詳細</a></main>')
    previous = hashlib.sha256('WAON POINT お得情報'.encode()).hexdigest()
    monkeypatch.setattr(collect, 'WORK_DIR', tmp_path)
    monkeypatch.setattr(collect, 'load_json', lambda path, default: {
        source['id']: {'hash': previous, 'extraction_revision': 3}
    } if path.name == 'snapshots.json' else default)
    class DynamicFetcher:
        last_url = source['url']
        def allowed(self, url): return True
        def get_js(self, url): return html
        def get_static(self, url): raise AssertionError('Initial HTML omits campaign conditions')
    monkeypatch.setattr(collect, 'Fetcher', lambda config: DynamicFetcher())
    pages = collect.collect([source], {'manual': {'extraction_revision': 3}},
                            record_snapshots=False, record_status=False)
    assert len(pages) == 1 and pages[0].ok and pages[0].changed
    assert pages[0].digest != previous
    assert 'WAON POINT10倍' in pages[0].text
    import re
    assert re.match(source['follow']['pattern'], pages[0].links[0][1])
    assert source['follow']['render'] == 'js'
    monkeypatch.setattr(discovery, 'WORK_DIR', tmp_path)
    detail_fetches = []
    class DetailFetcher:
        last_url = None
        def allowed(self, url): return True
        def get_js(self, url):
            detail_fetches.append(url)
            return html
        def get_static(self, url): raise AssertionError('Detail uses browser retrieval')
    got, failed = discovery.discover_and_refresh(pages[0], {}, {}, DetailFetcher())
    assert len(got) == 1 and not failed
    assert detail_fetches == [pages[0].links[0][1]]


def test_listing_connection_failure_keeps_known_detail_checks(tmp_path, monkeypatch):
    import prepare
    from collect import Page
    from common import save_json, load_json
    source = next(s for s in load_sources() if s['id'] == 'ministop-offers')
    source = deepcopy(source)
    source['follow']['max_new'] = 1
    known = ['https://www.ministop.co.jp/campaign/1get.html',
             'https://www.ministop.co.jp/campaign/2504aeonpay-cp.html']
    save_json(tmp_path / 'discovery' / 'ministop-offers.json', {
        'at': '2026-10-05T09:19:15+09:00',
        'discovered': [{'url': u, 'label': '公式詳細'} for u in known] + [
            {'url': 'https://example.com/campaign/not-official.html', 'label': '対象外'}],
        'checked': {known[0]: {'attempted_at': '2026-10-04'},
                    known[1]: {'attempted_at': '2026-10-03'}}})
    for module in (collect, discovery, prepare):
        monkeypatch.setattr(module, 'WORK_DIR', tmp_path)
    monkeypatch.setattr(prepare, 'INBOX', tmp_path / 'inbox')
    monkeypatch.setattr(prepare, 'load_sources', lambda: [source])
    monkeypatch.setattr(prepare, 'collect', lambda *a, **k: [Page(source, '', False, False, 'ConnectionError')])
    fetches = []
    class DetailFetcher:
        last_url = None
        def allowed(self, url): return True
        def get_js(self, url):
            fetches.append(url)
            return '<main>公式の詳細条件です。' + '対象購入期間と条件を確認してください。' * 10 + '</main>'
        def get_static(self, url): raise AssertionError('Details require browser rendering')
    monkeypatch.setattr(prepare, 'Fetcher', lambda config: DetailFetcher())
    result = prepare.main()
    queue = load_json(tmp_path / 'inbox' / result['run_id'] / 'queue.json', {})
    assert result['ok'] and len(result['to_answer']) == 1
    assert fetches == [known[1]]  # Oldest attempt first; keep configured budget.
    assert queue['failed'] == [{'source_id': source['id'], 'error': 'ConnectionError'}]
    audit = queue['coverage'][0]
    assert audit['listing_ok'] is False and audit['listing_pages'] == []
    assert audit['discovery_basis'] == 'previous_listing'
    assert audit['listing_discovery_at'] == '2026-10-05T09:19:15+09:00'
    assert audit['unvisited'] == [known[0]]
    assert audit['excluded'][0]['url'].startswith('https://example.com/')
    assert queue['source_summary'][source['id']]['listing_ok'] is False
    assert queue['source_summary'][source['id']]['complete_claim'] is False


def test_known_detail_fallback_does_not_bypass_denial_or_missing_permission():
    from collect import Page
    source = next(s for s in load_sources() if s['id'] == 'ministop-offers')
    for error in ('HTTP 403', 'HTTP 404', 'robots.txt で取得禁止', 'Access Denied（本文未取得）'):
        assert not discovery.can_refresh_known(Page(source, '', False, False, error))
    assert discovery.can_refresh_known(Page(source, '', False, False, 'ConnectionError'))
    assert discovery.can_refresh_known(Page(source, '', False, False, 'net::ERR_CONNECTION_RESET'))
    unconfigured = deepcopy(source)
    unconfigured['follow'].pop('refresh_known_on_listing_failure')
    assert not discovery.can_refresh_known(Page(unconfigured, '', False, False, 'ConnectionError'))

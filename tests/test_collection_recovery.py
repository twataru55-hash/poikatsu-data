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

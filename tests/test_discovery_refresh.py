import hashlib
from copy import deepcopy
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

import discovery
from collect import html_to_text
from common import load_json, SCHEMA_DIR

TEXT = 'キャンペーン期間2026年10月1日～10月31日。カード提示でポイントがもらえます。対象店舗と詳細条件をよく確認してください。'
SOURCE = {'id':'test','name':'test','url':'https://official.example/list/',
          'domains':['official.example'],'render':'static',
          'follow':{'pattern':r'^https://official\.example/cp/[ab]/$', 'max_new':1}}


class Fetcher:
    last_url = None
    def __init__(self): self.urls=[]
    def allowed(self,url): return True
    def get_static(self,url):
        self.urls.append(url)
        return '<main>'+TEXT+'</main>'


def run(tmp_path, monkeypatch, snapshots=None, force=False, urls=None):
    monkeypatch.setattr(discovery,'WORK_DIR',tmp_path)
    f=Fetcher(); audit={}
    page=SimpleNamespace(source=SOURCE,links=[('detail',u) for u in (urls or ['https://official.example/cp/a/'])])
    got,failed=discovery.discover_and_refresh(page,{},snapshots or {},f,force,audit)
    return got,failed,f,audit


def test_changed_processed_detail_is_reread(tmp_path,monkeypatch):
    sid=discovery.source_for_detail(SOURCE,'https://official.example/cp/a/')['id']
    got,failed,f,_=run(tmp_path,monkeypatch,{sid:{'hash':'old'}})
    assert len(got)==1 and not failed and len(f.urls)==1


def test_unchanged_detail_is_checked_without_duplicate_answer(tmp_path,monkeypatch):
    sid=discovery.source_for_detail(SOURCE,'https://official.example/cp/a/')['id']
    digest=hashlib.sha256(TEXT.encode()).hexdigest()
    got,failed,f,audit=run(tmp_path,monkeypatch,{sid:{'hash':digest}})
    assert not got and not failed and f.urls and audit['unchanged']==[sid]


def test_force_includes_processed_unchanged_detail(tmp_path,monkeypatch):
    sid=discovery.source_for_detail(SOURCE,'https://official.example/cp/a/')['id']
    got,_,_,_=run(tmp_path,monkeypatch,{sid:{'hash':hashlib.sha256(TEXT.encode()).hexdigest()}},True)
    assert len(got)==1


def test_budget_rotates_and_reports_unvisited(tmp_path,monkeypatch):
    urls=['https://official.example/cp/a/','https://official.example/cp/b/']
    _,_,first,audit=run(tmp_path,monkeypatch,urls=urls)
    _,_,second,_=run(tmp_path,monkeypatch,urls=urls)
    assert first.urls!=second.urls and audit['unvisited']==[urls[1]]
    assert len(audit['discovered'])==2


def test_unapproved_redirect_target_is_not_requested():
    f=Fetcher()
    with pytest.raises(RuntimeError):
        discovery.resolve_official_link('https://other.example/x',['tracker.example'],{'official.example'},f)
    assert f.urls==[]


def test_related_panel_removed_but_terms_kept():
    html='<main><section>主企画と注意事項</section><section id="related">別企画</section></main>'
    assert html_to_text(html,{'exclude_selectors':['#related']})=='主企画と注意事項'


def test_missing_content_selector_is_failure():
    with pytest.raises(RuntimeError): html_to_text('<main>nav</main>',{'content_selector':'#campaign'})


def test_unbranded_coupon_requires_named_store_and_other_campaign_requires_brand():
    from test_unknown_fields import BASE
    schema=Draft202012Validator(load_json(SCHEMA_DIR/'campaign.schema.json'))
    c=deepcopy(BASE);c.update(type='coupon',brand_ids=[])
    c['scope'].update(kind='stores',store_ids=['lawson'])
    assert not list(schema.iter_errors(c))
    c['scope']['kind']='payment_wide'
    assert list(schema.iter_errors(c))
    c['scope'].update(kind='stores',store_ids=['lawson']);c['type']='reward'
    assert list(schema.iter_errors(c))


def test_manual_catchup_does_not_mutate_daily_follow_configuration():
    from prepare import with_detail_limit
    sources = [deepcopy(SOURCE), {'id': 'direct', 'url': 'https://official.example/'}]
    larger = with_detail_limit(sources, 120)
    assert larger[0]['follow']['max_new'] == 120
    assert sources[0]['follow']['max_new'] == 1
    assert 'follow' not in larger[1]
    assert with_detail_limit(sources, None) is sources


@pytest.mark.parametrize('limit', [0, 301])
def test_catchup_budget_rejects_unbounded_fetch(limit):
    from prepare import with_detail_limit
    with pytest.raises(ValueError):
        with_detail_limit([SOURCE], limit)

import pytest
from discovery import unwrap_official_link

def test_known_wrapper_resolves_only_official_destination():
    url='https://dpoint.docomo.ne.jp/cp_7/dpc_lp_rdt_common/index.html?redirectID=https%3A%2F%2Fwww.docomo.ne.jp%2Fcampaign_event%2Ftokai%2Fexample%2F'
    assert unwrap_official_link(url, {'docomo.ne.jp'}) == 'https://www.docomo.ne.jp/campaign_event/tokai/example/'

@pytest.mark.parametrize('target', ['https%3A%2F%2Fevil.test%2F', 'http%3A%2F%2Fwww.docomo.ne.jp%2F'])
def test_wrapper_rejects_nonofficial_or_insecure_destination(target):
    with pytest.raises(ValueError):
        unwrap_official_link('https://dpoint.docomo.ne.jp/cp_7/dpc_lp_rdt_common/direct.html?redirectID='+target, {'docomo.ne.jp'})

def test_unrelated_url_is_unchanged():
    url='https://dpoint.docomo.ne.jp/cp_7/offer/index.html?redirectID=https%3A%2F%2Fevil.test%2F'
    assert unwrap_official_link(url, {'docomo.ne.jp'}) == url

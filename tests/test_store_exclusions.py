from copy import deepcopy

import pytest
from candidates import normalize, match_existing
from test_unknown_fields import raw_candidate
from test_validate import BASE, SOURCE, ctx
from validate import validate_candidate


def test_exclusion_survives_and_is_a_material_update():
    raw = raw_candidate(BASE)
    before = normalize(raw, SOURCE)
    raw['scope']['excluded_store_ids'] = ['store-a', 'store-a']
    after = normalize(raw, SOURCE)
    assert after['scope']['excluded_store_ids'] == ['store-a']
    assert match_existing(after, [before]) == ('changed', before)
    assert validate_candidate(after, ctx(only={'V03'})) == ('accept', [])
    assert 'excluded_store_ids' not in before['scope']  # Existing bundles remain valid.


def test_unregistered_excluded_store_is_held():
    raw = raw_candidate(BASE)
    raw['scope']['excluded_store_ids'] = ['not-registered']
    decision, reasons = validate_candidate(normalize(raw, SOURCE), ctx(only={'V03'}))
    assert decision == 'hold' and any('対象外店' in r for r in reasons)


@pytest.mark.parametrize('bad', ['store-a', None, [None], [12], {}])
def test_bad_exclusion_shape_is_not_silently_coerced(bad):
    raw = raw_candidate(BASE)
    raw['scope']['excluded_store_ids'] = bad
    assert validate_candidate(normalize(raw, SOURCE), ctx())[0] == 'discard'

import copy
import json
from pathlib import Path

import pytest

from trading.momentum_ls01.goldcheck import check_targets

FIXTURE=json.loads((Path(__file__).parent/'fixtures/momentum_ls01/anchors.json').read_text())


def test_goldcheck_compares_all_names_and_quantities():
    result=check_targets(FIXTURE['cases'], FIXTURE['lifetimes'])
    assert result['status']=='PASS' and result['anchors']==6
    bad=copy.deepcopy(FIXTURE['cases']);sym=next(iter(bad[0]['expected']));bad[0]['expected'][sym]*=1.01
    with pytest.raises(ValueError,match='GC2'):
        check_targets(bad,FIXTURE['lifetimes'])


def test_goldcheck_rejects_missing_target_instead_of_comparing_intersection():
    bad=copy.deepcopy(FIXTURE['cases']);bad[0]['expected'].pop(next(iter(bad[0]['expected'])))
    with pytest.raises(ValueError,match='GC2'):
        check_targets(bad,FIXTURE['lifetimes'])

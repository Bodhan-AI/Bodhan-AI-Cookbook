import json
import pytest
from bodhan_dub.fit_resume import chain_cost
from bodhan_dub.media import MediaError


def test_cost_chain_uses_journal_after_retry_not_stale_manifest(tmp_path):
    first=tmp_path/'first';second=tmp_path/'second';first.mkdir();second.mkdir()
    (first/'fit_manifest.json').write_text(json.dumps({'combined_estimated_cost_inr':'1'}))
    (first/'budget.json').write_text(json.dumps({'conservative_spent_inr':'1.2'}))
    (second/'fit_manifest.json').write_text(json.dumps({'previous_fit':str(first),'combined_estimated_cost_inr':'1.5'}))
    (second/'budget.json').write_text(json.dumps({'conservative_spent_inr':'.5'}))
    assert str(chain_cost(second))=='1.7'
    (first/'fit_manifest.json').write_text(json.dumps({'previous_fit':str(second)}))
    with pytest.raises(MediaError):chain_cost(second)

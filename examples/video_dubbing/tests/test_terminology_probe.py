import json
from decimal import Decimal
import pytest

from bodhan_dub.media import MediaError
from bodhan_dub.terminology_probe import main,run,substitute_term


def test_exact_term_substitution_preserves_all_surrounding_text():
    before='முன் ADJACENT ANGLES பின்.'
    supplied='சோதனை'
    assert substitute_term(before,'ADJACENT ANGLES',supplied)=='முன் '+supplied+' பின்.'


@pytest.mark.parametrize('source,original,term',[
 ('ADJACENT ANGLES','ADJACENT ANGLES','adjacent angles'),
 ('ADJACENT ANGLES','ADJACENT ANGLES',''),
 ('missing','ADJACENT ANGLES','தமிழ்'),
 ('X X','X','தமிழ்'),
 ('X','X','க'*81),
 ('க'*139+'X','X','தமிழ்'),
])
def test_unusable_term_or_changed_source_rejected(source,original,term):
    with pytest.raises(MediaError):substitute_term(source,original,term)


def test_dry_run_makes_no_calls_or_outputs(tmp_path,monkeypatch,capsys):
    monkeypatch.setattr('bodhan_dub.terminology_probe.translation_key',lambda *_:pytest.fail('No keys'))
    assert main(['--source-plan',str(tmp_path/'missing'),'--out',str(tmp_path/'new')])==0
    assert json.loads(capsys.readouterr().out)['hosted_calls']==0 and not (tmp_path/'new').exists()


@pytest.mark.parametrize('budget',[Decimal('NaN'),Decimal('-.1'),Decimal('.01')])
def test_invalid_budget_rejected_before_files_or_calls(tmp_path,budget):
    with pytest.raises(MediaError):run(tmp_path/'missing',tmp_path/'out',None,budget)
    assert not (tmp_path/'out').exists()

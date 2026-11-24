import json
from types import SimpleNamespace
import pytest

from bodhan_dub.meaning_checks import explicit_numbers,normal_source,source_copy_issue,judge
from bodhan_dub.media import MediaError
from bodhan_dub.reconciliation import LLMSettings
from bodhan_dub.translation import TranslationClient,TranslationSettings
from bodhan_dub.translation import glossary_present
from bodhan_dub.transcription import Response


def test_expand_hundreds_only_with_angle_or_arithmetic_evidence():
    source='An angle is one eighty degrees. Three sixty minus one twenty equals two forty degrees.'
    result=explicit_numbers(source)
    assert result=='An angle is one hundred and eighty degrees. three hundred and sixty minus one hundred and twenty equals two hundred and forty degrees.'
    assert explicit_numbers('Meet at one twenty for the class.')=='Meet at one twenty for the class.'
    assert explicit_numbers('Use one common arm and two angles.')=='Use one common arm and two angles.'
    assert explicit_numbers('One hundred and ten degrees.')=='One hundred and ten degrees.'


def test_long_english_echo_is_flagged_but_terms_and_labels_are_allowed():
    assert source_copy_issue('THREE OR MORE POINTS THAT LIE ON THE SAME LINE ARE COLLINEAR.','অর্থ THREE OR MORE POINTS THAT LIE ON THE SAME LINE ARE COLLINEAR।')
    assert not source_copy_issue('The angles AOC and COB are adjacent.','কোণ AOC ও COB সন্নিহিত।')
    assert 'AOC' in normal_source('HERE ANGLE AOC IS ONE TWENTY DEGREES.')


def test_back_translation_allows_words_to_digits_but_forward_guard_is_preserved(tmp_path):
    body={'choices':[{'finish_reason':'stop','message':{'content':'<bodhan_segments><segment id="u1">The angle is 240 degrees.</segment></bodhan_segments>'}}],'usage':{'completion_tokens':8}}
    transport=lambda _:Response(200,json.dumps(body).encode())
    pacer=SimpleNamespace(wait=lambda:None,observe=lambda _:None)
    c=TranslationClient('fake',tmp_path/'back',TranslationSettings(attempts=1),transport=transport,pacer=pacer,literal_numbers=False)
    r=c.translate([{'id':'u1','text':'கோணம் இருநூற்று நாற்பது டிகிரி.'}],'en')
    assert not r['errors'] and '240' in r['units']['u1']['translation']
    c=TranslationClient('fake',tmp_path/'forward',TranslationSettings(attempts=1),transport=transport,pacer=pacer)
    assert c.translate([{'id':'u1','text':'கோணம் இருநூற்று நாற்பது டிகிரி.'}],'en')['errors']['u1']=='literal_number_mismatch'


def test_missing_judge_identity_is_not_accepted(tmp_path,monkeypatch):
    monkeypatch.setattr('bodhan_dub.meaning_checks.RequestPacer',lambda _:SimpleNamespace(wait=lambda:None))
    settings=LLMSettings('https://example.test/v1','test','fake')
    with pytest.raises(MediaError):judge([{'id':'hi/u1','original':'First heading.','back_translation':'Heading.'}],settings,tmp_path,lambda *_:('{"checks":[]}',None))


def test_distinct_glossary_terms_cannot_be_satisfied_by_a_substring():
    assert glossary_present('पूरक कोण और संपूरक कोण',['पूरक','संपूरक'])
    assert not glossary_present('संपूरक कोण और संपूरक कोण',['पूरक','संपूरक'])
    assert glossary_present('நிரப்புக் கோணங்கள், மிகைநிரப்புக் கோணங்கள்',['நிரப்பு','மிகை நிரப்பு'])


@pytest.mark.parametrize('category,accepted',[('term_uncertainty',True),('factual_error',False)])
def test_glossary_can_resolve_name_uncertainty_but_never_factual_errors(tmp_path,monkeypatch,category,accepted):
    from bodhan_dub import meaning_checks as checks
    monkeypatch.setattr(checks,'charged_translate',lambda *_:{'units':{'u1':{'translation':'Complementary angles sum to ninety. Complementary angles sum to one hundred and eighty.'}}})
    monkeypatch.setattr(checks,'judge',lambda *_:{'hi/u1':{'id':'hi/u1','ok':False,'category':category,'issues':['Term differs.']}})
    items=[{'id':'u1','language':'hi','original_english':'Complementary and supplementary angles.','translation':'पूरक कोण और संपूरक कोण।'}]
    result=checks.assess_pairs(items,None,None,None,tmp_path,reference_glossary={'complementary':{'hi':'पूरक'},'supplementary':{'hi':'संपूरक'}})
    assert result['hi/u1']['ok'] is accepted


def test_back_translation_failure_keeps_other_language_checks(tmp_path,monkeypatch):
    from bodhan_dub import meaning_checks as checks
    def translate(_client,rows,*_):
        if rows[0]['text']=='हिंदी':raise MediaError('fixture_failure','Back translation unavailable.')
        return {'units':{'u1':{'translation':'First instruction.'}}}
    monkeypatch.setattr(checks,'charged_translate',translate)
    monkeypatch.setattr(checks,'judge',lambda rows,*_:{r['id']:{'id':r['id'],'ok':True,'issues':[]} for r in rows})
    items=[{'id':'u1','language':lang,'original_english':'First instruction.','translation':text} for lang,text in [('hi','हिंदी'),('ta','தமிழ்')]]
    result=checks.assess_pairs(items,None,None,None,tmp_path)
    assert not result['hi/u1']['ok'] and result['hi/u1']['verification_error']=='fixture_failure'
    assert result['ta/u1']['ok']


def test_verified_term_ids_only_change_verification_copy_not_output():
    from bodhan_dub.meaning_checks import verification_terms
    native='நிரப்பு கோணங்கள் தொண்ணூறு. மிகை நிரப்பு கோணங்கள் நூற்று எண்பது.'
    source='Complementary angles sum to ninety. Supplementary angles sum to one hundred and eighty.'
    glossary={'complementary':{'ta':'நிரப்பு'},'supplementary':{'ta':'மிகை நிரப்பு'}}
    english,copy,ids=verification_terms(source,native,'ta',glossary)
    assert english.startswith('CONCEPT_AA') and 'CONCEPT_AB' in english
    assert copy.startswith('CONCEPT_AA') and 'CONCEPT_AB' in copy
    assert ids=={'CONCEPT_AA':'complementary','CONCEPT_AB':'supplementary'}
    assert native.startswith('நிரப்பு')
    bad='மிகை நிரப்பு கோணங்கள் தொண்ணூறு. மிகை நிரப்பு கோணங்கள் நூற்று எண்பது.'
    assert verification_terms(source,bad,'ta',glossary)==(source,bad,{})


def test_cached_request_refusal_releases_only_new_reservation(tmp_path):
    from bodhan_dub.meaning_checks import charged_translate
    from bodhan_dub.fit_recovery import Budget
    from decimal import Decimal
    budget=Budget(tmp_path/'budget.json',Decimal('1'))
    budget.reserve('earlier_uncertain_request',Decimal('.1'))
    def refuse(*_):
        raise MediaError('previous_translation_attempt','No transport was called.')
    client=SimpleNamespace(settings=TranslationSettings(attempts=1),translate=refuse)
    with pytest.raises(MediaError,match='No transport'):
        charged_translate(client,[],'bn',budget,'new_attempt')
    assert budget.spent==Decimal('.1')
    assert budget.events[0]['state']=='reserved_if_billed'


def test_check_array_is_normalized_but_unrelated_json_is_not_accepted(tmp_path):
    from bodhan_dub.meaning_checks import llm_json
    settings=LLMSettings('https://example.test/v1','test','fake');pacer=SimpleNamespace(wait=lambda:None)
    result=llm_json(settings,[],tmp_path/'valid.json',pacer,lambda *_:('[{"id":"hi/u1","ok":true,"issues":[]}]',None))
    assert result['checks'][0]['id']=='hi/u1'
    with pytest.raises(MediaError):llm_json(settings,[],tmp_path/'bad.json',pacer,lambda *_:('["unrelated"]',None))


def test_malformed_english_json_is_retried_with_a_bound(tmp_path):
    from bodhan_dub.meaning_checks import llm_json
    calls=[]
    def complete(*_):
        calls.append(True);return ('{"checks":' if len(calls)==1 else '{"checks":[]}',None)
    result=llm_json(LLMSettings('https://example.test/v1','test','fake'),[],tmp_path/'review.json',SimpleNamespace(wait=lambda:None),complete)
    assert result=={'checks':[]} and len(calls)==2


def test_shared_review_cache_keeps_each_runs_report_separate(tmp_path,monkeypatch):
    from bodhan_dub import meaning_checks as checks
    monkeypatch.setattr(checks,'charged_translate',lambda *_:{'units':{'u1':{'translation':'Open your notebook.'}}})
    calls=[]
    def complete(*_):
        calls.append(True);return '{"checks":[{"id":"hi/u1","ok":true,"issues":[]}]}',None
    items=[{'id':'u1','language':'hi','original_english':'Open your notebook.','translation':'अपनी नोटबुक खोलें।'}]
    settings=LLMSettings('https://example.test/v1','model','fake')
    for folder in ('first','second'):
        result=checks.assess_pairs(items,None,None,settings,tmp_path/folder,completion=complete,review_cache=tmp_path/'shared')
        assert result['hi/u1']['ok'] and (tmp_path/folder/'meaning_report.json').exists()
    assert len(calls)==1



def test_english_reviewer_receives_full_term_identity_without_native_text(tmp_path,monkeypatch):
    from bodhan_dub import meaning_checks as checks
    monkeypatch.setattr(checks,'charged_translate',lambda *_:{'units':{'u1':{'translation':'Name one CONCEPT_AA.'}}})
    native='एक रैखिक युग्म का नाम बताओ।'
    item={'id':'u1','language':'hi','original_english':'Name one linear pair.','translation':native}
    glossary={'linear pair':{'hi':'रैखिक युग्म'}}
    def complete(_settings,messages):
        rows=json.loads(messages[1]['content'])
        assert rows[0]['verified_term_identities']=={'CONCEPT_AA':'linear pair'}
        assert native not in messages[1]['content'] and 'रैखिक' not in messages[1]['content']
        return '{"checks":[{"id":"hi/u1","ok":true,"issues":[]}]}',None
    result=checks.assess_pairs([item],None,None,LLMSettings('https://example.test/v1','model','fake'),tmp_path,completion=complete,reference_glossary=glossary)
    assert result['hi/u1']['ok'] and item['translation']==native


def test_malformed_batch_recovers_missing_back_translations(tmp_path, monkeypatch):
    from bodhan_dub import meaning_checks as checks
    calls = []
    def translate(client, rows, *args):
        calls.append([row['id'] for row in rows])
        if len(rows) > 1:
            error = MediaError('invalid_translation_structure', 'Malformed response')
            error.partial_result = {'units': {'a': {'translation': 'First instruction.'}}}
            raise error
        return {'units': {'b': {'translation': 'Second instruction.'}}}
    monkeypatch.setattr(checks, 'charged_translate', translate)
    monkeypatch.setattr(checks, 'judge', lambda rows, *_: {
        row['id']: {'id': row['id'], 'ok': True, 'issues': []} for row in rows})
    items = [{'id': key, 'language': 'hi', 'translation': 'पाठ', 'original_english': source}
             for key, source in [('a', 'First instruction.'), ('b', 'Second instruction.')]]
    results = checks.assess_pairs(items, None, None, None, tmp_path)
    assert calls == [['a', 'b'], ['b']]
    assert all(result['ok'] for result in results.values())


def test_review_batches_preserve_order_and_bound_large_inputs():
    from bodhan_dub.meaning_checks import review_batches
    rows = [{'id': str(i), 'original': 'short'} for i in range(14)]
    rows.append({'id': 'long', 'original': 'x' * 6200})
    groups = list(review_batches(rows))
    assert [len(group) for group in groups] == [12, 2, 1]
    assert [row for group in groups for row in group] == rows

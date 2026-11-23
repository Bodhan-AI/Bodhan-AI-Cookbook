import json
import pytest
from bodhan_dub import pipeline
from bodhan_dub.media import MediaError
from bodhan_dub.reconciliation import LLMSettings,source_windows
from bodhan_dub.source_punctuation import punctuate_source


def source():
    return source_windows([{'id':'a','start_seconds':0,'end_seconds':7,'text':'An acute angle is less than ninety degrees a right'},
        {'id':'b','start_seconds':6,'end_seconds':12,'text':'angle is exactly ninety degrees'}])


def test_sentence_crosses_audio_window_without_changing_words(tmp_path):
    windows=source();draft=pipeline.deterministic_draft(windows)
    text='An acute angle is less than ninety degrees. A right angle is exactly ninety degrees.'
    result=punctuate_source(windows,draft,LLMSettings('https://example.test/v1','model','fake'),tmp_path,
        lambda *_:(json.dumps({'text':text}),None))
    assert [s['text'] for s in result['sentences']]==['An acute angle is less than ninety degrees.','a right angle is exactly ninety degrees.']
    assert len(result['sentences'][1]['source_spans'])==2
    assert result['punctuation_planning']['source_tokens']==15


@pytest.mark.parametrize('proposal',[{'text':'An acute angle is small.'},{'ends':[7,14]},{'text':'An acute angle (is less than ninety degrees) a right angle is exactly ninety degrees.'},{'text':None}])
def test_invalid_boundaries_cannot_silently_change_source(tmp_path,proposal):
    windows=source();draft=pipeline.deterministic_draft(windows)
    with pytest.raises(MediaError):punctuate_source(windows,draft,LLMSettings('https://example.test/v1','model','fake'),tmp_path,
        lambda *_:(json.dumps(proposal),None))


def test_bad_boundary_proposal_has_one_bounded_correction(tmp_path):
    windows=source();draft=pipeline.deterministic_draft(windows);calls=[]
    def completion(*_):
        calls.append(True)
        text='An acute angle is small.' if len(calls)==1 else 'An acute angle is less than ninety degrees. A right angle is exactly ninety degrees.'
        return json.dumps({'text':text}),None
    result=punctuate_source(windows,draft,LLMSettings('https://example.test/v1','model','fake'),tmp_path,completion)
    assert len(calls)==2 and result['sentences'][1]['text']=='a right angle is exactly ninety degrees.'


def test_model_omission_is_not_applied_to_actual_source_words(tmp_path):
    from bodhan_dub.reconciliation import WORD
    words=['word'+str(i) for i in range(60)]
    windows=source_windows([{'id':'a','start_seconds':0,'end_seconds':15,'text':' '.join(words)}])
    draft=pipeline.deterministic_draft(windows)
    proposed=' '.join(words[:30])+'. '+' '.join(words[31:])+'.'
    result=punctuate_source(windows,draft,LLMSettings('https://example.test/v1','model','fake'),tmp_path,
        lambda *_:(json.dumps({'text':proposed}),None))
    assert WORD.findall(result['text'])==words
    assert result['punctuation_planning']['proposal_word_edits_ignored']==1


def test_compound_hyphen_is_presentation_and_final_question_is_preserved(tmp_path):
    windows=source_windows([{'id':'a','start_seconds':0,'end_seconds':8,'text':'Do the non common arms form a line'}])
    result=punctuate_source(windows,pipeline.deterministic_draft(windows),LLMSettings('https://example.test/v1','model','fake'),tmp_path,
        lambda *_:(json.dumps({'text':'Do the non‑common arms form a line?'}),None))
    assert result['text']=='Do the non common arms form a line?'

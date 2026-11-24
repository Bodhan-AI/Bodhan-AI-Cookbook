import json
from types import SimpleNamespace
import pytest

from bodhan_dub.configuration import glossary_configuration,voice_configuration,mapping_file
from bodhan_dub.media import MediaError
from bodhan_dub.translation import TranslationClient,TranslationSettings
from bodhan_dub.transcription import Response


def test_safe_yaml_and_bounded_voice_and_glossary(tmp_path):
    path=tmp_path/'voices.yaml';path.write_text('hi: Kavya\nta: Anitha\n')
    assert voice_configuration(path,['hi','ta'])=={'hi':'Kavya','ta':'Anitha'}
    with pytest.raises(MediaError):voice_configuration(path,['hi'])
    path.write_text('hypotenuse:\n  ta: கர்ணம்\n')
    assert glossary_configuration(path)=={'hypotenuse':{'ta':'கர்ணம்'}}
    path.write_text('!!python/object/apply:os.system [echo unsafe]')
    with pytest.raises(MediaError):mapping_file(path)
    path.write_text('word:\n  xx: invalid\n')
    with pytest.raises(MediaError):glossary_configuration(path)
    path.write_text('x'*65537)
    with pytest.raises(MediaError):mapping_file(path)


def client(tmp_path,transport,**kwargs):
    return TranslationClient('fake',tmp_path,TranslationSettings(attempts=1),transport=transport,
        pacer=SimpleNamespace(wait=lambda:None,observe=lambda _:None),**kwargs)


def test_glossary_context_never_becomes_synthesis_text(tmp_path):
    calls=[]
    def transport(request):
        body=json.loads(request.body);calls.append(body)
        text='<bodhan_segments><segment id="__glossary_context">சொல் சூழல்</segment><segment id="u1">கர்ணம் வரை.</segment></bodhan_segments>'
        return Response(200,json.dumps({'choices':[{'finish_reason':'stop','message':{'content':text}}],'usage':{'completion_tokens':10}}).encode())
    c=client(tmp_path,transport,glossary={'hypotenuse':{'ta':'கர்ணம்'}})
    r=c.translate([{'id':'u1','text':'Draw the hypotenuse.'}],'ta')
    assert set(r['units'])=={'u1'} and not r['errors']
    assert '__glossary_context' in calls[0]['messages'][0]['content']
    assert 'கர்ணம்' in calls[0]['messages'][0]['content']


def test_documented_code_mixed_request_and_truncation_guard(tmp_path):
    seen=[]
    def transport(request):
        seen.append(request);return Response(200,json.dumps({'translation':'<bodhan_segments><segment id="u1">notebook திறக்கவும்.</segment></bodhan_segments>',
            'target_language':'ta','script':'codemix','truncated':False,'usage':{'completion_tokens':5}}).encode())
    c=client(tmp_path/'good',transport,code_mixed=True)
    r=c.translate([{'id':'u1','text':'Open your notebook.'}],'ta')
    assert not r['errors'] and set(r['units'])=={'u1'}
    assert seen[0].url=='https://api.bodhan.ai/translate'
    assert json.loads(seen[0].body)['script']=='codemix'
    def truncated(request):return Response(200,json.dumps({'translation':'partial','target_language':'ta','script':'codemix','truncated':True}).encode())
    with pytest.raises(MediaError):client(tmp_path/'bad',truncated,code_mixed=True).translate([{'id':'u1','text':'Open your notebook.'}],'ta')
    with pytest.raises(MediaError):c.translate([{'id':'u1','text':'Open your notebook.'}],'en')

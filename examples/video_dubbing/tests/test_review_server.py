from functools import partial
from http.server import ThreadingHTTPServer
import threading
import urllib.error
import urllib.request

import pytest

from bodhan_dub.serve_review import ReviewHandler


@pytest.fixture
def server(tmp_path):
    (tmp_path/'sample.mp3').write_bytes(bytes(range(100)))
    service=ThreadingHTTPServer(('127.0.0.1',0),partial(ReviewHandler,directory=str(tmp_path)))
    thread=threading.Thread(target=service.serve_forever,daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{service.server_port}/sample.mp3'
    service.shutdown();service.server_close();thread.join()


@pytest.mark.parametrize('header,start,end',[('bytes=0-9',0,9),('bytes=90-',90,99),('bytes=-5',95,99),('bytes=95-200',95,99)])
def test_audio_range_response(server,header,start,end):
    request=urllib.request.Request(server,headers={'Range':header})
    with urllib.request.urlopen(request,timeout=3) as response:
        assert response.status==206
        assert response.headers['Content-Range']==f'bytes {start}-{end}/100'
        assert response.read()==bytes(range(start,end+1))
        assert response.headers['Content-Type']=='audio/mpeg'


@pytest.mark.parametrize('header',['bytes=100-','bytes=5-2','bytes=-0','bytes=0-1,3-4'])
def test_invalid_audio_range(server,header):
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(urllib.request.Request(server,headers={'Range':header}),timeout=3)
    assert error.value.code==416


def test_full_audio_and_head(server):
    with urllib.request.urlopen(server,timeout=3) as response:
        assert response.status==200 and response.read()==bytes(range(100))
    with urllib.request.urlopen(urllib.request.Request(server,method='HEAD'),timeout=3) as response:
        assert response.headers['Accept-Ranges']=='bytes'
        assert response.read()==b''

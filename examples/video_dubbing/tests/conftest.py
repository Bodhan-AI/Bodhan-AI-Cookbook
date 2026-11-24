import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from fake_bodhan import FakeBodhan  # noqa: E402
from make_media import make_video  # noqa: E402

from bodhan_dub import config, logs  # noqa: E402
from bodhan_dub.bodhan import BodhanClient  # noqa: E402
from bodhan_dub.llm import Brain  # noqa: E402
from bodhan_dub.pipeline import Options, run_pipeline  # noqa: E402


@pytest.fixture(scope="session")
def video(tmp_path_factory):
    return make_video(tmp_path_factory.mktemp("media") / "in.mp4")


@pytest.fixture(scope="session")
def video30(tmp_path_factory):
    return make_video(tmp_path_factory.mktemp("media30") / "clip30.mp4", bursts=((1, 5), (7, 13), (15, 19), (22, 28)), duration=30.0)


@pytest.fixture(scope="session")
def video_text(tmp_path_factory):
    """Dark 640x360 video: 'Revenue grew' on the left from 4 s to 9 s, 'Open source' on the right 10 s to 14 s."""
    return make_video(tmp_path_factory.mktemp("mediat") / "txt.mp4", duration=18.0,
                      texts=(("Revenue grew", 40, 150, 4, 9), ("Open source", 360, 250, 10, 14)))


class FakeLLM:
    """Stands in for an OpenAI-compatible endpoint. It only ever sees and returns English."""

    def __init__(self, shorten=True, bad_groups=False):
        self.shorten, self.bad_groups, self.calls = shorten, bad_groups, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, model, messages, temperature=0):
        sys_p, user = messages[0]["content"], messages[1]["content"]
        self.calls.append(sys_p[:30])
        if "regroup" in sys_p:
            out = json.dumps({"groups": [[0, 0], [1]]}) if self.bad_groups else json.dumps({"groups": [[i] for i in range(user.count("\n") + 1)]})
        elif "List terms" in sys_p:
            out = json.dumps({"terms": [{"term": "sentence", "kind": "term", "pin": True}]})
        elif "Rewrite" in sys_p:
            out = "Short one." if self.shorten else user
        else:
            out = json.dumps({"ok": True})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=out))])


@pytest.fixture
def run(tmp_path, video, video30):
    def _run(langs=("hi", "ta"), fake=None, llm=None, out=None, clip30=False, src=None, ocr_key="", **kw):
        fake = fake or FakeBodhan()
        out = out or tmp_path / "out"
        out.mkdir(exist_ok=True)
        logs.setup(out / "run.log.jsonl", echo=False)
        client = BodhanClient(config.Keys("a", "b", "c", ocr_key), out / "work" / "cache", max_concurrency=1, http=fake.client,
                              base_url="https://api.bodhan.ai", backoff=0.01)
        opt = Options(input=src or (video30 if clip30 else video), langs=list(langs), out=out, concurrency=1, **kw)
        rc = run_pipeline(opt, client, Brain(client=llm, model="fake"))
        report = json.loads((out / "report.json").read_text())
        return SimpleNamespace(rc=rc, out=out, fake=fake, report=report)
    return _run

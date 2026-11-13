from narrator.bodhan_client import _request_id


class FakeResponse:
    def __init__(self, headers):
        self.headers = headers


def test_request_id_is_found_under_either_header_name():
    assert _request_id(FakeResponse({"x-request-id": "a1"})) == "a1"
    assert _request_id(FakeResponse({"llm_provider-x-request-id": "b2"})) == "b2"
    assert _request_id(FakeResponse({})) is None
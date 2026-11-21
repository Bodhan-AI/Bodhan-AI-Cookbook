import httpx

from ..cache import Cache


class LLMClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        cache: Cache,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if not base_url:
            raise ValueError("LLM_BASE_URL is required")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.cache = cache
        self.http = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=60,
            transport=transport,
        )

    async def __aenter__(self) -> "LLMClient":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.http.aclose()

    async def chat(self, system: str, user: str, temperature: float = 0.0) -> str:
        response = await self.http.post(
            "/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": temperature,
            },
        )
        response.raise_for_status()
        body = response.json()
        choices = body.get("choices") if isinstance(body, dict) else None
        choice = choices[0] if isinstance(choices, list) and choices else None
        message = choice.get("message") if isinstance(choice, dict) else None
        text = message.get("content") if isinstance(message, dict) else None
        if not isinstance(text, str):
            raise ValueError("LLM response did not contain a string message.")
        return text

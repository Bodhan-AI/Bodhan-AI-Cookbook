"""Bodhan Translation Tool for LangChain Agents."""

from __future__ import annotations

from typing import Any, Optional

from langchain_core.callbacks import AsyncCallbackManagerForToolRun, CallbackManagerForToolRun
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from .client import BodhanAsyncClient, BodhanClient
from .exceptions import BodhanAPIError, BodhanValidationError
from .models import SUPPORTED_SCRIPTS, SUPPORTED_TRANSLATION_LANGUAGES


class BodhanTranslationInput(BaseModel):
    """Input parameters for the Bodhan translation tool."""

    text: str = Field(
        ...,
        description="The text to translate. Should not be empty.",
    )
    target_language: str = Field(
        ...,
        description=(
            "Target language ISO code. Supported codes include: "
            "as, bn, brx, doi, en, gu, hi, kn, kok, ks, ks-Deva, mai, ml, "
            "mni, mni-Beng, mr, ne, or, pa, sa, sat, sd, sd-Arab, ta, te, ur."
        ),
    )
    script: Optional[str] = Field(
        "native",
        description="Target script style: 'native' (default), 'roman' (Latin letters), or 'codemix'.",
    )


class BodhanTranslationTool(BaseTool):
    """LangChain tool for translating text between English and 22 Indian languages.

    Can be bound to LangChain chat models, ReAct agents, or tool-calling agents
    to give agents sovereign translation capabilities across Indian languages.

    Usage:
        ```python
        from bodhan_langchain import BodhanClient, BodhanTranslationTool

        client = BodhanClient()
        tool = BodhanTranslationTool(client=client)
        result = tool.invoke({"text": "Where is the library?", "target_language": "hi"})
        print(result)  # पुस्तकालय कहाँ है?
        ```
    """

    name: str = "bodhan_translate"
    description: str = (
        "Translates text between English and 22 Indian languages (Hindi, Tamil, Bengali, Telugu, "
        "Marathi, Gujarati, Kannada, Malayalam, Odia, Punjabi, etc.) using Bodhan AI foundation models. "
        "Source language is detected automatically."
    )
    args_schema: type[BaseModel] = BodhanTranslationInput

    client: Optional[BodhanClient] = None
    async_client: Optional[BodhanAsyncClient] = None
    transport: Optional[str] = None

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _get_client(self) -> BodhanClient:
        if self.client is not None:
            return self.client
        return BodhanClient()

    def _get_async_client(self) -> BodhanAsyncClient:
        if self.async_client is not None:
            return self.async_client
        return BodhanAsyncClient()

    def _run(
        self,
        text: str,
        target_language: str,
        script: str | None = "native",
        run_manager: CallbackManagerForToolRun | None = None,
        **kwargs: Any,
    ) -> str:
        """Execute translation synchronously."""
        client = self._get_client()
        try:
            resp = client.translate(
                text=text,
                target_language=target_language,
                script=script,
                transport=self.transport,  # type: ignore[arg-type]
            )
            return resp.text
        except (BodhanValidationError, BodhanAPIError) as err:
            return f"Error executing Bodhan translation: {err}"

    async def _arun(
        self,
        text: str,
        target_language: str,
        script: str | None = "native",
        run_manager: AsyncCallbackManagerForToolRun | None = None,
        **kwargs: Any,
    ) -> str:
        """Execute translation asynchronously."""
        async_client = self._get_async_client()
        try:
            resp = await async_client.translate(
                text=text,
                target_language=target_language,
                script=script,
                transport=self.transport,  # type: ignore[arg-type]
            )
            return resp.text
        except (BodhanValidationError, BodhanAPIError) as err:
            return f"Error executing Bodhan translation: {err}"

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
    target_language: Optional[str] = Field(
        None,
        description=(
            "Target language ISO code (e.g. 'hi', 'ta', 'bn', 'en', 'mr', 'te', 'gu'). "
            "Supports 22 Indian languages plus English across 26 language codes."
        ),
    )
    script: Optional[str] = Field(
        None,
        description=(
            "Target script style: 'native' (default for Indic targets), 'roman', or 'codemix'. "
            "Script selection does not apply to English."
        ),
    )


class BodhanTranslationTool(BaseTool):
    """LangChain tool for translating text across 22 Indian languages and English.

    Can be bound to LangChain chat models, ReAct agents, or tool-calling agents
    to give agents sovereign translation capabilities across Indian languages.

    Usage:
        ```python
        from bodhan_langchain import BodhanClient, BodhanTranslationTool

        client = BodhanClient()
        tool = BodhanTranslationTool(client=client, target_language="hi")
        result = tool.invoke({"text": "Where is the library?"})
        print(result)  # पुस्तकालय कहाँ है?
        ```
    """

    name: str = "bodhan_translate"
    description: str = (
        "Translates text across 22 Indian languages plus English (Hindi, Tamil, Bengali, Telugu, "
        "Marathi, Gujarati, Kannada, Malayalam, Odia, Punjabi, Assamese, etc.) using Bodhan AI models. "
        "Source language is detected automatically."
    )
    args_schema: type[BaseModel] = BodhanTranslationInput

    client: Optional[BodhanClient] = None
    async_client: Optional[BodhanAsyncClient] = None
    transport: Optional[str] = None
    target_language: str = "en"

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _get_client(self) -> tuple[BodhanClient, bool]:
        if self.client is not None:
            return self.client, False
        return BodhanClient(), True

    def _get_async_client(self) -> tuple[BodhanAsyncClient, bool]:
        if self.async_client is not None:
            return self.async_client, False
        return BodhanAsyncClient(), True

    def _run(
        self,
        text: str,
        target_language: str | None = None,
        script: str | None = None,
        run_manager: CallbackManagerForToolRun | None = None,
        **kwargs: Any,
    ) -> str:
        """Execute translation synchronously."""
        target = target_language or self.target_language
        script_to_use = ("native" if target != "en" else None) if script is None else script
        client, owns_client = self._get_client()
        try:
            resp = client.translate(
                text=text,
                target_language=target,
                script=script_to_use,
                transport=self.transport,  # type: ignore[arg-type]
            )
            return resp.text
        except (BodhanValidationError, BodhanAPIError) as err:
            return f"Error executing Bodhan translation: {err}"
        finally:
            if owns_client:
                client.close()

    async def _arun(
        self,
        text: str,
        target_language: str | None = None,
        script: str | None = None,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
        **kwargs: Any,
    ) -> str:
        """Execute translation asynchronously."""
        target = target_language or self.target_language
        script_to_use = ("native" if target != "en" else None) if script is None else script
        async_client, owns_client = self._get_async_client()
        try:
            resp = await async_client.translate(
                text=text,
                target_language=target,
                script=script_to_use,
                transport=self.transport,  # type: ignore[arg-type]
            )
            return resp.text
        except (BodhanValidationError, BodhanAPIError) as err:
            return f"Error executing Bodhan translation: {err}"
        finally:
            if owns_client:
                await async_client.aclose()

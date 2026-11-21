"""Grounded answer generation using LangChain and ChatGroq."""

from __future__ import annotations

from typing import List, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from .config import RAGConfig

GROUNDED_SYSTEM_PROMPT = """You are a document-grounded research assistant.

Use ONLY the information contained in the retrieved document passages provided below to answer the user's question.

STRICT OPERATIONAL RULES:
1. Do not invent facts, numbers, dates, amounts, eligibility criteria, or names.
2. Do not use external or unsupported knowledge.
3. Every factual claim in your answer MUST cite its supporting passage identifier (e.g. [S1], [S2]).
4. If the retrieved context is insufficient or does not contain enough information to answer the question truthfully, state clearly: "The retrieved documents do not contain enough information to answer this question."
5. Keep your answer direct, clear, objective, and well-structured.
6. Do not fabricate citation identifiers that are not present in the context.
7. Distinguish facts explicitly stated in the context from reasonable deductions."""


def get_chat_model(
    config: RAGConfig,
    mock_chat_model: Optional[BaseChatModel] = None,
) -> BaseChatModel:
    """Instantiate the companion LLM using ChatGroq or an injected mock model for testing.

    Args:
        config: RAG pipeline configuration.
        mock_chat_model: Optional mock BaseChatModel instance (for offline unit tests).

    Returns:
        BaseChatModel instance.

    Raises:
        ValueError: If GROQ_API_KEY is missing when initializing real ChatGroq.
        ImportError: If langchain-groq is not installed.
    """
    if mock_chat_model is not None:
        return mock_chat_model

    if not config.groq_api_key:
        raise ValueError(
            "GROQ_API_KEY is not set. Please set the GROQ_API_KEY environment variable "
            "to run real answer generation with Groq, or pass a mock chat model for testing."
        )

    try:
        from langchain_groq import ChatGroq

        kwargs = {
            "api_key": config.groq_api_key,
            "model": config.groq_model,
            "temperature": config.temperature,
            "timeout": config.timeout,
        }
        if config.max_output_tokens is not None:
            kwargs["max_tokens"] = config.max_output_tokens

        return ChatGroq(**kwargs)
    except ImportError as exc:
        raise ImportError(
            "langchain-groq is required for Groq answer generation. "
            "Please install it using: pip install langchain-groq"
        ) from exc


def build_grounded_messages(query: str, context_str: str) -> List[BaseMessage]:
    """Construct grounded chat messages containing system instructions, context, and query."""
    user_prompt = (
        f"Retrieved Document Passages:\n"
        f"{context_str}\n\n"
        f"User Question: {query}\n\n"
        f"Please provide a factual, grounded answer citing the passage IDs [S1], [S2] where applicable."
    )
    return [
        SystemMessage(content=GROUNDED_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]


def generate_grounded_answer(
    chat_model: BaseChatModel,
    query: str,
    context_str: str,
) -> str:
    """Invoke the chat model to generate an answer strictly grounded in retrieved passages.

    Args:
        chat_model: BaseChatModel instance (ChatGroq in production).
        query: English query string.
        context_str: Formatted context blocks with [S1], [S2] markers.

    Returns:
        String content of the model's generated response.
    """
    messages = build_grounded_messages(query, context_str)
    response = chat_model.invoke(messages)

    if isinstance(response, AIMessage):
        content = response.content
    elif hasattr(response, "content"):
        content = str(response.content)
    else:
        content = str(response)

    return content.strip()

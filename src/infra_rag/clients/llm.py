from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_groq import ChatGroq
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.messages import BaseMessage, HumanMessage
from typing import Any
import json

from infra_rag.config import settings


def create_llm(model_name: str) -> ChatOpenAI | ChatAnthropic | ChatGroq:
    if settings.llm.provider == "openai":
        return ChatOpenAI(
            model=model_name,
            api_key=settings.llm.openai_api_key or None,
            temperature=0,
            request_timeout=settings.llm.request_timeout_s,
            max_retries=settings.llm.max_retries,
        )
    elif settings.llm.provider == "anthropic":
        return ChatAnthropic(
            model=model_name,
            api_key=settings.llm.anthropic_api_key or None,
            temperature=0,
            timeout=settings.llm.request_timeout_s,
            max_retries=settings.llm.max_retries,
        )
    elif settings.llm.provider == "groq":
        return ChatGroq(
            model=model_name,
            groq_api_key=settings.llm.groq_api_key or None,
            temperature=0,
            timeout=settings.llm.request_timeout_s,
            max_retries=settings.llm.max_retries,
        )
    else:
        raise ValueError(f"Unknown LLM provider: {settings.llm.provider}")


def structured_output(llm: ChatOpenAI | ChatAnthropic | ChatGroq, schema: type, prompt: str) -> Any:
    # Modern approach: use with_structured_output if available
    try:
        if hasattr(llm, "with_structured_output"):
            structured_llm = llm.with_structured_output(schema)
            return structured_llm.invoke(prompt)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"with_structured_output failed: {e}. Falling back to manual parsing.")

    # Fallback to manual parsing for models/providers that don't support it well
    from langchain_core.output_parsers import PydanticOutputParser
    
    parser = PydanticOutputParser(pydantic_object=schema)
    formatted_prompt = f"{prompt}\n\n{parser.get_format_instructions()}"
    
    response = llm.invoke(formatted_prompt)
    content = response.content
    
    try:
        parsed = parser.parse(content)
        return schema(**parsed)
    except Exception:
        return _parse_json_fallback(content, schema)


def _parse_json_fallback(content: str, schema: type) -> Any:
    import re
    json_match = re.search(r'\{.*\}', content, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group())
            return schema(**data)
        except Exception:
            pass
    return None

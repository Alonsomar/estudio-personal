"""Wrappers para clientes de LLMs (Anthropic y OpenAI).

Leen las API keys desde variables de entorno configuradas en .env.
Uso:
    from shared.llm_clients import get_anthropic_client, get_openai_client

    client = get_anthropic_client()
    response = client.messages.create(...)
"""

from dotenv import load_dotenv

load_dotenv()

import anthropic
import openai


def get_anthropic_client() -> anthropic.Anthropic:
    """Retorna un cliente Anthropic configurado con la API key del .env."""
    return anthropic.Anthropic()


def get_openai_client() -> openai.OpenAI:
    """Retorna un cliente OpenAI configurado con la API key del .env."""
    return openai.OpenAI()

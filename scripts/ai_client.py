"""
Unified AI client for Gmail Classifier.

Supports any LLM provider via litellm. Configure with env vars:

  GMAIL_AI_MODEL=claude-haiku-4-5-20251001   # default (Anthropic)
  GMAIL_AI_MODEL=gpt-4o-mini                  # OpenAI
  GMAIL_AI_MODEL=gemini/gemini-1.5-flash      # Google
  GMAIL_AI_MODEL=groq/llama-3.1-8b-instant    # Groq
  GMAIL_AI_MODEL=ollama/llama3                # local Ollama

For Anthropic (default), API key resolution order:
  1. ~/.claude/settings.json apiKeyHelper (devbar / Claude Code gateway)
  2. ANTHROPIC_API_KEY environment variable
  3. LiteLLM's own provider env vars (OPENAI_API_KEY, GEMINI_API_KEY, etc.)

Usage:
  from ai_client import ai_completion, DEFAULT_MODEL

  text = ai_completion(
      prompt="Is this spam? Reply yes or no.",
      model=DEFAULT_MODEL,   # or any litellm model string
      max_tokens=64,
  )
"""

import json
import os
import subprocess
from pathlib import Path

DEFAULT_MODEL = os.environ.get('GMAIL_AI_MODEL', 'claude-haiku-4-5-20251001')


def _resolve_anthropic_creds() -> tuple:
    """Return (api_key, base_url) for Anthropic, trying devbar gateway first."""
    # 1. Claude Code settings.json — devbar / internal gateway
    settings_path = Path.home() / '.claude' / 'settings.json'
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
            base_url = settings.get('env', {}).get('ANTHROPIC_BASE_URL')
            helper = settings.get('apiKeyHelper')
            if helper and base_url:
                token = subprocess.check_output(
                    helper, shell=True, text=True, stderr=subprocess.DEVNULL
                ).strip()
                if token:
                    # Pass through any extra env vars the gateway needs
                    for k, v in settings.get('env', {}).items():
                        if k not in ('ANTHROPIC_API_KEY', 'ANTHROPIC_BASE_URL'):
                            os.environ.setdefault(k, v)
                    return token, base_url
        except Exception:
            pass

    # 2. Standard env var
    key = os.environ.get('ANTHROPIC_API_KEY', '')
    if key:
        return key, None

    return None, None


def ai_completion(prompt: str, model: str = None, max_tokens: int = 512) -> str:
    """
    Send a prompt to the configured LLM and return the response text.
    Raises RuntimeError if no credentials are available.
    """
    import litellm

    model = model or DEFAULT_MODEL

    # For Anthropic models, resolve credentials explicitly (supports devbar gateway)
    is_anthropic = (
        model.startswith('claude')
        or model.startswith('anthropic/')
        or 'anthropic' in model
    )

    kwargs = {
        'model': model,
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': max_tokens,
    }

    if is_anthropic:
        api_key, base_url = _resolve_anthropic_creds()
        if api_key:
            kwargs['api_key'] = api_key
        if base_url:
            kwargs['base_url'] = base_url
        elif not api_key:
            raise RuntimeError(
                'No Anthropic credentials found. Set ANTHROPIC_API_KEY or configure '
                '~/.claude/settings.json, or set GMAIL_AI_MODEL to use a different provider.'
            )

    response = litellm.completion(**kwargs)
    return response.choices[0].message.content.strip()


def make_client(model: str = None):
    """
    Returns a thin wrapper object with a messages.create()-compatible interface,
    for scripts that call client.messages.create() directly.
    Used as a drop-in replacement for anthropic.Anthropic().
    """
    model = model or DEFAULT_MODEL
    return _LiteLLMClient(model)


class _LiteLLMClient:
    """Minimal shim so existing call sites (client.messages.create) keep working."""

    def __init__(self, model: str):
        self.model = model
        self.messages = _Messages(model)


class _Messages:
    def __init__(self, model: str):
        self._model = model

    def create(self, model=None, messages=None, max_tokens=512, **_):
        text = ai_completion(
            prompt=messages[0]['content'] if messages else '',
            model=model or self._model,
            max_tokens=max_tokens,
        )
        return _Response(text)


class _Response:
    """Mimics anthropic.types.Message enough for existing code."""
    def __init__(self, text: str):
        self.content = [_Block(text)]


class _Block:
    def __init__(self, text: str):
        self.text = text

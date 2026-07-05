"""Benchmark configuration — model provider settings."""

import os

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "")

# OpenRouter model ids (verified live on OpenRouter). The previous default judge
# (google/gemini-2.0-flash-001) 404s — every run silently scored 0%.
ANSWER_MODELS = [
    "openai/gpt-4o",
    "google/gemini-2.5-flash",
]

JUDGE_MODEL = "google/gemini-2.5-flash"

OPENAI_ANSWER_MODELS = [
    "gpt-4o",
    "gpt-4o-mini",
]

OPENAI_JUDGE_MODEL = "gpt-4o-mini"

SEARCH_TOP_K = int(os.getenv("MEMDIO_TOPK", "10"))
MAX_MEMORY_CHARS = int(os.getenv("MEMDIO_MEMCHARS", "2000"))  # truncate each memory around relevant window

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
CHECKPOINTS_DIR = os.path.join(os.path.dirname(__file__), "checkpoints")

LONGMEMEVAL_URL = "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json"

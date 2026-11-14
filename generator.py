from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openai import OpenAI

from catalog import missing_fields, normalize_product


ENV_PATH = Path(__file__).with_name(".env")
PROMPT_PATH = Path(__file__).with_name("prompt.txt")
FACT_FIELDS = ("name", "manufacturer", "type", "description", "specs")
TEXT_LIMITS = {"seo_title": (1, 70), "seo_description": (0, 1200), "commercial": (1, 600), "reason": (0, 500)}

# Новый текстовый блок можно добавить в TEXT_LIMITS, общая проверка уже готова.
CARD_PROPERTIES = {field: {"type": "string"} for field in TEXT_LIMITS}
CARD_PROPERTIES["status"] = {"type": "string", "enum": ["draft", "needs_data"]}
CARD_PROPERTIES["search_queries"] = {"type": "array", "items": {"type": "string"}}
CARD_SCHEMA = {"type": "object", "properties": CARD_PROPERTIES, "required": list(CARD_PROPERTIES), "additionalProperties": False}
OUTPUT_FORMAT = {"type": "json_schema", "name": "product_card", "strict": True, "schema": CARD_SCHEMA}


def create_client() -> OpenAI:
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv(ENV_PATH, override=False)
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise ValueError("Заполните OPENAI_API_KEY в .env или переменных окружения.")
    return OpenAI(api_key=key, timeout=45.0, max_retries=0)


def validate_text(value: str, field: str, minimum: int, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field}: ожидается строка.")
    value = value.strip().replace("\u2014", "-")
    if not minimum <= len(value) <= maximum:
        raise ValueError(f"{field}: требуется от {minimum} до {maximum} символов, получено {len(value)}.")
    return value


def get_numbers(text: str) -> set[str]:
    return {number.replace(",", ".") for number in re.findall(r"\d+(?:[.,]\d+)?", text)}


def validate_card(data: dict, product: dict) -> dict:
    """Проверяет ответ модели; product должен быть уже нормализован."""
    if not isinstance(data, dict) or set(data) != set(CARD_PROPERTIES):
        raise ValueError("Ответ должен содержать только поля из JSON-схемы.")
    if data["status"] not in ("draft", "needs_data"):
        raise ValueError("status: ожидается draft или needs_data.")
    card = {field: validate_text(data[field], field, *limits) for field, limits in TEXT_LIMITS.items()}
    card["status"] = data["status"]
    if card["status"] == "draft" and len(card["seo_description"]) < 800:
        raise ValueError("seo_description: для draft нужно 800-1200 символов.")
    if card["status"] == "draft" and not (product["description"] or any(product["specs"].values())):
        raise ValueError("Нет описания и характеристик: верните status=needs_data.")
    if card["status"] == "needs_data" and not card["reason"]:
        raise ValueError("reason: объясните, каких данных не хватает.")
    queries = data["search_queries"]
    if not isinstance(queries, list) or not 3 <= len(queries) <= 5:
        raise ValueError("search_queries: требуется список из 3-5 запросов.")
    card["search_queries"] = [validate_text(query, "search_queries", 1, 200) for query in queries]
    if len({query.casefold() for query in card["search_queries"]}) != len(queries):
        raise ValueError("search_queries: запросы не должны повторяться.")
    facts = json.dumps({field: product[field] for field in FACT_FIELDS}, ensure_ascii=False)
    prose = " ".join([card["seo_title"], card["seo_description"], card["commercial"], *card["search_queries"]])
    new_numbers = get_numbers(prose) - get_numbers(facts)
    if new_numbers:
        raise ValueError(f"В ответе появились числа без источника: {', '.join(sorted(new_numbers))}.")
    card["id"] = product["id"]
    card["name"] = product["name"]
    card["specs"] = {name: value or "данных нет" for name, value in product["specs"].items()}
    card["missing_fields"] = missing_fields(product)
    return card


def request_card(client: OpenAI, model: str, prompt: str, payload: dict) -> str:
    from openai import APIConnectionError, APIStatusError, APITimeoutError

    try:
        response = client.responses.create(model=model, instructions=prompt, input=json.dumps(payload, ensure_ascii=False), text={"format": OUTPUT_FORMAT}, store=False, max_output_tokens=4000)
    except APITimeoutError:
        raise RuntimeError("API не ответил вовремя. Повторите запрос позже.") from None
    except APIConnectionError:
        raise RuntimeError("Не удалось подключиться к API. Проверьте соединение.") from None
    except APIStatusError as error:
        messages = {
            401: "API не принял ключ. Проверьте OPENAI_API_KEY.",
            403: "API запретил запрос. Проверьте доступ к модели и сервису.",
            404: "Модель не найдена. Проверьте OPENAI_MODEL.",
            429: "Достигнут лимит запросов или исчерпана квота API.",
        }
        raise RuntimeError(messages.get(error.status_code, f"Ошибка API: HTTP {error.status_code}.")) from None
    if response.status != "completed":
        raise RuntimeError("API вернул незавершённый ответ. Карточка не сформирована.")
    if any(part.type == "refusal" for item in response.output if item.type == "message" for part in item.content):
        raise RuntimeError("Модель отказалась формировать карточку.")
    if not response.output_text.strip():
        raise RuntimeError("Модель вернула пустой ответ.")
    return response.output_text


def generate_card(product: dict, client: OpenAI, model: str | None = None) -> dict:
    product = normalize_product(product)
    if not product["name"]:
        raise ValueError("Для генерации заполните name.")
    model = validate_text(model if model is not None else os.getenv("OPENAI_MODEL", ""), "OPENAI_MODEL", 1, 200)
    prompt = validate_text(PROMPT_PATH.read_text(encoding="utf-8"), "prompt.txt", 1, 10000)
    payload = {"product": {field: product[field] for field in FACT_FIELDS}, "correction": ""}
    for attempt in range(2):
        text = request_card(client, model, prompt, payload)
        try:
            return validate_card(json.loads(text), product)
        except ValueError as error:
            if attempt == 1:
                raise ValueError(f"Ответ модели не прошёл проверку после двух попыток: {error}") from None
            payload["correction"] = str(error)


def create_local_card(product: dict) -> dict:
    """Показывает проверяемую заглушку, не обращаясь к модели."""
    product = normalize_product(product)
    name = product["name"] or "Товар без названия"
    manufacturer = product["manufacturer"]
    queries = [name, f"{name} характеристики", f"{manufacturer or name} каталог"]
    sample = {
        "status": "needs_data",
        "seo_title": f"Локальный пример: {name}"[:70],
        "seo_description": "Локальная заглушка. Текст карточки не сгенерирован моделью.",
        "search_queries": queries,
        "commercial": "Демонстрационный пример для проверки формата карточки.",
        "reason": "Это локальный ответ-заглушка, а не результат генерации модели.",
    }
    return validate_card(sample, product)

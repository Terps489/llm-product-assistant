import json
from types import SimpleNamespace
from unittest.mock import Mock

import openai
import pytest
from openai import APIConnectionError, APIStatusError, APITimeoutError

import generator
from catalog import normalize_product
from generator import create_client, create_local_card, generate_card, get_numbers, request_card, validate_card, validate_text


@pytest.fixture
def product():
    return normalize_product({"name": "Манометр DM 02", "manufacturer": "Манотомь", "description": "Измеряет избыточное давление.", "specs": {"Диапазон": "0-1,6 МПа", "Питание": ""}, "id": "123456"})


@pytest.fixture
def draft():
    return {
        "status": "draft",
        "seo_title": "Манометр DM 02 для контроля давления",
        "seo_description": ("Манометр DM 02 предназначен для измерения избыточного давления. " * 16).strip(),
        "search_queries": ["манометр DM 02", "Манотомь манометр", "манометр давления"],
        "commercial": "Для специалистов, которым нужно измерять избыточное давление.",
        "reason": "",
    }


@pytest.fixture
def client(draft):
    api = Mock()
    api.responses.create.return_value = SimpleNamespace(status="completed", output=[], output_text=json.dumps(draft, ensure_ascii=False))
    return api


@pytest.mark.parametrize("environment_key, file_key, expected", [("env-key", "file-key", "env-key"), (None, "file-key", "file-key")])
def test_create_client(monkeypatch, tmp_path, environment_key, file_key, expected):
    path = tmp_path / ".env"
    path.write_text(f"OPENAI_API_KEY={file_key}\nOPENAI_MODEL=test-model\n", encoding="utf-8")
    monkeypatch.setattr(generator, "ENV_PATH", path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    if environment_key:
        monkeypatch.setenv("OPENAI_API_KEY", environment_key)
    constructor = Mock()
    monkeypatch.setattr(openai, "OpenAI", constructor)
    assert create_client() is constructor.return_value
    constructor.assert_called_once_with(api_key=expected, timeout=45.0, max_retries=0)


@pytest.mark.parametrize("key", ["", "   "])
def test_create_client_missing_key(monkeypatch, tmp_path, key):
    monkeypatch.setattr(generator, "ENV_PATH", tmp_path / "missing.env")
    monkeypatch.setenv("OPENAI_API_KEY", key)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        create_client()


@pytest.mark.parametrize("value, minimum, maximum, expected", [("  Текст  ", 1, 5, "Текст"), ("", 0, 5, ""), ("А" * 70, 1, 70, "А" * 70), ("А\u2014Б", 1, 5, "А-Б")])
def test_validate_text(value, minimum, maximum, expected):
    assert validate_text(value, "test", minimum, maximum) == expected


@pytest.mark.parametrize("value", [None, 123, "", "А" * 71])
def test_validate_text_invalid(value):
    with pytest.raises(ValueError, match="test:"):
        validate_text(value, "test", 1, 70)


@pytest.mark.parametrize("text, expected", [("0-1,6 МПа, 1.6 МПа", {"0", "1.6"}), ("DM 02, ПМ01-24.АВ", {"02", "01", "24"}), ("Нет чисел", set())])
def test_get_numbers(text, expected):
    assert get_numbers(text) == expected


@pytest.mark.parametrize("length", [800, 1000, 1200])
def test_validate_card(product, draft, length):
    draft["seo_description"] = "Т" * length
    card = validate_card(draft, product)
    assert card["status"] == "draft"
    assert card["specs"] == {"Диапазон": "0-1,6 МПа", "Питание": "данных нет"}
    assert card["missing_fields"] == ["specs.Питание"]
    assert card["id"] == "123456"
    assert product["specs"]["Питание"] == ""
    assert "specs" not in draft


@pytest.mark.parametrize("description", ["", "Исходных данных недостаточно для полного описания."])
def test_validate_card_needs_data(product, draft, description):
    product["description"] = ""
    product["specs"] = {}
    draft.update(status="needs_data", seo_description=description, reason="Добавьте описание назначения и технические характеристики.")
    card = validate_card(draft, product)
    assert card["status"] == "needs_data"
    assert card["specs"] == {}
    assert card["missing_fields"] == ["description", "specs"]


@pytest.mark.parametrize("field, value, message", [
    ("status", "ready", "status:"),
    ("seo_title", "", "seo_title:"),
    ("seo_title", "А" * 71, "seo_title:"),
    ("seo_description", "А" * 799, "seo_description:"),
    ("seo_description", "А" * 1201, "seo_description:"),
    ("commercial", None, "commercial:"),
    ("search_queries", "манометр", "search_queries:"),
    ("search_queries", ["один", "два"], "search_queries:"),
    ("search_queries", ["один", "два", "три", "четыре", "пять", "шесть"], "search_queries:"),
    ("search_queries", ["один", "два", " "], "search_queries:"),
    ("search_queries", ["Манометр", " манометр ", "давление"], "не должны повторяться"),
])
def test_validate_card_invalid(product, draft, field, value, message):
    draft[field] = value
    with pytest.raises(ValueError, match=message):
        validate_card(draft, product)


@pytest.mark.parametrize("data", [None, {}, {"specs": {"Питание": "220 В"}}])
def test_validate_card_wrong_schema(product, data):
    with pytest.raises(ValueError, match="JSON-схемы"):
        validate_card(data, product)


def test_validate_card_extra_field(product, draft):
    draft["specs"] = {"Питание": "220 В"}
    with pytest.raises(ValueError, match="JSON-схемы"):
        validate_card(draft, product)


@pytest.mark.parametrize("field", ["seo_title", "seo_description", "commercial"])
def test_validate_card_new_numbers(product, draft, field):
    draft[field] = draft[field] + " 220 В"
    with pytest.raises(ValueError, match="числа без источника: 220"):
        validate_card(draft, product)


def test_validate_card_search_query_with_new_number(product, draft):
    draft["search_queries"][0] = "манометр 123456 В"
    with pytest.raises(ValueError, match="числа без источника: 123456"):
        validate_card(draft, product)


def test_validate_card_sparse_source(product, draft):
    product["description"] = ""
    product["specs"] = {"Питание": ""}
    with pytest.raises(ValueError, match="status=needs_data"):
        validate_card(draft, product)


def test_validate_card_needs_data_without_reason(product, draft):
    draft.update(status="needs_data", reason=" ")
    with pytest.raises(ValueError, match="reason:"):
        validate_card(draft, product)


def test_request_card(client, product):
    result = request_card(client, "test-model", "Инструкции", {"product": product})
    assert json.loads(result)["status"] == "draft"
    arguments = client.responses.create.call_args.kwargs
    assert arguments["model"] == "test-model"
    assert arguments["instructions"] == "Инструкции"
    assert json.loads(arguments["input"]) == {"product": product}
    assert arguments["text"]["format"]["strict"] is True
    assert arguments["store"] is False


@pytest.mark.parametrize("code, message", [(401, "ключ"), (403, "запретил"), (404, "Модель не найдена"), (429, "лимит"), (500, "HTTP 500")])
def test_request_card_api_status_error(client, code, message):
    response = Mock(status_code=code, headers={})
    client.responses.create.side_effect = APIStatusError("private API message", response=response, body=None)
    with pytest.raises(RuntimeError, match=message):
        request_card(client, "test-model", "Инструкции", {})


@pytest.mark.parametrize("error, message", [(APITimeoutError(request=Mock()), "не ответил вовремя"), (APIConnectionError(request=Mock()), "подключиться")])
def test_request_card_network_error(client, error, message):
    client.responses.create.side_effect = error
    with pytest.raises(RuntimeError, match=message):
        request_card(client, "test-model", "Инструкции", {})


@pytest.mark.parametrize("status, text, message", [("incomplete", "{}", "незавершённый"), ("failed", "{}", "незавершённый"), ("completed", " ", "пустой ответ")])
def test_request_card_invalid_response(client, status, text, message):
    client.responses.create.return_value = SimpleNamespace(status=status, output=[], output_text=text)
    with pytest.raises(RuntimeError, match=message):
        request_card(client, "test-model", "Инструкции", {})


def test_request_card_refusal(client):
    refusal = SimpleNamespace(type="refusal")
    message = SimpleNamespace(type="message", content=[refusal])
    client.responses.create.return_value = SimpleNamespace(status="completed", output=[message], output_text="")
    with pytest.raises(RuntimeError, match="отказалась"):
        request_card(client, "test-model", "Инструкции", {})


def test_generate_card(client, product):
    card = generate_card(product, client, "test-model")
    assert card["status"] == "draft"
    assert card["name"] == "Манометр DM 02"
    client.responses.create.assert_called_once()
    payload = json.loads(client.responses.create.call_args.kwargs["input"])
    assert payload["correction"] == ""
    assert "id" not in payload["product"]


@pytest.mark.parametrize("first_text", ["not JSON", '{"seo_title": "Мало полей"}'])
def test_generate_card_one_correction(client, product, draft, first_text):
    bad = SimpleNamespace(status="completed", output=[], output_text=first_text)
    good = SimpleNamespace(status="completed", output=[], output_text=json.dumps(draft))
    client.responses.create.side_effect = [bad, good]
    assert generate_card(product, client, "test-model")["status"] == "draft"
    assert client.responses.create.call_count == 2
    correction = json.loads(client.responses.create.call_args.kwargs["input"])["correction"]
    assert correction


def test_generate_card_stops_after_two_attempts(client, product):
    client.responses.create.return_value = SimpleNamespace(status="completed", output=[], output_text="broken JSON")
    with pytest.raises(ValueError, match="после двух попыток"):
        generate_card(product, client, "test-model")
    assert client.responses.create.call_count == 2


def test_generate_card_does_not_retry_api_error(client, product):
    client.responses.create.side_effect = APITimeoutError(request=Mock())
    with pytest.raises(RuntimeError, match="не ответил вовремя"):
        generate_card(product, client, "test-model")
    client.responses.create.assert_called_once()


def test_generate_card_missing_name(client):
    with pytest.raises(ValueError, match="заполните name"):
        generate_card({}, client, "test-model")
    client.responses.create.assert_not_called()


def test_generate_card_model_from_environment(client, product, monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "env-model")
    generate_card(product, client)
    assert client.responses.create.call_args.kwargs["model"] == "env-model"


def test_generate_card_missing_model(client, product, monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    with pytest.raises(ValueError, match="OPENAI_MODEL"):
        generate_card(product, client)
    client.responses.create.assert_not_called()


@pytest.mark.parametrize("text", [None, " "])
def test_generate_card_invalid_prompt(client, product, monkeypatch, tmp_path, text):
    path = tmp_path / "prompt.txt"
    if text is not None:
        path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(generator, "PROMPT_PATH", path)
    with pytest.raises((ValueError, FileNotFoundError)):
        generate_card(product, client, "test-model")
    client.responses.create.assert_not_called()


@pytest.mark.parametrize("source", [
    {"name": "Манометр DM 02", "manufacturer": "Манотомь", "description": "Измеряет давление.", "specs": {"Диапазон": "0-1,6 МПа"}},
    {"name": "Товар", "manufacturer": "", "description": "", "specs": {}},
])
def test_create_local_card(source):
    card = create_local_card(source)
    assert card["status"] == "needs_data"
    assert card["id"] == ""
    assert len(card["search_queries"]) == 3
    assert "заглушка" in card["reason"]

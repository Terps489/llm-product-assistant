import json
import xml.etree.ElementTree as ET
from pathlib import Path


TEXT_FIELDS = ("name", "manufacturer", "description", "id", "type", "source_url", "article", "product_code")
IDENTIFIER_PARAMS = {"артикул", "код товара"}


def clean_text(value: str | None, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field}: ожидается строка или null.")
    return value.strip()


def normalize_product(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Товар должен быть объектом JSON.")
    product = {field: clean_text(data.get(field), field) for field in TEXT_FIELDS}
    specs = data.get("specs")
    if specs is None:
        specs = {}
    if not isinstance(specs, dict):
        raise ValueError("specs: ожидается объект с характеристиками.")
    product["specs"] = {}
    for name, value in specs.items():
        name = clean_text(name, "Название характеристики")
        if not name:
            raise ValueError("Название характеристики не может быть пустым.")
        if name in product["specs"]:
            raise ValueError(f"Повторяется характеристика: {name}.")
        product["specs"][name] = clean_text(value, f"specs.{name}")
    return product


def load_products(path: str | Path) -> list[dict]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("Некорректный JSON. Проверьте синтаксис и кодировку UTF-8.") from error
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list) or not data:
        raise ValueError("Ожидается товар или непустой список товаров.")
    return [normalize_product(product) for product in data]


def missing_fields(product: dict) -> list[str]:
    """Принимает товар, уже обработанный normalize_product."""
    missing = [field for field in ("name", "manufacturer", "description", "specs") if not product[field]]
    missing.extend(f"specs.{name}" for name, value in product["specs"].items() if not value)
    return missing


def parse_offer(offer: ET.Element) -> dict:
    specs = {}
    identifiers = {}
    for param in offer.findall("param"):
        name = (param.get("name") or "").strip()
        if not name:
            continue
        value = (param.text or "").strip()
        if name.casefold() in IDENTIFIER_PARAMS:
            identifiers[name.casefold()] = value
            continue
        if name in specs:
            raise ValueError(f"Повторяется характеристика в XML: {name}.")
        unit = (param.get("unit") or "").strip()
        specs[name] = f"{value} {unit}".strip() if value else ""
    product = {
        "id": offer.get("id"),
        "name": offer.findtext("name"),
        "type": offer.findtext("typePrefix"),
        "manufacturer": offer.findtext("vendor"),
        "description": offer.findtext("description"),
        "source_url": offer.findtext("url"),
        "article": offer.findtext("vendorCode") or identifiers.get("артикул"),
        "product_code": identifiers.get("код товара"),
        "specs": specs,
    }
    return normalize_product(product)


def load_catalog(path: str | Path, ids: list[str]) -> list[dict]:
    if not ids:
        raise ValueError("Укажите хотя бы один ID товара.")
    ids = list(dict.fromkeys(ids))
    try:
        # Для большего каталога здесь можно перейти на iterparse вместо чтения целиком.
        root = ET.parse(path).getroot()
    except ET.ParseError as error:
        raise ValueError("Некорректный XML каталога.") from error
    if root.tag != "yml_catalog" or root.find("shop/offers") is None:
        raise ValueError("Ожидается YML-каталог с разделом shop/offers.")
    offers = {offer.get("id"): offer for offer in root.findall("shop/offers/offer") if offer.get("id") in ids}
    missing = [offer_id for offer_id in ids if offer_id not in offers]
    if missing:
        raise ValueError(f"Товары не найдены: {', '.join(missing)}.")
    return [parse_offer(offers[offer_id]) for offer_id in ids]

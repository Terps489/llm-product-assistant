import json
import xml.etree.ElementTree as ET

import pytest

from catalog import clean_text, load_catalog, load_products, missing_fields, normalize_product, parse_offer


@pytest.mark.parametrize("value, expected", [("  ОВЕН \n", "ОВЕН"), ("0", "0"), ("  ", ""), (None, "")])
def test_clean_text(value, expected):
    assert clean_text(value, "name") == expected


@pytest.mark.parametrize("value", [0, False, [], {}])
def test_clean_text_wrong_type(value):
    with pytest.raises(ValueError, match="name: ожидается строка"):
        clean_text(value, "name")


def test_normalize_product():
    data = {
        "name": " Манометр DM 02 ",
        "manufacturer": " Манотомь ",
        "description": " Измеряет избыточное давление. ",
        "specs": {" Диапазон ": " 0-1,6 МПа ", "Класс точности": "1,5"},
        "id": " 1 ",
        "article": " 001 ",
        "product_code": " 002 ",
        "type": " Манометр ",
        "source_url": " https://example.com/1 ",
    }
    product = normalize_product(data)
    assert product == {
        "name": "Манометр DM 02",
        "manufacturer": "Манотомь",
        "description": "Измеряет избыточное давление.",
        "specs": {"Диапазон": "0-1,6 МПа", "Класс точности": "1,5"},
        "id": "1",
        "article": "001",
        "product_code": "002",
        "type": "Манометр",
        "source_url": "https://example.com/1",
    }
    assert data["specs"][" Диапазон "] == " 0-1,6 МПа "


@pytest.mark.parametrize("data", [{}, {"specs": None}, {"name": None, "description": " ", "manufacturer": ""}])
def test_normalize_product_missing_values(data):
    assert normalize_product(data) == {
        "name": "", "manufacturer": "", "description": "", "specs": {},
        "id": "", "type": "", "source_url": "", "article": "", "product_code": "",
    }


@pytest.mark.parametrize("data, message", [
    ([], "объектом JSON"),
    ({"name": 5}, "name: ожидается строка"),
    ({"specs": []}, "specs: ожидается объект"),
    ({"specs": {" ": "1"}}, "не может быть пустым"),
    ({"specs": {"Питание": 24}}, "specs.Питание: ожидается строка"),
    ({"specs": {"Питание": "24 В", " Питание ": "12 В"}}, "Повторяется характеристика"),
])
def test_normalize_product_invalid_data(data, message):
    with pytest.raises(ValueError, match=message):
        normalize_product(data)


@pytest.mark.parametrize("data, names", [
    ({"name": "Манометр"}, ["Манометр"]),
    ([{"name": "Манометр"}, {"name": "Модем"}], ["Манометр", "Модем"]),
    ({}, [""]),
])
def test_load_products(tmp_path, data, names):
    path = tmp_path / "products.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8-sig")
    assert [product["name"] for product in load_products(path)] == names


@pytest.mark.parametrize("text", ["", "{", "[]", "null", "[3]"])
def test_load_products_invalid_json(tmp_path, text):
    path = tmp_path / "products.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        load_products(path)


def test_load_products_invalid_encoding(tmp_path):
    path = tmp_path / "products.json"
    path.write_bytes(b"\xff")
    with pytest.raises(ValueError, match="кодировку UTF-8"):
        load_products(path)


def test_load_products_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_products(tmp_path / "missing.json")


@pytest.mark.parametrize("product, expected", [
    ({"name": "Манометр", "manufacturer": "Завод", "description": "Измеряет давление.", "specs": {"Диапазон": "0-1 МПа"}}, []),
    ({"name": "", "manufacturer": "", "description": "", "specs": {}}, ["name", "manufacturer", "description", "specs"]),
    ({"name": "Модем", "manufacturer": "ОВЕН", "description": "", "specs": {}}, ["description", "specs"]),
    ({"name": "Модем", "manufacturer": "ОВЕН", "description": "Модем.", "specs": {"Питание": "", "Входы": "0"}}, ["specs.Питание"]),
])
def test_missing_fields(product, expected):
    assert missing_fields(product) == expected


def test_parse_offer():
    offer = ET.fromstring('''<offer id="1">
        <name> DM 02 </name><typePrefix>Манометр</typePrefix><vendor>Манотомь</vendor>
        <description>Измеряет давление.</description><url>https://example.com/1</url>
        <vendorCode>001</vendorCode><param name="Артикул">001</param>
        <param name="Код товара">002</param><param name="Диапазон" unit="МПа">0-1,6</param>
    </offer>''')
    assert parse_offer(offer) == {
        "id": "1", "name": "DM 02", "type": "Манометр", "manufacturer": "Манотомь",
        "description": "Измеряет давление.", "source_url": "https://example.com/1",
        "article": "001", "product_code": "002", "specs": {"Диапазон": "0-1,6 МПа"},
    }


def test_parse_offer_missing_values():
    offer = ET.fromstring('''<offer>
        <name> ПМ01-24.АВ </name><param name="Артикул"> 001 </param>
        <param name="Питание" unit="В"/><param>Без имени</param>
    </offer>''')
    product = parse_offer(offer)
    assert product["name"] == "ПМ01-24.АВ"
    assert product["article"] == "001"
    assert product["description"] == ""
    assert product["specs"] == {"Питание": ""}


def test_parse_offer_duplicate_specs():
    offer = ET.fromstring('<offer><param name="Питание">24 В</param><param name="Питание">12 В</param></offer>')
    with pytest.raises(ValueError, match="Повторяется характеристика в XML"):
        parse_offer(offer)


@pytest.mark.parametrize("article, code", [("Артикул", "Код товара"), ("АРТИКУЛ", "КОД ТОВАРА"), (" артикул ", " код товара ")])
def test_parse_offer_identifiers(article, code):
    offer = ET.fromstring(f'<offer><param name="{article}">001</param><param name="{code}">002</param></offer>')
    product = parse_offer(offer)
    assert product["article"] == "001"
    assert product["product_code"] == "002"
    assert product["specs"] == {}


@pytest.mark.parametrize("ids, names", [(["1", "2"], ["Манометр", "Модем"]), (["2", "1"], ["Модем", "Манометр"]), (["1", "1"], ["Манометр"])])
def test_load_catalog(tmp_path, ids, names):
    path = tmp_path / "feed.xml"
    path.write_text('<yml_catalog><shop><offers><offer id="1"><name>Манометр</name></offer><offer id="2"><name>Модем</name></offer></offers></shop></yml_catalog>', encoding="utf-8")
    assert [product["name"] for product in load_catalog(path, ids)] == names


@pytest.mark.parametrize("xml, message", [
    ("<yml_catalog>", "Некорректный XML"),
    ('<yml_catalog><shop><offers><offer id="1"/></offers></shop></yml_catalog><', "Некорректный XML"),
    ("<other/>", "Ожидается YML-каталог"),
    ("<yml_catalog><shop/></yml_catalog>", "Ожидается YML-каталог"),
    ("<yml_catalog><shop><offers/></shop></yml_catalog>", "Товары не найдены: 1"),
])
def test_load_catalog_invalid_xml(tmp_path, xml, message):
    path = tmp_path / "feed.xml"
    path.write_text(xml, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_catalog(path, ["1"])


def test_load_catalog_empty_ids(tmp_path):
    with pytest.raises(ValueError, match="хотя бы один ID"):
        load_catalog(tmp_path / "feed.xml", [])


def test_load_catalog_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_catalog(tmp_path / "missing.xml", ["1"])

import json
from unittest.mock import Mock

import main


def test_main_local(monkeypatch, tmp_path, capsys):
    product_path = tmp_path / "product.json"
    product_path.write_text(json.dumps({"name": "Тестовый товар"}), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["main.py", "--local", "--product", str(product_path)])
    create_client = Mock(side_effect=AssertionError("API should not be called"))
    monkeypatch.setattr(main, "create_client", create_client)

    main.main()

    output = capsys.readouterr().out
    assert "Локальный пример, не результат модели." in output
    assert json.loads(output.split("\n", 1)[1])["status"] == "needs_data"
    create_client.assert_not_called()


def test_main_api(monkeypatch, tmp_path, capsys):
    product_path = tmp_path / "product.json"
    product_path.write_text(json.dumps({"name": "Тестовый товар"}), encoding="utf-8")
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    monkeypatch.setattr("sys.argv", ["main.py", "--product", str(product_path)])
    monkeypatch.setattr(main, "create_client", lambda: client)
    monkeypatch.setattr(main, "generate_card", lambda product, api: {"status": "draft"})

    main.main()

    assert json.loads(capsys.readouterr().out)["status"] == "draft"

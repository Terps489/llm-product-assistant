import argparse
import json
from pathlib import Path

from catalog import load_products
from generator import create_client, create_local_card, generate_card


PROJECT_PATH = Path(__file__).parent


def main() -> None:
    parser = argparse.ArgumentParser(description="Формирование карточки товара.")
    parser.add_argument("--local", action="store_true", help="показать локальный пример без вызова API")
    parser.add_argument("--product", default=str(PROJECT_PATH / "data/product_example.json"), help="путь к JSON с товаром")
    args = parser.parse_args()

    product = load_products(args.product)[0]
    if args.local:
        card = create_local_card(product)
        print("Локальный пример, не результат модели.")
    else:
        with create_client() as client:
            card = generate_card(product, client)
    print(json.dumps(card, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

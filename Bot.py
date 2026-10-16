import os
from urllib.parse import quote

import requests
import telebot


SERVER_URL = os.getenv("SERVER_URL", "http://127.0.0.1:5000")
API_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


def ask_server(question: str) -> list[dict]:
    response = requests.post(
        f"{SERVER_URL}/generate-sql",
        json={"question": question},
        timeout=60,
    )
    response.raise_for_status()
    generated_sql = response.json()["sql"]

    response = requests.post(
        f"{SERVER_URL}/query",
        json={"sql": generated_sql},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["results"]


def format_results(results: list[dict]) -> str:
    if not results:
        return "Nenhum resultado encontrado."
    return "\n".join(
        " | ".join(f"{key}: {value}" for key, value in row.items())
        for row in results
    )


def command_fields(message) -> list[str]:
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return []
    return [field.strip() for field in parts[1].split("|")]


def request_product_change(bot, message, method: str, path: str, payload=None):
    try:
        response = requests.request(
            method,
            f"{SERVER_URL}{path}",
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("Resposta inválida")
        return result
    except requests.HTTPError as error:
        try:
            detail = error.response.json().get("error", "erro desconhecido")
        except (ValueError, AttributeError):
            detail = "erro desconhecido"
        bot.reply_to(message, f"Não foi possível alterar o produto: {detail}")
    except requests.RequestException:
        bot.reply_to(
            message,
            "Não foi possível consultar o servidor. "
            "Verifique se o Server.py está em execução.",
        )
    except ValueError:
        bot.reply_to(message, "O servidor retornou uma resposta inválida.")
    return None


def create_bot() -> telebot.TeleBot:
    if not API_TOKEN:
        raise RuntimeError(
            "Defina TELEGRAM_BOT_TOKEN como variável de ambiente antes de iniciar o bot."
        )
    bot = telebot.TeleBot(API_TOKEN)

    @bot.message_handler(commands=["start", "help"])
    def welcome(message):
        bot.reply_to(
            message,
            "Envie uma pergunta sobre os produtos, por exemplo: "
            '"qual o departamento do sabonete?".\n'
            "Comandos:\n"
            "/adicionar nome | departamento | quantidade\n"
            "/editar nome atual | nome ou departamento | novo valor\n"
            "/estoque nome | quantidade\n"
            "/excluir nome",
        )

    @bot.message_handler(commands=["adicionar"])
    def add_product(message):
        fields = command_fields(message)
        if len(fields) not in (2, 3) or not fields[0] or not fields[1]:
            bot.reply_to(
                message,
                "Use: /adicionar nome | departamento | quantidade (opcional)",
            )
            return
        try:
            quantity = int(fields[2]) if len(fields) == 3 else 0
        except ValueError:
            bot.reply_to(message, "A quantidade deve ser um número inteiro.")
            return
        if quantity < 0:
            bot.reply_to(message, "A quantidade não pode ser negativa.")
            return

        result = request_product_change(
            bot,
            message,
            "POST",
            "/products",
            {
                "nome": fields[0],
                "departamento": fields[1],
                "quantidade": quantity,
            },
        )
        if result:
            product = result["product"]
            bot.reply_to(
                message,
                f"Produto cadastrado: {product['nome']} "
                f"(estoque: {product['quantidade']}).",
            )

    @bot.message_handler(commands=["editar"])
    def edit_product(message):
        fields = command_fields(message)
        if len(fields) != 3 or not all(fields):
            bot.reply_to(
                message,
                "Use: /editar nome atual | nome ou departamento | novo valor",
            )
            return
        field = fields[1].lower()
        if field not in ("nome", "departamento"):
            bot.reply_to(message, "O campo deve ser nome ou departamento.")
            return

        result = request_product_change(
            bot,
            message,
            "PATCH",
            f"/products/{quote(fields[0], safe='')}",
            {field: fields[2]},
        )
        if result:
            product = result["product"]
            bot.reply_to(
                message,
                f"Produto atualizado: {product['nome']} | "
                f"{product['departamento']} | estoque: {product['quantidade']}.",
            )

    @bot.message_handler(commands=["estoque"])
    def update_stock(message):
        fields = command_fields(message)
        if len(fields) != 2 or not fields[0] or not fields[1]:
            bot.reply_to(message, "Use: /estoque nome do produto | quantidade")
            return
        try:
            quantity = int(fields[1])
        except ValueError:
            bot.reply_to(message, "A quantidade deve ser um número inteiro.")
            return
        if quantity < 0:
            bot.reply_to(message, "A quantidade não pode ser negativa.")
            return

        result = request_product_change(
            bot,
            message,
            "PATCH",
            f"/products/{quote(fields[0], safe='')}/quantity",
            {"quantidade": quantity},
        )
        if result:
            product = result["product"]
            bot.reply_to(
                message,
                f"Estoque atualizado: {product['nome']} "
                f"({product['quantidade']}).",
            )

    @bot.message_handler(commands=["excluir"])
    def delete_product(message):
        fields = command_fields(message)
        if len(fields) != 1 or not fields[0]:
            bot.reply_to(message, "Use: /excluir nome do produto")
            return

        result = request_product_change(
            bot,
            message,
            "DELETE",
            f"/products/{quote(fields[0], safe='')}",
        )
        if result:
            bot.reply_to(message, result.get("message", "Produto excluído."))

    @bot.message_handler(content_types=["text"])
    def answer_question(message):
        try:
            results = ask_server(message.text)
            bot.reply_to(message, format_results(results))
        except requests.HTTPError as error:
            try:
                detail = error.response.json().get("error", "erro desconhecido")
            except (ValueError, AttributeError):
                detail = "erro desconhecido"
            bot.reply_to(message, f"Não foi possível responder: {detail}")
        except requests.RequestException:
            bot.reply_to(
                message,
                "Não foi possível consultar o servidor. "
                "Verifique se o Server.py está em execução.",
            )
        except (KeyError, ValueError):
            bot.reply_to(message, "O servidor retornou uma resposta inválida.")

    return bot


def main() -> None:
    bot = create_bot()
    bot.infinity_polling()


if __name__ == "__main__":
    main()

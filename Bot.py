import os

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
            '"qual o departamento do sabonete?"',
        )

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

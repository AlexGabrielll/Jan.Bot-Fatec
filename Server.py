import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", BASE_DIR / "lojas.db"))
SQL_SCHEMA = """
CREATE TABLE produtos (
    nome TEXT NOT NULL PRIMARY KEY,
    departamento TEXT NOT NULL
)
"""

app = Flask(__name__)


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database() -> None:
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS produtos (
                nome TEXT NOT NULL PRIMARY KEY,
                departamento TEXT NOT NULL
            )
            """
        )
        connection.executemany(
            "INSERT OR IGNORE INTO produtos (nome, departamento) VALUES (?, ?)",
            [
                ("sabonete", "higiene"),
                ("agua", "bebidas"),
                ("coca", "bebidas"),
            ],
        )


def generate_sql(question: str) -> str:
    """Converte uma pergunta em SQL usando o modelo local configurado."""
    try:
        return generate_sql_with_model(question)
    except Exception:
        app.logger.exception(
            "O modelo local não conseguiu gerar SQL; usando o tradutor inicial."
        )
        return generate_sql_fallback(question)


def generate_sql_with_model(question: str) -> str:
    """Gera SQL com um modelo instruct compatível com completions/chat completions."""
    try:
        import dspy
    except ImportError as error:
        raise RuntimeError("A dependência dspy não está instalada.") from error

    class TextToSQL(dspy.Signature):
        """
        Responda perguntas variadas em linguagem natural convertendo-as para SQL.
        Use somente o schema fornecido e gere uma única consulta SELECT.
        Não invente colunas, tabelas ou dados e não inclua explicações, markdown
        ou comandos que alterem o banco.
        """

        dbschema = dspy.InputField(
            desc="Schema completo do banco de dados disponível para consulta"
        )
        question = dspy.InputField(
            desc=(
                "Pergunta do usuário em português. Interprete sinônimos, "
                "plural e diferentes formas de solicitar a mesma informação."
            )
        )
        sql_query = dspy.OutputField(
            desc=(
                "Uma única consulta SQLite SELECT válida sobre produtos, "
                "sem explicação, markdown ou ponto e vírgula"
            )
        )

    model = dspy.LM(
        os.getenv("LOCAL_LM_MODEL", "openai/Qwen2.5-7B-Instruct"),
        api_base=os.getenv("LOCAL_LM_API_BASE", "http://localhost:1337/v1"),
        api_key=os.getenv("LOCAL_LM_API_KEY", "not-needed"),
        temperature=float(os.getenv("LOCAL_LM_TEMPERATURE", "0")),
    )
    dspy.configure(lm=model)
    prediction = dspy.ChainOfThought(TextToSQL)(
        dbschema=SQL_SCHEMA,
        question=question,
    )
    return validate_select(prediction.sql_query)


def generate_sql_fallback(question: str) -> str:
    """Atende perguntas básicas enquanto o servidor não tiver um LLM generativo."""
    normalized = " ".join(question.lower().strip().split())
    if not normalized:
        raise ValueError("Informe uma pergunta não vazia.")

    if re.search(r"\b(quais|listar|liste|mostrar|mostre)\b.*\bprodutos\b", normalized):
        return "SELECT nome, departamento FROM produtos ORDER BY nome"

    if re.search(r"\b(quais|listar|liste|mostrar|mostre)\b.*\bdepartamentos\b", normalized):
        return "SELECT DISTINCT departamento FROM produtos ORDER BY departamento"

    product_match = re.search(
        r"\b(?:do|da|de|produto)\s+([a-zà-ÿ]+)\b", normalized
    )
    if product_match and re.search(
        r"\b(departamento|categoria|setor)\b", normalized
    ):
        product_name = product_match.group(1)
        escaped_name = product_name.replace("'", "''")
        return (
            "SELECT departamento FROM produtos "
            f"WHERE nome = '{escaped_name}'"
        )

    raise ValueError(
        "O servidor local não possui um modelo generativo ativo. "
        "Carregue um modelo de chat/texto ou faça uma pergunta básica, "
        "como 'qual o departamento do sabonete?' ou 'quais produtos existem?'."
    )


def validate_select(sql_query: str) -> str:
    """Permite somente SELECT simples sobre a tabela pública do projeto."""
    normalized = sql_query.strip()
    normalized = re.sub(r"^```(?:sql)?\s*", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s*```$", "", normalized)
    normalized = re.sub(r";\s*$", "", normalized)
    if not re.match(r"^SELECT\b", normalized, re.IGNORECASE):
        raise ValueError("A consulta precisa começar com SELECT.")
    if ";" in normalized or "--" in normalized or "/*" in normalized:
        raise ValueError("A consulta contém sintaxe não permitida.")
    if not re.search(r"\bprodutos\b", normalized, re.IGNORECASE):
        raise ValueError("A consulta deve utilizar a tabela produtos.")
    if re.search(
        r"\b(ATTACH|DETACH|DELETE|DROP|INSERT|PRAGMA|UPDATE|ALTER|CREATE|REPLACE)\b",
        normalized,
        re.IGNORECASE,
    ):
        raise ValueError("Somente consultas de leitura são permitidas.")
    return normalized


def execute_query(sql_query: str) -> list[dict[str, Any]]:
    validated_query = validate_select(sql_query)
    with get_connection() as connection:
        rows = connection.execute(validated_query).fetchall()
    return [dict(row) for row in rows]


@app.post("/generate-sql")
def generate_sql_route():
    body = request.get_json(silent=True) or {}
    question = body.get("question")
    if not isinstance(question, str) or not question.strip():
        return jsonify(error="Informe uma pergunta não vazia."), 400

    try:
        sql_query = generate_sql(question.strip())
    except (RuntimeError, ValueError) as error:
        return jsonify(error=str(error)), 422
    except Exception:
        app.logger.exception("Falha ao gerar SQL.")
        return jsonify(error="Não foi possível gerar a consulta SQL."), 502
    return jsonify(question=question.strip(), sql=sql_query)


@app.post("/query")
def query_route():
    body = request.get_json(silent=True) or {}
    sql_query = body.get("sql")
    if not isinstance(sql_query, str) or not sql_query.strip():
        return jsonify(error="Informe uma consulta SQL."), 400

    try:
        results = execute_query(sql_query)
    except (ValueError, sqlite3.Error) as error:
        return jsonify(error=f"Consulta inválida: {error}"), 400
    return jsonify(results=results)


if __name__ == "__main__":
    initialize_database()
    app.run(
        host=os.getenv("SERVER_HOST", "127.0.0.1"),
        port=int(os.getenv("SERVER_PORT", "5000")),
        debug=False,
    )

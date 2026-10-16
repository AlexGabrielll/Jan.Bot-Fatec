import os
import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", BASE_DIR / "lojas.db"))
SQL_SCHEMA = """
CREATE TABLE produtos (
    nome TEXT NOT NULL PRIMARY KEY,
    departamento TEXT NOT NULL,
    quantidade INTEGER NOT NULL DEFAULT 0 CHECK (quantidade >= 0)
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
                departamento TEXT NOT NULL,
                quantidade INTEGER NOT NULL DEFAULT 0 CHECK (quantidade >= 0)
            )
            """
        )
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(produtos)")
        }
        if "quantidade" not in columns:
            connection.execute(
                """
                ALTER TABLE produtos
                ADD COLUMN quantidade INTEGER NOT NULL DEFAULT 0
                CHECK (quantidade >= 0)
                """
            )
        connection.executemany(
            "INSERT OR IGNORE INTO produtos (nome, departamento) VALUES (?, ?)",
            [
                ("sabonete", "higiene"),
                ("agua", "bebidas"),
                ("coca", "bebidas"),
                ("arroz", "mercearia"),
                ("feijao", "mercearia"),
                ("cafe", "mercearia"),
                ("macarrao", "mercearia"),
                ("leite", "bebidas"),
                ("banana", "hortifruti"),
                ("maca", "hortifruti"),
                ("shampoo", "higiene"),
                ("papel higienico", "higiene"),
                ("detergente", "limpeza"),
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
        from openai import OpenAI
    except ImportError as error:
        raise RuntimeError("Instale a dependência openai no ambiente do servidor.") from error

    client = OpenAI(
        base_url=os.getenv("LOCAL_LM_API_BASE", "http://127.0.0.1:1337/v1"),
        api_key=os.getenv("LOCAL_LM_API_KEY", "not-needed"),
    )
    response = client.chat.completions.create(
        model=os.getenv("LOCAL_LM_MODEL", "Jan-code-4b-Q4_K_M"),
        temperature=float(os.getenv("LOCAL_LM_TEMPERATURE", "0")),
        max_tokens=200,
        messages=[
            {
                "role": "system",
                "content": (
                    "Você converte perguntas em português para uma única consulta "
                    "SQLite SELECT. Use somente a tabela "
                    "produtos(nome, departamento, quantidade). "
                    "Não invente colunas, não altere dados, não use markdown e não "
                    "inclua ponto e vírgula. Responda apenas com o SQL."
                ),
            },
            {
                "role": "user",
                "content": f"Schema: {SQL_SCHEMA}\nPergunta: {question}",
            },
        ],
    )
    sql_query = response.choices[0].message.content
    if not sql_query:
        raise ValueError("O modelo não retornou uma consulta SQL.")
    return validate_select(sql_query)


def generate_sql_fallback(question: str) -> str:
    """Atende perguntas básicas enquanto o servidor não tiver um LLM generativo."""
    normalized = " ".join(question.lower().strip().split())
    comparable = "".join(
        character
        for character in unicodedata.normalize("NFD", normalized)
        if unicodedata.category(character) != "Mn"
    )
    if not normalized:
        raise ValueError("Informe uma pergunta não vazia.")

    if re.search(
        r"\b(quais|qual|listar|liste|lista|mostrar|mostre|tem|possui)\b"
        r".*\b(produtos|itens|mercadorias)\b",
        comparable,
    ):
        return "SELECT nome, departamento, quantidade FROM produtos ORDER BY nome"

    if re.search(r"\b(quantidade|estoque)\b", comparable):
        product_match = re.search(
            r"\b(?:do|da|de|produto|item)\s+(.+?)\s*[?.!]*$", normalized
        )
        if not product_match:
            raise ValueError(
                "Informe o produto, por exemplo: 'qual o estoque do sabonete?'."
            )
        product_name = product_match.group(1).strip(" ?.! ")
        if not product_name:
            raise ValueError("Informe o nome do produto.")
        escaped_name = product_name.replace("'", "''")
        return (
            "SELECT quantidade FROM produtos "
            f"WHERE lower(nome) = lower('{escaped_name}')"
        )

    if re.search(
        r"\b(quais|qual|listar|liste|lista|mostrar|mostre|tem|possui)\b"
        r".*\b(departamentos|categorias|setores)\b",
        comparable,
    ):
        return "SELECT DISTINCT departamento FROM produtos ORDER BY departamento"

    if re.search(r"\b(departamento|categoria|setor)\b", comparable):
        product_match = re.search(
            r"\b(?:do|da|de|produto|item)\s+([a-zà-ÿ]+)\b", normalized
        )
        if not product_match:
            product_match = re.search(
                r"\b(?:o|a)\s+([a-zà-ÿ]+)\b", normalized
            )
        if not product_match:
            raise ValueError(
                "Informe o nome do produto, por exemplo: "
                "'qual o departamento do sabonete?'."
            )
        product_name = product_match.group(1)
        escaped_name = product_name.replace("'", "''")
        return (
            "SELECT departamento FROM produtos "
            f"WHERE nome = '{escaped_name}'"
        )

    raise ValueError(
        "Não entendi a pergunta. Pergunte, por exemplo, "
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


@app.post("/products")
def create_product_route():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="Informe os dados do produto em JSON."), 400
    if set(body) - {"nome", "departamento", "quantidade"}:
        return jsonify(error="O produto contém campos não permitidos."), 400

    name = body.get("nome")
    department = body.get("departamento")
    quantity = body.get("quantidade", 0)
    if not isinstance(name, str) or not name.strip():
        return jsonify(error="Informe um nome de produto válido."), 400
    if not isinstance(department, str) or not department.strip():
        return jsonify(error="Informe um departamento válido."), 400
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
        return jsonify(error="A quantidade deve ser um inteiro não negativo."), 400

    product = {
        "nome": name.strip(),
        "departamento": department.strip(),
        "quantidade": quantity,
    }
    try:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO produtos (nome, departamento, quantidade)
                VALUES (?, ?, ?)
                """,
                (product["nome"], product["departamento"], product["quantidade"]),
            )
    except sqlite3.IntegrityError:
        return jsonify(error="Já existe um produto com esse nome."), 409
    return jsonify(product=product), 201


@app.patch("/products/<string:product_name>")
def update_product_route(product_name: str):
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not body:
        return jsonify(error="Informe ao menos um campo para editar."), 400
    if set(body) - {"nome", "departamento"}:
        return jsonify(error="Edite somente nome ou departamento nesta rota."), 400

    updates = {}
    for field in ("nome", "departamento"):
        if field in body:
            value = body[field]
            if not isinstance(value, str) or not value.strip():
                return jsonify(error=f"Informe um valor válido para {field}."), 400
            updates[field] = value.strip()

    assignments = ", ".join(f"{field} = ?" for field in updates)
    values = [*updates.values(), product_name]
    try:
        with get_connection() as connection:
            cursor = connection.execute(
                f"UPDATE produtos SET {assignments} WHERE nome = ?", values
            )
            if cursor.rowcount == 0:
                return jsonify(error="Produto não encontrado."), 404
            updated_name = updates.get("nome", product_name)
            row = connection.execute(
                """
                SELECT nome, departamento, quantidade
                FROM produtos WHERE nome = ?
                """,
                (updated_name,),
            ).fetchone()
    except sqlite3.IntegrityError:
        return jsonify(error="Já existe um produto com esse nome."), 409

    return jsonify(product=dict(row))


@app.patch("/products/<string:product_name>/quantity")
def update_product_quantity_route(product_name: str):
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) != {"quantidade"}:
        return jsonify(error="Informe somente o campo quantidade."), 400
    quantity = body["quantidade"]
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
        return jsonify(error="A quantidade deve ser um inteiro não negativo."), 400

    with get_connection() as connection:
        cursor = connection.execute(
            "UPDATE produtos SET quantidade = ? WHERE nome = ?",
            (quantity, product_name),
        )
        if cursor.rowcount == 0:
            return jsonify(error="Produto não encontrado."), 404
        row = connection.execute(
            """
            SELECT nome, departamento, quantidade
            FROM produtos WHERE nome = ?
            """,
            (product_name,),
        ).fetchone()
    return jsonify(product=dict(row))


@app.delete("/products/<string:product_name>")
def delete_product_route(product_name: str):
    with get_connection() as connection:
        cursor = connection.execute(
            "DELETE FROM produtos WHERE nome = ?", (product_name,)
        )
    if cursor.rowcount == 0:
        return jsonify(error="Produto não encontrado."), 404
    return jsonify(message="Produto excluído.")


if __name__ == "__main__":
    initialize_database()
    app.run(
        host=os.getenv("SERVER_HOST", "127.0.0.1"),
        port=int(os.getenv("SERVER_PORT", "5000")),
        debug=False,
    )

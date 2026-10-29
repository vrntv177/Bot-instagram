
import base64
import hashlib
import json
import lzma
import os
import re
import runpy
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent

EXPECTED = {
    "JAMAICA_RESTORE_PART_01": (
        850,
        "3ce3706ab4333452966030b7cea77e2d18a949bfd768e6ff0f6cbdc767762272"
    ),
    "JAMAICA_RESTORE_PART_02": (
        850,
        "19f6061097f4d5a698f8306c80d92c8e91c3bf519ed708ce40a210294bd85556"
    ),
    "JAMAICA_RESTORE_PART_03": (
        850,
        "bb29f140f09280bd175ee10226baf5463c4cb9598a4c2bb30b0aca98849de8a2"
    ),
    "JAMAICA_RESTORE_PART_04": (
        850,
        "6561e3c77825f93f8addba8835432a3e0665b38c37ab3454b0184ecaee6a14d5"
    ),
    "JAMAICA_RESTORE_PART_05": (
        850,
        "051a8e546e39d5548a0b74d4c9f5f176f9f88eed6f64815be2ae4c77dad95233"
    ),
    "JAMAICA_RESTORE_PART_06": (
        850,
        "f8a3f1c5302d51eb1609c43a137e271beb17518b1c8cd815106e381950089991"
    ),
    "JAMAICA_RESTORE_PART_07": (
        84,
        "6e3d99090beaa3b134f8625e7ea19e8437873dbc8e8ab4a455941797a76c4f42"
    ),
}


def prepare_db():
    db_path = os.environ.get("DB_PATH", "").strip()

    if not db_path:
        if Path("/data").is_dir():
            db_path = "/data/jamaica.db"
        else:
            db_path = "jamaica.db"

    os.environ["DB_PATH"] = db_path

    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    return path


def create_tables(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS posts (
            message_id INTEGER PRIMARY KEY,
            channel_id INTEGER NOT NULL,
            author_id INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS likes (
            message_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            PRIMARY KEY (message_id, user_id),
            FOREIGN KEY (message_id)
                REFERENCES posts(message_id)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (message_id)
                REFERENCES posts(message_id)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS jamaica_restore_log (
            signature TEXT PRIMARY KEY
        );
    """)


def merge_data(db, data, signature):
    with db:
        existing = db.execute(
            "SELECT 1 FROM jamaica_restore_log WHERE signature=?",
            (signature,)
        ).fetchone()

        if existing:
            return False

        db.executemany(
            """
            INSERT OR IGNORE INTO posts
            (message_id, channel_id, author_id, created_at)
            VALUES (?, ?, ?, ?)
            """,
            data["posts"]
        )

        db.executemany(
            """
            INSERT OR IGNORE INTO likes
            (message_id, user_id)
            VALUES (?, ?)
            """,
            data["likes"]
        )

        for row in data["comments"]:
            db.execute(
                """
                INSERT INTO comments
                (message_id, user_id, body, created_at)
                SELECT ?, ?, ?, ?
                WHERE NOT EXISTS (
                    SELECT 1 FROM comments
                    WHERE message_id=?
                      AND user_id=?
                      AND body=?
                      AND created_at=?
                )
                """,
                tuple(row) + tuple(row)
            )

        db.execute(
            "INSERT INTO jamaica_restore_log (signature) VALUES (?)",
            (signature,)
        )

    return True


def read_existing_db(path):
    uri = path.resolve().as_uri() + "?mode=ro"

    with sqlite3.connect(uri, uri=True) as old:
        data = {
            "posts": old.execute(
                """
                SELECT message_id, channel_id, author_id, created_at
                FROM posts
                """
            ).fetchall(),

            "likes": old.execute(
                """
                SELECT message_id, user_id
                FROM likes
                """
            ).fetchall(),

            "comments": old.execute(
                """
                SELECT message_id, user_id, body, created_at
                FROM comments
                """
            ).fetchall()
        }

    return data


def check_variables():
    invalid = []

    for name, (expected_length, expected_hash) in EXPECTED.items():
        value = os.environ.get(name, "").strip()

        length = len(value)

        digest = hashlib.sha256(
            value.encode("utf-8")
        ).hexdigest()

        if length != expected_length or digest != expected_hash:
            print(
                f"[RESTAURACAO] {name}: INCORRETA "
                f"({length} caracteres; esperado: {expected_length})",
                flush=True
            )

            invalid.append(name)

        else:
            print(
                f"[RESTAURACAO] {name}: OK",
                flush=True
            )

    extras = [
        name for name in os.environ
        if re.fullmatch(r"JAMAICA_RESTORE_PART_\d{2}", name)
        and name not in EXPECTED
    ]

    if extras:
        print(
            "[RESTAURACAO] Variaveis extras:",
            ", ".join(extras),
            flush=True
        )
        invalid.extend(extras)

    return invalid


def restore():
    os.chdir(ROOT)

    db_path = prepare_db()

    print(
        "[RESTAURACAO] Iniciando Instagram Jamaica...",
        flush=True
    )

    with sqlite3.connect(db_path, timeout=30) as db:
        db.execute("PRAGMA foreign_keys=ON")

        create_tables(db)

        # Preservar registros de um banco local anterior.
        previous_db = ROOT / "jamaica.db"

        if (
            previous_db.is_file()
            and previous_db.resolve() != db_path.resolve()
        ):
            try:
                previous_data = read_existing_db(previous_db)

                merge_data(
                    db,
                    previous_data,
                    "migrated-local-db"
                )

                print(
                    "[RESTAURACAO] Banco local preservado",
                    flush=True
                )

            except sqlite3.Error as error:
                print(
                    "[RESTAURACAO] Banco local nao importado:",
                    type(error).__name__,
                    flush=True
                )

        names = [
            name for name in os.environ
            if re.fullmatch(r"JAMAICA_RESTORE_PART_\d{2}", name)
        ]

        if not names:
            print(
                "[RESTAURACAO] Nenhuma variavel encontrada",
                flush=True
            )

        else:
            invalid = check_variables()

            if invalid:
                print(
                    "[RESTAURACAO] NAO CONCLUIDA",
                    flush=True
                )

                print(
                    "[RESTAURACAO] Corrija as variaveis:",
                    ", ".join(invalid),
                    flush=True
                )

            else:
                try:
                    encoded = "".join(
                        os.environ[name].strip()
                        for name in EXPECTED
                    )

                    compressed = base64.b64decode(
                        encoded,
                        validate=True
                    )

                    decoded = lzma.decompress(compressed)

                    data = json.loads(
                        decoded.decode("utf-8")
                    )

                    required = {"posts", "likes", "comments"}

                    if not isinstance(data, dict):
                        raise ValueError("Dados invalidos")

                    if set(data) != required:
                        raise ValueError("Estrutura invalida")

                    signature = (
                        "env:"
                        + hashlib.sha256(compressed).hexdigest()
                    )

                    imported = merge_data(
                        db,
                        data,
                        signature
                    )

                    if imported:
                        print(
                            "[RESTAURACAO] CONCLUIDA: "
                            "dados antigos recuperados",
                            flush=True
                        )

                    else:
                        print(
                            "[RESTAURACAO] Dados ja restaurados",
                            flush=True
                        )

                except (
                    ValueError,
                    KeyError,
                    lzma.LZMAError,
                    OSError
                ) as error:
                    print(
                        "[RESTAURACAO] Falha:",
                        type(error).__name__,
                        flush=True
                    )

                    print(
                        "[RESTAURACAO] O bot sera iniciado "
                        "sem restaurar os dados antigos",
                        flush=True
                    )

        posts = db.execute(
            "SELECT COUNT(*) FROM posts"
        ).fetchone()[0]

        likes = db.execute(
            "SELECT COUNT(*) FROM likes"
        ).fetchone()[0]

        comments = db.execute(
            "SELECT COUNT(*) FROM comments"
        ).fetchone()[0]

        print(
            f"[JAMAICA] Publicacoes: {posts}",
            flush=True
        )

        print(
            f"[JAMAICA] Curtidas: {likes}",
            flush=True
        )

        print(
            f"[JAMAICA] Comentarios: {comments}",
            flush=True
        )

    print(
        "[JAMAICA] Iniciando bot do Discord...",
        flush=True
    )


if __name__ == "__main__":
    restore()

    runpy.run_path(
        str(ROOT / "bot.py"),
        run_name="__main__"
    )

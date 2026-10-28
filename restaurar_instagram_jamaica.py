"""Restaura dados do Instagram Jamaica sem publicar o banco de dados no GitHub.

1. Envie apenas este arquivo (SEM o arquivo de variaveis) ao repositorio.
2. Configure o comando Start: python restaurar_instagram_jamaica.py
3. Adicione JAMAICA_RESTORE_PART_01, ... nas variaveis de ambiente.

Os dados sao mesclados uma so vez por pacote, sem apagar dados novos.
"""

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


def prepare_db():
    # A FadeHost preserva os arquivos em /data. A pasta do projeto pode ser
    # substituida durante redeploy; por isso priorizamos o armazenamento persistente.
    db_config = os.environ.get("DB_PATH", "").strip()
    if not db_config and Path("/data").is_dir():
        db_config = "/data/jamaica.db"
    if not db_config:
        db_config = "jamaica.db"
    os.environ["DB_PATH"] = db_config
    db_file = Path(db_config)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    return db_file


def tables(db):
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
            FOREIGN KEY (message_id) REFERENCES posts(message_id)
                ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (message_id) REFERENCES posts(message_id)
                ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS jamaica_restore_log (
            signature TEXT PRIMARY KEY
        );
    """)


def merge(db, data, signature):
    # SQLite controla a transacao: o marcador e as insercoes sao gravados juntos.
    with db:
        if db.execute("SELECT 1 FROM jamaica_restore_log WHERE signature=?", (signature,)).fetchone():
            return False
        db.executemany(
            "INSERT OR IGNORE INTO posts (message_id,channel_id,author_id,created_at) VALUES (?,?,?,?)",
            data["posts"],
        )
        db.executemany(
            "INSERT OR IGNORE INTO likes (message_id,user_id) VALUES (?,?)",
            data["likes"],
        )
        # IDs internos de comentarios podem colidir entre hospedagens.
        # Por isso, deixamos o SQLite gerar novos IDs e evitamos duplicatas.
        db.executemany("""
            INSERT INTO comments (message_id,user_id,body,created_at)
            SELECT ?,?,?,?
            WHERE NOT EXISTS (
                SELECT 1 FROM comments
                WHERE message_id=? AND user_id=? AND body=? AND created_at=?
            )
        """, [tuple(row) + tuple(row) for row in data["comments"]])
        db.execute("INSERT INTO jamaica_restore_log (signature) VALUES (?)", (signature,))
    return True


def from_existing_file(path):
    uri = path.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as old:
        old_tables = {x[0] for x in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"posts", "likes", "comments"}.issubset(old_tables):
            raise RuntimeError(f"Banco local incompatível: {path}")
        data = {
            "posts": old.execute("SELECT message_id,channel_id,author_id,created_at FROM posts").fetchall(),
            "likes": old.execute("SELECT message_id,user_id FROM likes").fetchall(),
            "comments": old.execute("SELECT message_id,user_id,body,created_at FROM comments").fetchall(),
        }
    return data


def restore():
    os.chdir(ROOT)
    dest = prepare_db()
    with sqlite3.connect(dest, timeout=30) as db:
        db.execute("PRAGMA foreign_keys=ON")
        tables(db)

        # Se ja havia um banco no diretorio original, preserva suas publicacoes novas.
        previous = ROOT / "jamaica.db"
        if previous.is_file() and previous.resolve() != dest.resolve():
            current = from_existing_file(previous)
            # Para nao perder alteracoes, so mesclamos o banco de origem quando presente.
            if merge(db, current, "migrated-local-db"):
                print("Instagram Jamaica: dados do banco local preservados", flush=True)

        part_names = sorted(
            (key for key in os.environ if re.fullmatch(r"JAMAICA_RESTORE_PART_\d{2}", key)),
        )
        if part_names:
            required = [f"JAMAICA_RESTORE_PART_{i:02}" for i in range(1, len(part_names)+1)]
            if part_names != required:
                raise RuntimeError("Faltam partes numeradas do pacote de restauracao")
            encoded = "".join(os.environ[key].strip() for key in part_names)
            compressed = base64.b64decode(encoded, validate=True)
            # LZMA detecta pacotes corrompidos.
            data = json.loads(lzma.decompress(compressed).decode("utf-8"))
            if not isinstance(data, dict) or set(data) != {"posts", "likes", "comments"}:
                raise RuntimeError("Pacote de restauracao invalido")
            signature = "env:" + hashlib.sha256(compressed).hexdigest()
            if merge(db, data, signature):
                print("Instagram Jamaica: restauradas as publicacoes, curtidas e comentarios antigos", flush=True)
            else:
                print("Instagram Jamaica: recuperacao antiga ja aplicada", flush=True)

        counts = {tbl: db.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                  for tbl in ("posts", "likes", "comments")}
        print(f"Instagram Jamaica: banco ativo {dest} | "
              f"{counts['posts']} publicacoes, {counts['likes']} curtidas, "
              f"{counts['comments']} comentarios", flush=True)


if __name__ == "__main__":
    restore()
    runpy.run_path(str(ROOT / "bot.py"), run_name="__main__")

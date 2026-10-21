# criado por victor

import io
import os
import sqlite3
import logging
import asyncio
from pathlib import Path
from datetime import datetime, timezone

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "jamaica.db")

MAX_FILE_SIZE = 25 * 1024 * 1024

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("jamaica")

EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".webp",
    ".gif", ".mp4", ".mov", ".webm",
    ".m4v"
}

IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif"
}

VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".webm", ".m4v"
}

MIME_TYPES = {
    "image/png", "image/jpeg", "image/webp",
    "image/gif", "video/mp4", "video/quicktime",
    "video/webm", "video/x-m4v"
}

MIME_TO_EXTENSION = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "video/x-m4v": ".m4v"
}


def connect():
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.execute("PRAGMA foreign_keys=ON")
    return db


def init_database():
    with connect() as db:
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
        """)


def get_post(message_id):
    with connect() as db:
        return db.execute(
            "SELECT author_id FROM posts WHERE message_id=?",
            (message_id,)
        ).fetchone()


def get_counts(message_id):
    with connect() as db:
        likes = db.execute(
            "SELECT COUNT(*) FROM likes WHERE message_id=?",
            (message_id,)
        ).fetchone()[0]

        comments = db.execute(
            "SELECT COUNT(*) FROM comments WHERE message_id=?",
            (message_id,)
        ).fetchone()[0]

    return likes, comments


def media_info(attachment):
    mime = (attachment.content_type or "").split(";")[0].lower()
    suffix = Path(attachment.filename).suffix.lower()

    if suffix not in EXTENSIONS:
        suffix = MIME_TO_EXTENSION.get(mime)

    if suffix not in EXTENSIONS:
        return None

    is_video = suffix in VIDEO_EXTENSIONS

    return suffix, is_video


def make_embed(author, filename, caption, is_video):
    description = f"**Autor(a):** {author.mention}"

    if caption.strip():
        description += (
            "\n\n" +
            discord.utils.escape_mentions(caption[:900])
        )

    embed = discord.Embed(
        description=description,
        color=0x15803D,
        timestamp=datetime.now(timezone.utc)
    )

    if is_video:
        embed.description += "\n\n🎬 **Vídeo publicado**"
    else:
        embed.set_image(url=f"attachment://{filename}")

    return embed


class CommentModal(discord.ui.Modal):
    def __init__(self, post_id):
        super().__init__(title="Comentar publicação")

        self.post_id = post_id

        self.comment = discord.ui.TextInput(
            label="Seu comentário",
            placeholder="Escreva seu comentário...",
            style=discord.TextStyle.paragraph,
            max_length=500,
            required=True
        )

        self.add_item(self.comment)

    async def on_submit(self, interaction):
        if not get_post(self.post_id):
            return await interaction.response.send_message(
                "Publicação não encontrada",
                ephemeral=True
            )

        with connect() as db:
            db.execute(
                """INSERT INTO comments
                (message_id, user_id, body, created_at)
                VALUES (?, ?, ?, ?)""",
                (
                    self.post_id,
                    interaction.user.id,
                    str(self.comment.value),
                    datetime.now(timezone.utc).isoformat()
                )
            )

        await interaction.response.send_message(
            "💬 Comentário publicado!",
            ephemeral=True
        )

        await refresh_post(
            interaction.channel,
            self.post_id
        )


class PostView(discord.ui.View):
    def __init__(self, likes=0, comments=0):
        super().__init__(timeout=None)

        for item in self.children:
            if not isinstance(item, discord.ui.Button):
                continue

            if item.custom_id == "jamaica:like":
                item.label = str(likes)

            elif item.custom_id == "jamaica:comment":
                item.label = str(comments)

    @discord.ui.button(
        emoji="❤️",
        label="0",
        style=discord.ButtonStyle.secondary,
        custom_id="jamaica:like",
        row=0
    )
    async def like(self, interaction, button):
        mid = interaction.message.id

        if not get_post(mid):
            return await interaction.response.send_message(
                "Publicação indisponível",
                ephemeral=True
            )

        await interaction.response.defer()

        with connect() as db:
            existing = db.execute(
                """SELECT 1 FROM likes
                WHERE message_id=? AND user_id=?""",
                (mid, interaction.user.id)
            ).fetchone()

            if existing:
                db.execute(
                    """DELETE FROM likes
                    WHERE message_id=? AND user_id=?""",
                    (mid, interaction.user.id)
                )
            else:
                db.execute(
                    """INSERT OR IGNORE INTO likes
                    (message_id, user_id)
                    VALUES (?, ?)""",
                    (mid, interaction.user.id)
                )

        await refresh_post(interaction.channel, mid)

    @discord.ui.button(
        emoji="💬",
        label="0",
        style=discord.ButtonStyle.secondary,
        custom_id="jamaica:comment",
        row=0
    )
    async def comment(self, interaction, button):
        if not get_post(interaction.message.id):
            return await interaction.response.send_message(
                "Publicação indisponível",
                ephemeral=True
            )

        await interaction.response.send_modal(
            CommentModal(interaction.message.id)
        )

    @discord.ui.button(
        emoji="🗑️",
        style=discord.ButtonStyle.danger,
        custom_id="jamaica:delete",
        row=0
    )
    async def delete(self, interaction, button):
        mid = interaction.message.id
        post = get_post(mid)

        if not post:
            return await interaction.response.send_message(
                "Publicação não encontrada",
                ephemeral=True
            )

        moderator = (
            isinstance(interaction.user, discord.Member)
            and interaction.user.guild_permissions.manage_messages
        )

        if interaction.user.id != post[0] and not moderator:
            return await interaction.response.send_message(
                "Você não pode excluir esta publicação",
                ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)

        try:
            await interaction.message.delete()
        except discord.HTTPException:
            return await interaction.followup.send(
                "Não consegui excluir",
                ephemeral=True
            )

        with connect() as db:
            db.execute(
                "DELETE FROM posts WHERE message_id=?",
                (mid,)
            )

        await interaction.followup.send(
            "🗑️ Publicação excluída",
            ephemeral=True
        )

    @discord.ui.button(
        emoji="👥",
        label="Quem curtiu",
        style=discord.ButtonStyle.secondary,
        custom_id="jamaica:likers",
        row=1
    )
    async def likers(self, interaction, button):
        mid = interaction.message.id

        with connect() as db:
            total = db.execute(
                "SELECT COUNT(*) FROM likes WHERE message_id=?",
                (mid,)
            ).fetchone()[0]

            rows = db.execute(
                """SELECT user_id FROM likes
                WHERE message_id=?
                ORDER BY rowid DESC LIMIT 30""",
                (mid,)
            ).fetchall()

        names = "\n".join(
            f"• <@{uid}>" for (uid,) in rows
        ) or "Ninguém curtiu ainda"

        if total > 30:
            names += f"\nE mais {total - 30} pessoas"

        await interaction.response.send_message(
            f"❤️ **Curtidas ({total})**\n{names}",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none()
        )

    @discord.ui.button(
        emoji="📖",
        label="Ver comentários",
        style=discord.ButtonStyle.secondary,
        custom_id="jamaica:comments",
        row=1
    )
    async def comments(self, interaction, button):
        mid = interaction.message.id

        with connect() as db:
            total = db.execute(
                "SELECT COUNT(*) FROM comments WHERE message_id=?",
                (mid,)
            ).fetchone()[0]

            rows = db.execute(
                """SELECT user_id, body FROM comments
                WHERE message_id=?
                ORDER BY id DESC LIMIT 10""",
                (mid,)
            ).fetchall()

        if not rows:
            return await interaction.response.send_message(
                "Ainda não há comentários",
                ephemeral=True
            )

        lines = []

        for uid, body in rows:
            safe = discord.utils.escape_mentions(
                discord.utils.escape_markdown(body)
            )
            lines.append(f"**<@{uid}>:** {safe[:150]}")

        output = "\n\n".join(lines)

        if total > 10:
            output += f"\n\nMostrando 10 de {total}"

        await interaction.response.send_message(
            f"💬 **Comentários ({total})**\n\n{output[:1800]}",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none()
        )


async def refresh_post(channel, mid):
    if not get_post(mid):
        return

    likes, comments = get_counts(mid)

    try:
        msg = await channel.fetch_message(mid)
        await msg.edit(view=PostView(likes, comments))
    except discord.HTTPException:
        log.exception("Erro ao atualizar publicação")


intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    allowed_mentions=discord.AllowedMentions.none()
)


@bot.event
async def on_ready():
    if not getattr(bot, "_views_registered", False):
        bot.add_view(PostView())
        bot._views_registered = True

    log.info("Bot conectado: %s", bot.user)


@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    if message.channel.id != CHANNEL_ID:
        return

    attachment = None
    info = None

    for file in message.attachments:
        result = media_info(file)

        if result:
            attachment = file
            info = result
            break

    if not attachment:
        log.info(
            "Mensagem %s sem anexo reconhecido. "
            "Anexos: %s, embeds: %s",
            message.id,
            len(message.attachments),
            len(message.embeds)
        )
        return

    suffix, is_video = info

    if attachment.size > MAX_FILE_SIZE:
        return await message.reply(
            "Arquivo maior que 25 MB",
            mention_author=False
        )

    try:
        data = await attachment.read()

        filename = f"jamaica_{message.id}{suffix}"

        embed = make_embed(
            message.author,
            filename,
            message.content,
            is_video
        )

        sent = await message.channel.send(
            embed=embed,
            file=discord.File(
                io.BytesIO(data),
                filename=filename
            ),
            view=PostView()
        )

        with connect() as db:
            db.execute(
                """INSERT INTO posts
                (message_id, channel_id, author_id, created_at)
                VALUES (?, ?, ?, ?)""",
                (
                    sent.id,
                    message.channel.id,
                    message.author.id,
                    datetime.now(timezone.utc).isoformat()
                )
            )

        try:
            await message.delete()
        except discord.Forbidden:
            log.warning(
                "Sem permissão para apagar mensagem original"
            )

        log.info(
            "Publicação criada: %s | Autor: %s | Tipo: %s",
            sent.id,
            message.author.id,
            suffix
        )

    except discord.HTTPException:
        log.exception("Erro ao publicar mídia")
        await message.reply(
            "Não consegui publicar esta mídia. "
            "Verifique o tamanho e as permissões",
            mention_author=False
        )


if __name__ == "__main__":
    if not TOKEN or CHANNEL_ID <= 0:
        raise SystemExit(
            "Configure DISCORD_TOKEN e CHANNEL_ID no .env "
            "ou nas variáveis da hospedagem"
        )

    init_database()
    bot.run(TOKEN)
         #criado por victor.
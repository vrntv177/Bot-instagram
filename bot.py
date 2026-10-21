# criado por victor

import io
import os
import sqlite3
import logging
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "jamaica.db")

MAX_FILE_SIZE = 10 * 1024 * 1024

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("jamaica")

SUPPORTED_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "video/mp4",
    "video/webm",
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
            PRIMARY KEY(message_id, user_id),
            FOREIGN KEY(message_id)
            REFERENCES posts(message_id)
            ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(message_id)
            REFERENCES posts(message_id)
            ON DELETE CASCADE
        );
        """)


def get_post(mid):
    with connect() as db:
        return db.execute(
            "SELECT author_id FROM posts WHERE message_id=?",
            (mid,)
        ).fetchone()


def get_counts(mid):
    with connect() as db:
        likes = db.execute(
            "SELECT COUNT(*) FROM likes WHERE message_id=?",
            (mid,)
        ).fetchone()[0]

        comments = db.execute(
            "SELECT COUNT(*) FROM comments WHERE message_id=?",
            (mid,)
        ).fetchone()[0]

    return likes, comments


def create_embed(author, filename, caption, is_video):
    description = f"**Autor(a):** {author.mention}"

    if caption.strip():
        safe_caption = discord.utils.escape_mentions(
            caption[:900]
        )
        description += f"\n\n{safe_caption}"

    embed = discord.Embed(
        description=description,
        color=discord.Color.from_rgb(25, 110, 55)
    )

    if not is_video:
        embed.set_image(
            url=f"attachment://{filename}"
        )
    else:
        embed.description += "\n\n🎬 **Vídeo publicado**"

    return embed


class CommentModal(discord.ui.Modal):
    def __init__(self, post_id):
        super().__init__(title="Comentar publicação")
        self.post_id = post_id

        self.comment = discord.ui.TextInput(
            label="Seu comentário",
            placeholder="Digite seu comentário...",
            style=discord.TextStyle.paragraph,
            max_length=500,
            required=True
        )

        self.add_item(self.comment)

    async def on_submit(self, interaction):
        if not get_post(self.post_id):
            await interaction.response.send_message(
                "Publicação indisponível",
                ephemeral=True
            )
            return

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
            if isinstance(item, discord.ui.Button):
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
                "Publicação não encontrada",
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

        is_mod = (
            isinstance(interaction.user, discord.Member)
            and interaction.user.guild_permissions.manage_messages
        )

        if interaction.user.id != post[0] and not is_mod:
            return await interaction.response.send_message(
                "Apenas o autor ou a moderação pode excluir",
                ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)

        try:
            await interaction.message.delete()
        except discord.HTTPException:
            return await interaction.followup.send(
                "Não consegui excluir a publicação",
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
        ) or "Nenhuma curtida ainda"

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
        await msg.edit(
            view=PostView(likes, comments)
        )
    except discord.HTTPException:
        log.exception(
            "Não foi possível atualizar a publicação"
        )


intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    allowed_mentions=discord.AllowedMentions.none()
)


@bot.event
async def on_ready():
    if not getattr(bot, "_view_ready", False):
        bot.add_view(PostView())
        bot._view_ready = True

    log.info("Bot conectado: %s", bot.user)


@bot.event
async def on_message(message):
    if (
        message.author.bot
        or not message.guild
        or message.channel.id != CHANNEL_ID
    ):
        return

    attachment = next(
        (
            a for a in message.attachments
            if (a.content_type or "").split(";")[0].lower()
            in SUPPORTED_TYPES
        ),
        None
    )

    if not attachment:
        return

    if attachment.size > MAX_FILE_SIZE:
        return await message.reply(
            "Arquivo maior que 10 MB",
            mention_author=False
        )

    is_video = (
        attachment.content_type or ""
    ).lower().startswith("video/")

    try:
        data = await attachment.read()

        suffix = Path(attachment.filename).suffix.lower()

        if suffix not in {
            ".png", ".jpg", ".jpeg",
            ".webp", ".gif", ".mp4", ".webm"
        }:
            suffix = ".mp4" if is_video else ".png"

        filename = f"jamaica_{message.id}{suffix}"

        embed = create_embed(
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

    except discord.HTTPException:
        log.exception("Erro ao criar publicação")
        await message.reply(
            "Não consegui publicar. "
            "Confira as permissões do bot",
            mention_author=False
        )


if __name__ == "__main__":
    if not TOKEN or CHANNEL_ID <= 0:
        raise SystemExit(
            "Configure DISCORD_TOKEN e CHANNEL_ID no .env"
        )

    init_database()
    bot.run(TOKEN)
         #criado por victor.
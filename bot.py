# Criado por Victor

import io
import os
import sqlite3
import logging
from pathlib import Path
from datetime import datetime, timezone

import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DB_PATH = os.getenv("DB_PATH", "jamaica.db")
LEGACY_CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0") or "0")

MAX_FILE_SIZE = 25 * 1024 * 1024

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("instagram")

EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".webp",
    ".gif", ".mp4", ".mov", ".webm", ".m4v"
}

VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".webm", ".m4v"
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


# BANCO DE DADOS

def connect():
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.execute("PRAGMA foreign_keys=ON")
    return db


def init_database():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id INTEGER PRIMARY KEY,
            channel_id INTEGER NOT NULL
        );

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


def get_channel(guild_id):
    with connect() as db:
        row = db.execute(
            "SELECT channel_id FROM guild_settings WHERE guild_id=?",
            (guild_id,)
        ).fetchone()

    return row[0] if row else None


def set_channel(guild_id, channel_id):
    with connect() as db:
        db.execute(
            """
            INSERT INTO guild_settings
            (guild_id, channel_id)
            VALUES (?, ?)
            ON CONFLICT(guild_id)
            DO UPDATE SET channel_id=excluded.channel_id
            """,
            (guild_id, channel_id)
        )


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


# SISTEMA DE MIDIA

def media_info(attachment):
    mime = (
        attachment.content_type or ""
    ).split(";")[0].lower()

    suffix = Path(attachment.filename).suffix.lower()

    if suffix not in EXTENSIONS:
        suffix = MIME_TO_EXTENSION.get(mime)

    if suffix not in EXTENSIONS:
        return None

    return suffix, suffix in VIDEO_EXTENSIONS


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


# COMENTARIOS

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
                "❌ Publicação não encontrada.",
                ephemeral=True
            )

        with connect() as db:
            db.execute(
                """
                INSERT INTO comments
                (message_id, user_id, body, created_at)
                VALUES (?, ?, ?, ?)
                """,
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


# BOTOES DAS PUBLICACOES

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

    # CURTIR

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
                "❌ Publicação indisponível.",
                ephemeral=True
            )

        await interaction.response.defer()

        with connect() as db:

            existing = db.execute(
                """
                SELECT 1 FROM likes
                WHERE message_id=? AND user_id=?
                """,
                (mid, interaction.user.id)
            ).fetchone()

            if existing:

                db.execute(
                    """
                    DELETE FROM likes
                    WHERE message_id=? AND user_id=?
                    """,
                    (mid, interaction.user.id)
                )

            else:

                db.execute(
                    """
                    INSERT OR IGNORE INTO likes
                    (message_id, user_id)
                    VALUES (?, ?)
                    """,
                    (mid, interaction.user.id)
                )

        await refresh_post(interaction.channel, mid)

    # COMENTAR

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
                "❌ Publicação indisponível.",
                ephemeral=True
            )

        await interaction.response.send_modal(
            CommentModal(interaction.message.id)
        )

    # EXCLUIR PUBLICACAO

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
                "❌ Publicação não encontrada.",
                ephemeral=True
            )

        moderator = (
            isinstance(interaction.user, discord.Member)
            and interaction.permissions.manage_messages
        )

        if interaction.user.id != post[0] and not moderator:

            return await interaction.response.send_message(
                "❌ Você não pode excluir esta publicação.",
                ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)

        try:
            await interaction.message.delete()

        except discord.HTTPException:

            return await interaction.followup.send(
                "❌ Não consegui excluir a publicação.",
                ephemeral=True
            )

        with connect() as db:

            db.execute(
                "DELETE FROM posts WHERE message_id=?",
                (mid,)
            )

        await interaction.followup.send(
            "🗑️ Publicação excluída.",
            ephemeral=True
        )

    # QUEM CURTIU

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
                """
                SELECT user_id FROM likes
                WHERE message_id=?
                ORDER BY rowid DESC LIMIT 30
                """,
                (mid,)
            ).fetchall()

        names = "\n".join(
            f"• <@{uid}>" for (uid,) in rows
        ) or "Ninguém curtiu ainda."

        if total > 30:
            names += f"\nE mais {total - 30} pessoas."

        await interaction.response.send_message(
            f"❤️ **Curtidas ({total})**\n{names}",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none()
        )

    # VER COMENTARIOS

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
                """
                SELECT user_id, body FROM comments
                WHERE message_id=?
                ORDER BY id DESC LIMIT 10
                """,
                (mid,)
            ).fetchall()

        if not rows:

            return await interaction.response.send_message(
                "Ainda não há comentários.",
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


# ATUALIZAR CONTADORES

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
        log.exception("Erro ao atualizar publicação")


# CONFIGURACAO DO BOT

intents = discord.Intents.default()
intents.message_content = True


class InstagramBot(commands.Bot):

    async def setup_hook(self):

        self.add_view(PostView())

        synced = await self.tree.sync()

        log.info(
            "%s comandos sincronizados.",
            len(synced)
        )


bot = InstagramBot(
    command_prefix="!",
    intents=intents,
    allowed_mentions=discord.AllowedMentions.none()
)


# CONFIGURAR INSTAGRAM POR SERVIDOR

@bot.tree.command(
    name="instagram",
    description="Configurar o Instagram deste servidor"
)
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(
    canal="Selecione o canal de Instagram"
)
async def instagram(
    interaction: discord.Interaction,
    canal: discord.TextChannel
):

    if canal.guild.id != interaction.guild_id:

        return await interaction.response.send_message(
            "❌ Selecione um canal deste servidor.",
            ephemeral=True
        )

    perms = canal.permissions_for(interaction.guild.me)

    if not all((
        perms.view_channel,
        perms.send_messages,
        perms.embed_links,
        perms.attach_files,
        perms.read_message_history,
        perms.manage_messages
    )):

        return await interaction.response.send_message(
            "❌ O bot não possui todas as permissões necessárias nesse canal.",
            ephemeral=True
        )

    set_channel(
        interaction.guild_id,
        canal.id
    )

    embed = discord.Embed(
        title="📸 Instagram configurado",
        description=(
            f"**Servidor:** {interaction.guild.name}\n"
            f"**Canal:** {canal.mention}\n\n"
            "✅ Sistema de publicações ativo.\n"
            "✅ Sistema de curtidas ativo.\n"
            "✅ Sistema de comentários ativo.\n"
            "✅ Suporte a imagens e vídeos."
        ),
        color=0x15803D
    )

    embed.set_footer(
        text="Desenvolvido por Victor"
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )

    log.info(
        "Instagram configurado: %s | #%s",
        interaction.guild.name,
        canal.name
    )


# DESATIVAR INSTAGRAM

@bot.tree.command(
    name="instagram_desativar",
    description="Desativar o Instagram deste servidor"
)
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def instagram_desativar(
    interaction: discord.Interaction
):

    set_channel(interaction.guild_id, 0)

    await interaction.response.send_message(
        "✅ Instagram desativado neste servidor.",
        ephemeral=True
    )


# STATUS DO INSTAGRAM

@bot.tree.command(
    name="instagram_status",
    description="Consultar o status do Instagram"
)
@app_commands.guild_only()
async def instagram_status(
    interaction: discord.Interaction
):

    channel_id = get_channel(interaction.guild_id)

    if not channel_id:

        return await interaction.response.send_message(
            "❌ Instagram não configurado ou desativado.",
            ephemeral=True
        )

    canal = interaction.guild.get_channel(channel_id)

    if not isinstance(canal, discord.TextChannel):

        return await interaction.response.send_message(
            "❌ O canal configurado não existe mais.",
            ephemeral=True
        )

    if not canal.permissions_for(interaction.user).view_channel:

        return await interaction.response.send_message(
            "❌ Você não possui acesso ao canal configurado.",
            ephemeral=True
        )

    await interaction.response.send_message(
        f"✅ Instagram configurado em {canal.mention}",
        ephemeral=True
    )


# ERROS DOS COMANDOS

@bot.tree.error
async def on_app_command_error(interaction, error):

    if isinstance(error, app_commands.MissingPermissions):

        mensagem = (
            "❌ Você precisa da permissão Gerenciar Servidor."
        )

    else:

        mensagem = "❌ Não foi possível executar o comando."

    log.error("Erro no comando: %s", error)

    if interaction.response.is_done():

        await interaction.followup.send(
            mensagem,
            ephemeral=True
        )

    else:

        await interaction.response.send_message(
            mensagem,
            ephemeral=True
        )


# BOT ONLINE

@bot.event
async def on_ready():

    # Preserva a configuração antiga, caso exista.

    if LEGACY_CHANNEL_ID:

        canal = bot.get_channel(LEGACY_CHANNEL_ID)

        if isinstance(canal, discord.TextChannel):

            if get_channel(canal.guild.id) is None:

                set_channel(
                    canal.guild.id,
                    canal.id
                )

    print("\n" + "=" * 45)
    print("         INSTAGRAM MULTI-SERVIDOR")
    print("=" * 45)
    print(f"Bot: {bot.user}")
    print(f"Servidores: {len(bot.guilds)}")
    print("Status: ONLINE")
    print("Criador: Victor")
    print("=" * 45)

    for guild in bot.guilds:

        canal_id = get_channel(guild.id)

        if canal_id:

            canal = guild.get_channel(canal_id)

            if canal:
                print(f"[ATIVO] {guild.name} | #{canal.name}")
            else:
                print(f"[CANAL INEXISTENTE] {guild.name}")

        else:

            print(f"[NÃO CONFIGURADO] {guild.name}")

    print("=" * 45 + "\n")


# NOVO SERVIDOR

@bot.event
async def on_guild_join(guild):

    log.info(
        "Bot adicionado ao servidor: %s",
        guild.name
    )


# PUBLICACOES AUTOMATICAS

@bot.event
async def on_message(message):

    if message.author.bot or not message.guild:
        return

    channel_id = get_channel(message.guild.id)

    if not channel_id:
        return

    if message.channel.id != channel_id:
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
        return

    suffix, is_video = info

    limite = min(
        MAX_FILE_SIZE,
        message.guild.filesize_limit
    )

    if attachment.size > limite:

        return await message.reply(
            "❌ O arquivo ultrapassa o limite permitido.",
            mention_author=False
        )

    try:

        data = await attachment.read()

        filename = f"instagram_{message.id}{suffix}"

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
                """
                INSERT INTO posts
                (message_id, channel_id, author_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    sent.id,
                    message.channel.id,
                    message.author.id,
                    datetime.now(timezone.utc).isoformat()
                )
            )

        # Evita apagar outros anexos que não foram republicados.

        if len(message.attachments) == 1:

            try:

                await message.delete()

            except discord.HTTPException:

                log.warning(
                    "Não consegui apagar a mensagem original."
                )

        log.info(
            "Publicação criada | Servidor: %s | Autor: %s",
            message.guild.name,
            message.author.name
        )

    except discord.HTTPException:

        log.exception("Erro ao publicar mídia")

        try:
            await message.reply(
                "❌ Não consegui publicar. Verifique as permissões.",
                mention_author=False
            )
        except discord.HTTPException:
            pass


# INICIAR BOT

if __name__ == "__main__":

    if not TOKEN:

        raise SystemExit(
            "❌ Configure DISCORD_TOKEN no arquivo .env"
        )

    init_database()

    bot.run(TOKEN)

# Criado por Victor

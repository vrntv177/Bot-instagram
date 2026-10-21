#criado por victor.
import asyncio
import io
import logging
import os
import sqlite3
from datetime import datetime, timezone

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN', '')
CHANNEL_ID = int(os.getenv('CHANNEL_ID', '0'))
DB_PATH = os.getenv('DB_PATH', 'jamaica.db')
MAX_IMAGE_BYTES = 10 * 1024 * 1024
logging.basicConfig(level=logging.INFO)
log = logging.getLogger('jamaica')


def connect():
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA foreign_keys=ON')
    return db


def init_db():
    with connect() as db:
        db.executescript('''
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
                FOREIGN KEY(message_id) REFERENCES posts(message_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(message_id) REFERENCES posts(message_id) ON DELETE CASCADE
            );
        ''')


def post_exists(mid):
    with connect() as db:
        return db.execute('SELECT 1 FROM posts WHERE message_id=?', (mid,)).fetchone() is not None


def stats(mid):
    with connect() as db:
        likes = db.execute('SELECT COUNT(*) FROM likes WHERE message_id=?', (mid,)).fetchone()[0]
        comments = db.execute('SELECT COUNT(*) FROM comments WHERE message_id=?', (mid,)).fetchone()[0]
    return likes, comments


def make_embed(author, image_filename, caption):
    embed = discord.Embed(
        title='🇯🇲  JAMAICA • INSTAGRAM',
        description=f'**Autor(a):** {author.mention}' + (f'\n\n{discord.utils.escape_mentions(caption[:900])}' if caption else ''),
        color=discord.Color.from_rgb(25, 145, 65),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_image(url=f'attachment://{image_filename}')
    embed.set_footer(text='🟢 Jamaica • 🟡 Respeito • ⚫ Comunidade')
    return embed


class CommentModal(discord.ui.Modal, title='💬 Comentar na publicação'):
    comment = discord.ui.TextInput(label='Seu comentário', style=discord.TextStyle.paragraph, max_length=500, required=True)

    def __init__(self, post_id):
        super().__init__()
        self.post_id = post_id

    async def on_submit(self, interaction: discord.Interaction):
        if not post_exists(self.post_id):
            return await interaction.response.send_message('Essa publicação não está mais disponível', ephemeral=True)
        with connect() as db:
            db.execute('INSERT INTO comments (message_id, user_id, body, created_at) VALUES (?, ?, ?, ?)',
                       (self.post_id, interaction.user.id, str(self.comment.value), datetime.now(timezone.utc).isoformat()))
        await interaction.response.send_message('🇯🇲 Comentário publicado!', ephemeral=True)
        await refresh_post(interaction.client, self.post_id, interaction.channel)


class PostView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='Curtir', emoji='❤️', style=discord.ButtonStyle.secondary, custom_id='jamaica:like')
    async def like(self, interaction: discord.Interaction, button: discord.ui.Button):
        mid = interaction.message.id
        if not post_exists(mid):
            return await interaction.response.send_message('Publicação não encontrada', ephemeral=True)
        with connect() as db:
            current = db.execute('SELECT 1 FROM likes WHERE message_id=? AND user_id=?', (mid, interaction.user.id)).fetchone()
            if current:
                db.execute('DELETE FROM likes WHERE message_id=? AND user_id=?', (mid, interaction.user.id))
            else:
                db.execute('INSERT OR IGNORE INTO likes (message_id, user_id) VALUES (?, ?)', (mid, interaction.user.id))
        await interaction.response.send_message('💚 Curtida removida' if current else '💚 Você curtiu!', ephemeral=True)
        await refresh_post(interaction.client, mid, interaction.channel)

    @discord.ui.button(label='Comentar', emoji='💬', style=discord.ButtonStyle.secondary, custom_id='jamaica:comment')
    async def comment(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not post_exists(interaction.message.id):
            return await interaction.response.send_message('Publicação não encontrada', ephemeral=True)
        await interaction.response.send_modal(CommentModal(interaction.message.id))

    @discord.ui.button(label='Ver curtidas', emoji='👥', style=discord.ButtonStyle.secondary, custom_id='jamaica:likers')
    async def likers(self, interaction: discord.Interaction, button: discord.ui.Button):
        with connect() as db:
            total = db.execute('SELECT COUNT(*) FROM likes WHERE message_id=?', (interaction.message.id,)).fetchone()[0]
            rows = db.execute('SELECT user_id FROM likes WHERE message_id=? ORDER BY rowid DESC LIMIT 30', (interaction.message.id,)).fetchall()
        names = '\n'.join(f'• <@{uid}>' for (uid,) in rows) or 'Ainda não há curtidas'
        suffix = f'\n… e mais {total - 30} pessoa(s)' if total > 30 else ''
        await interaction.response.send_message(f'💚 **Curtidas ({total})**\n{names}{suffix}', ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label='Ver comentários', emoji='📝', style=discord.ButtonStyle.secondary, custom_id='jamaica:comments')
    async def comments(self, interaction: discord.Interaction, button: discord.ui.Button):
        with connect() as db:
            total = db.execute('SELECT COUNT(*) FROM comments WHERE message_id=?', (interaction.message.id,)).fetchone()[0]
            rows = db.execute('SELECT user_id, body FROM comments WHERE message_id=? ORDER BY id DESC LIMIT 10', (interaction.message.id,)).fetchall()
        if not rows:
            return await interaction.response.send_message('Ainda não há comentários', ephemeral=True)
        lines = []
        for uid, body in rows:
            safe = discord.utils.escape_mentions(discord.utils.escape_markdown(body))
            lines.append(f'**<@{uid}>:** {safe[:180]}')
        output = '\n\n'.join(lines)
        if total > 10:
            output += f'\n\nMostrando os 10 mais recentes de {total}'
        await interaction.response.send_message(f'📝 **Comentários ({total})**\n\n{output[:1900]}', ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label='Excluir', emoji='🗑️', style=discord.ButtonStyle.danger, custom_id='jamaica:delete', row=1)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        mid = interaction.message.id
        with connect() as db:
            record = db.execute('SELECT author_id FROM posts WHERE message_id=?', (mid,)).fetchone()
        if not record:
            return await interaction.response.send_message('Publicação não encontrada', ephemeral=True)
        if interaction.user.id != record[0] and not interaction.user.guild_permissions.manage_messages:
            return await interaction.response.send_message('Só o autor ou um moderador pode excluir', ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            await interaction.message.delete()
        except discord.HTTPException:
            return await interaction.followup.send('Não consegui excluir a mensagem', ephemeral=True)
        with connect() as db:
            db.execute('DELETE FROM posts WHERE message_id=?', (mid,))
        await interaction.followup.send('Publicação excluída', ephemeral=True)


async def refresh_post(bot, mid, channel):
    if not post_exists(mid):
        return
    likes, comments = stats(mid)
    try:
        msg = await channel.fetch_message(mid)
        if not msg.embeds:
            return
        embed = msg.embeds[0].copy()
        embed.set_field_at(0, name='❤️ Curtidas', value=str(likes), inline=True) if embed.fields else embed.add_field(name='❤️ Curtidas', value=str(likes), inline=True)
        if len(embed.fields) > 1:
            embed.set_field_at(1, name='💬 Comentários', value=str(comments), inline=True)
        else:
            embed.add_field(name='💬 Comentários', value=str(comments), inline=True)
        await msg.edit(embed=embed)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        log.warning('Não foi possível atualizar post %s', mid)


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents, allowed_mentions=discord.AllowedMentions.none())


@bot.event
async def on_ready():
    if not getattr(bot, '_view_registered', False):
        bot.add_view(PostView())
        bot._view_registered = True
    log.info('Conectado como %s', bot.user)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild or message.channel.id != CHANNEL_ID:
        return
    images = [a for a in message.attachments if a.content_type and a.content_type.lower().split(';')[0] in ('image/png', 'image/jpeg', 'image/webp', 'image/gif')]
    if not images:
        return
    image = images[0]
    if image.size > MAX_IMAGE_BYTES:
        await message.reply('Imagem muito grande (limite: 10 MB)', mention_author=False)
        return
    try:
        data = await image.read()
        filename = 'post.' + image.filename.rsplit('.', 1)[-1].lower()
        file = discord.File(io.BytesIO(data), filename=filename)
        embed = make_embed(message.author, filename, message.content)
        embed.add_field(name='❤️ Curtidas', value='0', inline=True)
        embed.add_field(name='💬 Comentários', value='0', inline=True)
        sent = await message.channel.send(embed=embed, file=file, view=PostView())
        with connect() as db:
            db.execute('INSERT INTO posts (message_id, channel_id, author_id, created_at) VALUES (?, ?, ?, ?)',
                       (sent.id, message.channel.id, message.author.id, datetime.now(timezone.utc).isoformat()))
        try:
            await message.delete()
        except discord.Forbidden:
            log.warning('Sem permissão para apagar a mensagem original')
    except discord.HTTPException:
        log.exception('Falha ao publicar imagem')
        await message.reply('Não consegui criar a publicação. Verifique as permissões do bot', mention_author=False)


if __name__ == '__main__':
    if not TOKEN or CHANNEL_ID <= 0:
        raise SystemExit('Configure DISCORD_TOKEN e CHANNEL_ID no arquivo .env')
    init_db()
    bot.run(TOKEN)
         #criado por victor.
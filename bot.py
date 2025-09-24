import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import os, threading
from keep_alive import keep_alive
import json

# === CONFIG BOT ===
TOKEN = os.environ.get("TOKEN")
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# === CONFIG GOOGLE SHEETS ===
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
client = gspread.authorize(creds)
sheet = client.open("Reservations").sheet1  # nom du sheet

# === STOCKAGE DES CODES ===
with open("config.json", "r") as f:
    config = json.load(f)

codes = config["salles"]

# === FONCTIONS GOOGLE SHEETS ===
def load_reservations():
    return sheet.get_all_records()

def add_reservation(res_id, user_id, username, salle, date, heure, duree):
    # Forcer l'ID en string pour éviter les problèmes
    sheet.append_row([str(res_id), str(user_id), username, salle, date, heure, str(duree)])

def delete_reservation(res_id):
    data = sheet.get_all_records()
    for i, r in enumerate(data, start=2):
        if str(r["id"]) == str(res_id):
            sheet.delete_rows(i)
            break

def get_new_id():
    data = sheet.get_all_records()
    return len(data) + 1

def salle_disponible(salle, date, heure, duree):
    start = datetime.strptime(f"{date} {heure}", "%Y-%m-%d %H:%M")
    end = start + timedelta(hours=duree)
    for r in load_reservations():
        r_start = datetime.strptime(f"{r['date']} {r['heure']}", "%Y-%m-%d %H:%M")
        r_end = r_start + timedelta(hours=int(r['duree']))
        if str(r["salle"]) == salle and not (end <= r_start or start >= r_end):
            return False
    return True

# === UTILITAIRE POUR EMBED ===
def format_reservations_embed(reservations, titre):
    if not reservations:
        return None

    grouped = {}
    for r in reservations:
        salle = str(r['salle'])
        if salle not in grouped:
            grouped[salle] = []
        grouped[salle].append(r)

    for salle in grouped:
        grouped[salle].sort(
            key=lambda r: datetime.strptime(f"{r['date']} {r['heure']}", "%Y-%m-%d %H:%M")
        )

    embed = discord.Embed(title=titre, color=discord.Color.blurple())
    for salle in sorted(grouped.keys()):
        value = ""
        for r in grouped[salle]:
            value += f"ID {r['id']} | {r['date']} {r['heure']} ({r['duree']}h) - {r['username']} (<@{r['user']}>)\n"
        embed.add_field(name=f"🎶 Salle {salle}", value=value, inline=False)
    return embed

# === EVENTS ===
@bot.event
async def on_ready():
    print(f"Connecté en tant que {bot.user}")

# === COMMANDES ===
@bot.command()
async def reserver(ctx, salle: str, date: str, heure: str, duree: int):
    if salle not in codes:
        return await ctx.send("❌ Salle invalide (Sevenans ou Belfort).")

    start = datetime.strptime(f"{date} {heure}", "%Y-%m-%d %H:%M")
    if start > datetime.now() + timedelta(days=7):
        return await ctx.send("❌ Impossible de réserver à plus d'une semaine d'avance.")

    if not salle_disponible(salle, date, heure, duree):
        return await ctx.send("❌ Cette salle est déjà réservée à ce créneau.")

    res_id = get_new_id()
    add_reservation(res_id, ctx.author.id, ctx.author.name, salle, date, heure, duree)
    await ctx.send(f"✅ Réservation #{res_id} confirmée pour salle {salle} le {date} à {heure} pendant {duree}h.")

@bot.command()
async def planning(ctx):
    data = load_reservations()
    now = datetime.now()
    # On affiche seulement si la fin est après "now"
    future_reservations = []
    for r in data:
        start = datetime.strptime(f"{r['date']} {r['heure']}", "%Y-%m-%d %H:%M")
        end = start + timedelta(hours=int(r['duree']))
        if end >= now:
            future_reservations.append(r)

    if not future_reservations:
        return await ctx.send("📅 Aucun créneau à venir.")

    embed = format_reservations_embed(future_reservations, "📅 Réservations à venir")
    await ctx.send(embed=embed)

@bot.command()
async def historique(ctx):
    data = load_reservations()
    now = datetime.now()
    past_reservations = []
    # Suppression des réservations passées depuis plus de 7 jours
    for r in data:
        start = datetime.strptime(f"{r['date']} {r['heure']}", "%Y-%m-%d %H:%M")
        end = start + timedelta(hours=int(r['duree']))
        if now > end + timedelta(days=7):
            delete_reservation(r['id'])
            print(f"🗑 Réservation #{r['id']} supprimée (trop ancienne).")

    for r in data:
        start = datetime.strptime(f"{r['date']} {r['heure']}", "%Y-%m-%d %H:%M")
        end = start + timedelta(hours=int(r['duree']))
        if now > end and now <= end + timedelta(days=7):
            past_reservations.append(r)

    if not past_reservations:
        return await ctx.send("📜 Aucun historique récent.")

    embed = format_reservations_embed(past_reservations, "📜 Historique des 7 derniers jours")
    await ctx.send(embed=embed)

@bot.command()
async def annuler(ctx, res_id: int):
    data = load_reservations()
    res = next((r for r in data if str(r["id"]) == str(res_id)), None)
    if not res:
        return await ctx.send("❌ Réservation introuvable.")
    if str(res["user"]) != str(ctx.author.id):
        return await ctx.send("❌ Vous ne pouvez annuler que vos propres réservations.")
    delete_reservation(res_id)
    await ctx.send(f"✅ Réservation #{res_id} annulée.")

@bot.command()
@commands.has_permissions(administrator=True)
async def adminannuler(ctx, res_id: int):
    data = load_reservations()
    res = next((r for r in data if str(r["id"]) == str(res_id)), None)
    if not res:
        return await ctx.send("❌ Réservation introuvable.")
    delete_reservation(res_id)
    await ctx.send(f"🛑 Réservation #{res_id} annulée par un admin.")

def save_codes():
    with open("config.json", "w") as f:
        json.dump({"salles": codes}, f, indent=4)

@bot.command()
@commands.has_permissions(administrator=True)
async def setcode(ctx, salle: str, code: str):
    if salle not in codes:
        return await ctx.send("❌ Salle invalide")
    codes[salle] = code
    save_codes()
    await ctx.send(f"🔑 Code de la salle {salle} mis à jour.")

@bot.command()
async def code(ctx):
    """Envoie le code de ta réservation si elle commence dans moins d'une heure ou si elle est déjà en cours."""
    now = datetime.now()
    data = load_reservations()

    for r in data:
        if str(r["user"]) != str(ctx.author.id):
            continue

        start = datetime.strptime(f"{r['date']} {r['heure']}", "%Y-%m-%d %H:%M")
        end = start + timedelta(hours=int(r['duree']))

        # Autoriser si la réservation commence dans < 1h OU si elle a commencé il y a < 1h OU si elle est en cours
        if (now <= start <= now + timedelta(hours=1)) or (start <= now <= end):
            try:
                await ctx.author.send(
                    f"🔑 Voici ton code pour la salle **{r['salle']}** : `{codes[r['salle']]}`\n"
                    f"📅 Créneau : {r['date']} {r['heure']} ({r['duree']}h)"
                )
                return await ctx.send("✅ Le code t’a été envoyé en message privé !")
            except:
                return await ctx.send("❌ Impossible de t’envoyer un DM, vérifie tes paramètres Discord.")

    await ctx.send("❌ Tu n’as aucune réservation à venir dans l’heure ou en cours.")


keep_alive()
bot.run(TOKEN)


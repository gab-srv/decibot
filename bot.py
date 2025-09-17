import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import os, threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# === CONFIG BOT ===
TOKEN = os.environ.get("TOKEN")
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="/", intents=intents)

# === FAUX PORT ===
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Discord en ligne")

def run_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("", port), Handler)
    server.serve_forever()

threading.Thread(target=run_server, daemon=True).start()

# === CONFIG GOOGLE SHEETS ===
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
client = gspread.authorize(creds)
sheet = client.open("Reservations").sheet1  # nom du sheet

# === STOCKAGE DES CODES ===
codes = {"Sevenans": "1709", "Belfort": "1705"}

# === FONCTIONS GOOGLE SHEETS ===
def load_reservations():
    return sheet.get_all_records()

def add_reservation(res_id, user, salle, date, heure, duree):
    sheet.append_row([res_id, user, salle, date, heure, duree])

def delete_reservation(res_id):
    data = sheet.get_all_records()
    for i, r in enumerate(data, start=2):
        if r["id"] == res_id:
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
        r_end = r_start + timedelta(hours=r['duree'])
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
            value += f"ID {r['id']} | {r['date']} {r['heure']} ({r['duree']}h) - <@{r['user']}>\n"
        embed.add_field(name=f"🎶 Salle {salle}", value=value, inline=False)
    return embed

# === EVENTS ===
@bot.event
async def on_ready():
    print(f"Connecté en tant que {bot.user}")
    check_reservations.start()

# === COMMANDES ===
@bot.command()
async def reserver(ctx, salle: str, date: str, heure: str, duree: int):
    if salle not in ["1", "2"]:
        return await ctx.send("❌ Salle invalide (1 ou 2).")

    start = datetime.strptime(f"{date} {heure}", "%Y-%m-%d %H:%M")
    if start > datetime.now() + timedelta(days=7):
        return await ctx.send("❌ Impossible de réserver à plus d'une semaine d'avance.")

    if not salle_disponible(salle, date, heure, duree):
        return await ctx.send("❌ Cette salle est déjà réservée à ce créneau.")

    res_id = get_new_id()
    add_reservation(res_id, ctx.author.id, salle, date, heure, duree)
    await ctx.send(f"✅ Réservation #{res_id} confirmée pour salle {salle} le {date} à {heure} pendant {duree}h.")

@bot.command()
async def planning(ctx):
    data = load_reservations()
    now = datetime.now()
    future_reservations = [r for r in data if datetime.strptime(f"{r['date']} {r['heure']}", "%Y-%m-%d %H:%M") >= now]

    if not future_reservations:
        return await ctx.send("📅 Aucun créneau à venir.")

    embed = format_reservations_embed(future_reservations, "📅 Réservations à venir")
    await ctx.send(embed=embed)

@bot.command()
async def historique(ctx):
    data = load_reservations()
    now = datetime.now()
    past_reservations = []
    for r in data:
        start = datetime.strptime(f"{r['date']} {r['heure']}", "%Y-%m-%d %H:%M")
        end = start + timedelta(hours=r['duree'])
        if now > end and now <= end + timedelta(days=7):
            past_reservations.append(r)

    if not past_reservations:
        return await ctx.send("📜 Aucun historique récent.")

    embed = format_reservations_embed(past_reservations, "📜 Historique des 7 derniers jours")
    await ctx.send(embed=embed)

@bot.command()
async def annuler(ctx, res_id: int):
    data = load_reservations()
    res = next((r for r in data if r["id"] == res_id), None)
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
    res = next((r for r in data if r["id"] == res_id), None)
    if not res:
        return await ctx.send("❌ Réservation introuvable.")
    delete_reservation(res_id)
    await ctx.send(f"🛑 Réservation #{res_id} annulée par un admin.")

@bot.command()
@commands.has_permissions(administrator=True)
async def setcode(ctx, salle: str, code: str):
    if salle not in codes:
        return await ctx.send("❌ Salle invalide")
    codes[salle] = code
    await ctx.send(f"🔑 Code de la salle {salle} mis à jour.")

# === TÂCHE RÉCURRENTE ===
@tasks.loop(minutes=1)
async def check_reservations():
    now = datetime.now()
    data = load_reservations()

    # Envoi du code 15 min avant le créneau
    for r in data:
        start = datetime.strptime(f"{r['date']} {r['heure']}", "%Y-%m-%d %H:%M")
        if now + timedelta(minutes=15) >= start and now < start:
            user = bot.get_user(int(r["user"]))
            if user:
                await user.send(
                    f"Salut 👋 ! Voici ton code pour la salle {r['salle']} : {codes[r['salle']]} "
                    f"(créneau {r['date']} {r['heure']})"
                )

    # Suppression des réservations passées depuis plus de 7 jours
    for r in data:
        start = datetime.strptime(f"{r['date']} {r['heure']}", "%Y-%m-%d %H:%M")
        end = start + timedelta(hours=r['duree'])
        if now > end + timedelta(days=7):
            delete_reservation(r['id'])
            print(f"🗑 Réservation #{r['id']} supprimée (trop ancienne).")

bot.run(TOKEN)

import os
import discord
from discord import app_commands
from discord.ext import tasks
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import asyncio
from discord.utils import get

# =======================
# CONFIGURATION
# =======================
TOKEN = os.environ.get("TOKEN")  # Token Discord via variable d'environnement Render
GUILD_ID = 708681984766902403  # Remplace par ton serveur Discord
SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
SHEET_NAME = "Reservations"
CLEANUP_DAYS = 7

# Codes d'accès par nom de salle
codes = {"Sevenans": "1709", "Belfort": "1705"}

# =======================
# GOOGLE SHEET
# =======================
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", SCOPE)
client = gspread.authorize(creds)
sheet = client.open(SHEET_NAME).sheet1

# =======================
# SERVEUR HTTP BIDON (pour Render Web Service)
# =======================
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

# =======================
# BOT DISCORD
# =======================
intents = discord.Intents.default()
intents.members = True  # Nécessaire pour envoyer des DMs
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# =======================
# FONCTIONS UTILITAIRES
# =======================
def ajouter_reservation(user, salle, date_str, heure_str, duree):
    res_id = str(int(datetime.now().timestamp()))
    row = [res_id, user, salle, date_str, heure_str, str(duree)]
    sheet.append_row(row)

def supprimer_reservation_by_id(res_id):
    all_records = sheet.get_all_records()
    for idx, r in enumerate(all_records, start=2):
        if str(r["id"]) == str(res_id):
            sheet.delete_row(idx)
            return True
    return False

def nettoyer_anciennes_reservations():
    all_records = sheet.get_all_records()
    now = datetime.now()
    for idx, r in enumerate(all_records, start=2):
        res_date = datetime.strptime(r["date"], "%Y-%m-%d")
        if res_date < now - timedelta(days=CLEANUP_DAYS):
            sheet.delete_row(idx)

# =======================
# TÂCHES ASYNCHRONE
# =======================
@tasks.loop(hours=24)
async def cleanup_task():
    nettoyer_anciennes_reservations()

async def send_codes_task():
    await bot.wait_until_ready()
    sent_ids = set()  # Pour éviter d'envoyer plusieurs fois
    while not bot.is_closed():
        all_records = sheet.get_all_records()
        now = datetime.now()
        for r in all_records:
            res_datetime = datetime.strptime(r["date"] + " " + r["heure"], "%Y-%m-%d %H:%M")
            if timedelta(minutes=29) <= res_datetime - now <= timedelta(minutes=31):
                if r["id"] in sent_ids:
                    continue
                user = get(bot.get_all_members(), name=r["user"])
                if user:
                    code_salle = codes.get(r["salle"], "Code inconnu")
                    try:
                        await user.send(f"Votre créneau pour la salle {r['salle']} commence dans 30 minutes !\nCode d'accès : {code_salle}")
                        sent_ids.add(r["id"])
                    except Exception as e:
                        print(f"Impossible d'envoyer un DM à {r['user']} : {e}")
        await asyncio.sleep(60)

# =======================
# COMMANDES SLASH
# =======================
@tree.command(name="reserver", description="Réserver une salle", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(
    salle="Nom de la salle",
    date="Date YYYY-MM-DD",
    heure="Heure HH:MM",
    duree="Durée en heures"
)
async def reserver(interaction: discord.Interaction, salle: str, date: str, heure: str, duree: int):
    salle = salle.strip()
    if salle not in codes:
        await interaction.response.send_message(
            f"Salle inconnue. Salles disponibles : {', '.join(codes.keys())}",
            ephemeral=True
        )
        return

    date_obj = datetime.strptime(date, "%Y-%m-%d")
    if date_obj > datetime.now() + timedelta(days=7):
        await interaction.response.send_message(
            "Tu ne peux pas réserver plus d'une semaine à l'avance.", ephemeral=True
        )
        return

    ajouter_reservation(interaction.user.name, salle, date, heure, duree)
    await interaction.response.send_message(
        f"Salle {salle} réservée pour {date} à {heure} pendant {duree}h.\nVous recevrez le code d'accès 30 minutes avant le créneau.",
        ephemeral=True
    )

@tree.command(name="planning", description="Voir les réservations à venir", guild=discord.Object(id=GUILD_ID))
async def planning(interaction: discord.Interaction):
    all_records = sheet.get_all_records()
    now = datetime.now()
    future_records = [r for r in all_records if datetime.strptime(r["date"], "%Y-%m-%d") >= now]
    future_records.sort(key=lambda x: (x["salle"], x["date"], x["heure"]))

    msg = "**Planning des réservations :**\n"
    for r in future_records:
        msg += f"Salle {r['salle']} : {r['date']} à {r['heure']} ({r['user']})\n"

    await interaction.response.send_message(msg or "Aucune réservation à venir.", ephemeral=False)

@tree.command(name="annuler", description="Annuler ta réservation", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(res_id="ID de la réservation")
async def annuler(interaction: discord.Interaction, res_id: str):
    all_records = sheet.get_all_records()
    for r in all_records:
        if str(r["id"]) == str(res_id) and r["user"] == interaction.user.name:
            supprimer_reservation_by_id(res_id)
            await interaction.response.send_message("Réservation annulée.", ephemeral=True)
            return
    await interaction.response.send_message("Réservation introuvable ou vous n'êtes pas autorisé.", ephemeral=True)

@tree.command(name="adminannuler", description="Annuler une réservation (admin)", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(res_id="ID de la réservation")
async def adminannuler(interaction: discord.Interaction, res_id: str):
    if interaction.user.guild_permissions.administrator:
        if supprimer_reservation_by_id(res_id):
            await interaction.response.send_message("Réservation annulée par admin.", ephemeral=True)
        else:
            await interaction.response.send_message("Réservation introuvable.", ephemeral=True)
    else:
        await interaction.response.send_message("Vous n'êtes pas admin.", ephemeral=True)

# =======================
# EVENTS
# =======================
@bot.event
async def on_ready():
    await tree.sync(guild=discord.Object(id=GUILD_ID))
    cleanup_task.start()
    bot.loop.create_task(send_codes_task())
    print(f"Connecté en tant que {bot.user}!")

# =======================
# LANCEMENT DU BOT
# =======================
bot.run(TOKEN)

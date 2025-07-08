import google.generativeai as genai
import datetime
import os
from PIL import Image, ImageDraw, ImageFont
#from instagrapi import Client # Importa la libreria
#from instagrapi.types import StoryMention, StoryMedia, StoryLink, StoryHashtag # Per storie, se necessario

# --- Credenziali Instagram (USARE CON ESTREMA CAUTELA) ---
INSTAGRAM_USERNAME = "prova6953"
INSTAGRAM_PASSWORD = "prova123456"

# --- Configurazione API Gemini ---
GEMINI_API_KEY = "AIzaSyBrYdXKayH6bttpGaPai9UMAWO-qjoM9Ts" # USA LA TUA VERA CHIAVE QUI
GEMINI_MODEL_NAME = "gemini-1.5-flash-latest"
GENERATION_CONFIG = genai.types.GenerationConfig(
    max_output_tokens=150, # Aumentato leggermente per permettere i \n
    temperature=0.7,
    top_p=0.95,
    top_k=50
)

# --- Configurazione Immagine ---
IMAGE_WIDTH = 1080
IMAGE_HEIGHT = 1350
TEXT_COLOR = (50, 50, 50) # Grigio scuro
BACKGROUND_COLOR = (255, 255, 255) # Beige chiaro per default
FONT_PATH = "VintageTypistBold-lxOWd.otf"  # ESEMPIO! SOSTITUISCI CON UN FONT VALIDO

TEMPLATE_IMAGE_PATH = "AneddotiStorici.png"
OUTPUT_IMAGE_NAME_TEMPLATE = "aneddoto_del_giorno_{day}_{month}.png"

# --- IMPOSTAZIONI GLOBALI PER LA DIMENSIONE DEL FONT ---
TITLE_FONT_SIZE = 120
LINE_SPACING_ANECDOTE = 15 # Spaziatura tra le linee dell'aneddoto

# --- MARGINI PER IL TESTO (in pixel) ---
MARGIN_X = 80  # Margine sinistro (e implicitamente destro se il testo non è troppo largo)
MARGIN_X_TITLE = 150
MARGIN_Y_TITLE = int(IMAGE_HEIGHT * 0.12) # Margine superiore per il titolo
MARGIN_Y_ANECDOTE = int(IMAGE_HEIGHT * 0.4) # Margine superiore per l'aneddoto

def wrap_text_custom(text, max_line_length=30): #TODO: SPOSTARE CHIAMATA DIRETTAMENTE NEL MAIN
    if not text:
        return ""

    words = text.split(' ') # Dividi il testo in parole
    wrapped_lines = []
    current_line = ""

    for word in words:
        # Se la parola stessa è più lunga della lunghezza massima della riga
        # (caso estremo, potresti volerla spezzare forzatamente o gestirla diversamente)
        if len(word) > max_line_length:
            if current_line: # Se c'è qualcosa nella riga corrente, aggiungila prima
                wrapped_lines.append(current_line)
                current_line = ""
            # Aggiungi la parola lunga su una riga a sé (o implementa una logica per spezzarla)
            wrapped_lines.append(word)
            continue # Passa alla parola successiva

        # Controlla se aggiungere la prossima parola (più lo spazio) supera la lunghezza massima
        if current_line: # Se la riga corrente non è vuota
            if len(current_line) + len(word) + 1 > max_line_length: # +1 per lo spazio
                wrapped_lines.append(current_line) # Aggiungi la riga corrente completata
                current_line = word # Inizia una nuova riga con la parola corrente
            else:
                current_line += " " + word # Aggiungi la parola alla riga corrente
        else: # Se la riga corrente è vuota (inizio o dopo un "a capo")
            current_line = word

    # Aggiungi l'ultima riga corrente, se non è vuota
    if current_line:
        wrapped_lines.append(current_line)

    return "\n".join(wrapped_lines)


def get_historical_anecdote_gemini(day, month_name):
    if not GEMINI_API_KEY:
        print("Errore: La GEMINI_API_KEY non è impostata o è ancora quella di esempio.")
        print("Imposta la tua chiave API per usare Gemini.")
        # Esempio di testo formattato come dovrebbe essere dopo la sostituzione
        # return "Il 15 Aprile\n1452 nasceva Leonardo da\nVinci, genio universale del\nRinascimento italiano, noto per\nopere come la Gioconda."
        return None
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(GEMINI_MODEL_NAME)
    except Exception as e:
        print(f"Errore durante la configurazione del modello Gemini: {e}")
        return None

    prompt = (
        f"Raccontami in italiano un brevissimo e interessante aneddoto o evento storico accaduto "
        f"il {day} {month_name}. "
        f"Massimo 2-3 frasi. Deve essere conciso e adatto per un post social. "
        f"REQUISITO DI LUNGHEZZA IMPORTANTE: L'aneddoto completo deve avere una lunghezza compresa tra 200 e 270 caratteri, inclusi tutti gli spazi e la punteggiatura. Cerca di avvicinarti il più possibile a questo intervallo."
        f"Non includere il prompt nella risposta. Non iniziare con 'Certo, ecco un aneddoto...' o simili, vai direttamente all'aneddoto."
    )
    print(f"Invio richiesta a Gemini per il {day} {month_name}...")
    try:
        response = model.generate_content(prompt, generation_config=GENERATION_CONFIG)
        if response.text:
            anecdote = response.text.strip()
            phrases_to_remove = [ # Questa parte rimane per pulizia generale
                "certo, ecco un aneddoto:", "ecco un aneddoto:", "un aneddoto interessante:",
                "raccontami in italiano un brevissimo e interessante aneddoto o evento storico accaduto"
            ]
            for phrase in phrases_to_remove:
                if anecdote.lower().startswith(phrase.lower()):
                    anecdote = anecdote[len(phrase):].strip()
                    if anecdote: anecdote = anecdote[0].upper() + anecdote[1:]

            return anecdote
        else:
            if response.candidates:
                for candidate in response.candidates:
                    if candidate.finish_reason != 1: # NOT_STOPPED
                        print(f"  Candidato bloccato. Ragione: {candidate.finish_reason.name}")
                        if candidate.safety_ratings:
                            for rating in candidate.safety_ratings:
                                print(f"    Safety Rating: {rating.category.name} - {rating.probability.name}")
            print(f"Risposta da Gemini vuota o non valida. Prompt Safety Feedback: {response.prompt_feedback}")
            return None
    except Exception as e:
        print(f"Errore durante la chiamata API a Gemini: {e}")
        return None

def create_instagram_post_image(day_str, month_str_human, anecdote_text, output_filename, anecdote_font_size):
    if not os.path.exists(TEMPLATE_IMAGE_PATH):
        print(f"Template '{TEMPLATE_IMAGE_PATH}' non trovato. Uso sfondo di default.")
        img = Image.new('RGB', (IMAGE_WIDTH, IMAGE_HEIGHT), color=BACKGROUND_COLOR)
    else:
        try:
            img = Image.open(TEMPLATE_IMAGE_PATH).convert("RGB").resize((IMAGE_WIDTH, IMAGE_HEIGHT))
        except Exception as e:
            print(f"Errore apertura template '{TEMPLATE_IMAGE_PATH}': {e}. Uso sfondo di default.")
            img = Image.new('RGB', (IMAGE_WIDTH, IMAGE_HEIGHT), color=BACKGROUND_COLOR)
    draw = ImageDraw.Draw(img)

    font_title = None
    try:
        font_title = ImageFont.truetype(FONT_PATH, TITLE_FONT_SIZE)
    except IOError:
        print(f"Attenzione: Font '{FONT_PATH}' non trovato. Uso fallback Arial per il titolo.")
        try:
            font_title = ImageFont.truetype("arial.ttf", TITLE_FONT_SIZE)
        except IOError:
            print("Attenzione: Arial non trovato. Uso font di default Pillow per il titolo.")
            font_title = ImageFont.load_default() # Non accetta size, quindi sarà piccolo

    font_anecdote = None

    try:
        font_anecdote = ImageFont.truetype(FONT_PATH, anecdote_font_size)
    except IOError:
        print(f"Attenzione: Font '{FONT_PATH}' non trovato. Uso fallback Arial per l'aneddoto.")
        try:
            font_anecdote = ImageFont.truetype("arial.ttf", anecdote_font_size)
        except IOError:
            print("Attenzione: Arial non trovato. Uso font di default Pillow per l'aneddoto.")
            font_anecdote = ImageFont.load_default() # Non accetta size

    # --- Titolo (Data) ---
    title_text_content = f"{day_str} {month_str_human.capitalize()}"
    # Disegna il titolo con allineamento a sinistra basato sui margini
    draw.text(
        (MARGIN_X_TITLE, MARGIN_Y_TITLE),
        title_text_content,
        font=font_title,
        fill=TEXT_COLOR
    )

    # --- Aneddoto ---
    if anecdote_text:
        # Disegna l'aneddoto. Il testo dovrebbe già contenere \n da Gemini.
        # Usiamo multiline_text che gestisce i \n e l'allineamento.
        draw.multiline_text(
            (MARGIN_X, MARGIN_Y_ANECDOTE),
            anecdote_text, # Il testo con i \n forniti da Gemini
            font=font_anecdote,
            fill=TEXT_COLOR,
            align="left", # Allinea il blocco di testo e le singole linee a sinistra
            spacing=LINE_SPACING_ANECDOTE
        )
    else:
        print("Nessun testo per l'aneddoto.")

    img.save(output_filename)
    print(f"Immagine salvata come: {output_filename}")

"""
def upload_to_instagram(image_path, caption_text):

    if not INSTAGRAM_USERNAME or not INSTAGRAM_PASSWORD or INSTAGRAM_USERNAME == "IL_TUO_USERNAME_INSTAGRAM":
        print("Credenziali Instagram non configurate. Impossibile caricare.")
        return False

    cl = Client()
    # cl.login_by_sessionid = "SESSION_ID_SE_LO_HAI_SALVATO" # Alternativa al login
    try:
        print(f"Tentativo di login su Instagram come {INSTAGRAM_USERNAME}...")
        # Qui potresti dover gestire le eccezioni per 2FA, challenge, ecc.
        # Salva le impostazioni di sessione per evitare login ripetuti (più sicuro)
        session_file = f"{INSTAGRAM_USERNAME}_session.json"
        if os.path.exists(session_file):
            cl.load_settings(session_file)
            cl.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD) # Il login potrebbe essere bypassato se la sessione è valida
            cl.get_timeline_feed() # Test per vedere se la sessione è valida
            print("Login con sessione esistente riuscito.")
        else:
            cl.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)
            cl.dump_settings(session_file) # Salva la sessione per usi futuri
            print("Login riuscito e sessione salvata.")

    except Exception as e:
        print(f"Errore durante il login su Instagram: {e}")
        print("Possibili cause: credenziali errate, verifica 2FA, challenge richiesta da Instagram.")
        print("Se usi questa automazione frequentemente, Instagram potrebbe bloccarla.")
        return False

    try:
        print(f"Caricamento di '{image_path}' su Instagram...")
        media = cl.photo_upload(
            path=image_path,
            caption=caption_text
        )
        if media:
            print(f"Immagine caricata con successo! Media ID: {media.id}")
            # print(f"Link al post: https://www.instagram.com/p/{media.code}/") # Non sempre 'code' è disponibile subito
            return True
        else:
            print("Caricamento fallito, la libreria non ha restituito un oggetto media.")
            return False
    except Exception as e:
        print(f"Errore durante il caricamento dell'immagine su Instagram: {e}")
        return False
    """

if __name__ == "__main__":
    # Controllo API Key Gemini (omesso per brevità, ma dovrebbe esserci)
    if not GEMINI_API_KEY or GEMINI_API_KEY == "chiave":
        print("ATTENZIONE: GEMINI_API_KEY non impostata correttamente.")
        # exit() # Potresti voler uscire

    # Controllo credenziali Instagram
    if not INSTAGRAM_USERNAME or not INSTAGRAM_PASSWORD or INSTAGRAM_USERNAME == "IL_TUO_USERNAME_INSTAGRAM":
        print("---------------------------------------------------------------------------")
        print("ATTENZIONE: Credenziali INSTAGRAM_USERNAME e/o INSTAGRAM_PASSWORD non impostate.")
        print("Il caricamento su Instagram sarà saltato.")
        print("---------------------------------------------------------------------------")


    # ... (codice per font e data invariato) ...
    original_font_path = FONT_PATH
    if not os.path.exists(FONT_PATH):
        print(f"ATTENZIONE: Font '{FONT_PATH}' non trovato. Tento di usare 'arial.ttf'.")
        FONT_PATH = "arial.ttf"
        if not os.path.exists(FONT_PATH):
            print(f"ATTENZIONE: Font 'arial.ttf' non trovato.")
            FONT_PATH = original_font_path

    #now = datetime.datetime.now()
    #current_day, current_month_numeric = now.day, now.month
    current_day, current_month_numeric, current_month_name_it = 6, 8, "Agosto"  #TODO: SET DAY HERE
    month_mapping_it = {
        1: "Gennaio", 2: "Febbraio", 3: "Marzo", 4: "Aprile", 5: "Maggio", 6: "Giugno",
        7: "Luglio", 8: "Agosto", 9: "Settembre", 10: "Ottobre", 11: "Novembre", 12: "Dicembre"
    }
    #current_month_name_it = month_mapping_it.get(current_month_numeric, now.strftime("%B"))

    print(f"Oggi è il {current_day} {current_month_name_it}")

    anecdote = get_historical_anecdote_gemini(current_day, current_month_name_it)

    anecdote_font_size = 65  # Dimensione di default per l'aneddoto

    if anecdote:

        # Controlla la lunghezza dell'aneddoto
        print(f"Lunghezza dell'aneddoto: {len(anecdote)} caratteri")
        if len(anecdote) >= 400 :
            anecdote_font_size = 45
            anecdote = wrap_text_custom(anecdote, max_line_length=50)
        elif len(anecdote) >= 335 and len(anecdote) < 400:
            anecdote_font_size = 50
            anecdote = wrap_text_custom(anecdote, max_line_length=48)
        elif len(anecdote) >= 290 and len(anecdote) < 335:
            anecdote_font_size = 55
            anecdote = wrap_text_custom(anecdote, max_line_length=45)
        elif len(anecdote) >= 275 and len(anecdote) < 290:
            anecdote_font_size = 58
            anecdote = wrap_text_custom(anecdote, max_line_length=40)
        elif len(anecdote) >= 230 and len(anecdote) < 375:
            anecdote_font_size = 60
            anecdote = wrap_text_custom(anecdote, max_line_length=40)
        else: # < 230
            anecdote_font_size = 65
            anecdote = wrap_text_custom(anecdote, max_line_length=38)
            
        print("\n--- Aneddoto del Giorno (Wrappato per Immagine) ---")
        print(anecdote)
#        anecdote="""Il 16 giugno 1963, Valentina Tereskova

#un'epoca."""
        
        output_fn = OUTPUT_IMAGE_NAME_TEMPLATE.format(day=current_day, month=current_month_numeric)
        create_instagram_post_image(str(current_day), current_month_name_it, anecdote, output_fn, anecdote_font_size)

        # Preparazione della caption per Instagram (usa l'aneddoto originale non wrappato o una versione leggermente modificata)
        # Potresti aggiungere hashtag qui
        instagram_caption = f"✨ Aneddoto del {current_day} {current_month_name_it} ✨\n\n{anecdote}\n\n"
        instagram_caption += "#aneddoto #storia #storiadelgiorno #accaddeoggi #curiosità"
        # Aggiungi altri hashtag pertinenti, es. #{month_name.lower()} #{day}
        instagram_caption += f" #{current_month_name_it.lower().replace('à', 'a')} #{current_day}{current_month_name_it.lower().replace('à', 'a')}"


        print("\n--- Caption per Instagram ---")
        print(instagram_caption)

        """
        # Caricamento su Instagram
        if INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD and INSTAGRAM_USERNAME != "IL_TUO_USERNAME_INSTAGRAM":
            upload_success = upload_to_instagram(output_fn, instagram_caption)
            if upload_success:
                print("Post pubblicato con successo su Instagram.")
            else:
                print("Impossibile pubblicare il post su Instagram.")
        else:
            print("Caricamento su Instagram saltato a causa di credenziali mancanti.")
            print(f"Puoi trovare l'immagine generata qui: {os.path.abspath(output_fn)}")
        """
    else:
        print("\nNessun aneddoto recuperato o generato.")
import google.generativeai as genai
import datetime
import os
from PIL import Image, ImageDraw, ImageFont
from instagrapi import Client

from google.cloud import secretmanager
from google.cloud import storage
import google.auth

# --- NOMI DEI FILE STATICI (devono essere nel pacchetto di deploy) ---
FONT_FILE_NAME = "VintageTypistBold-lxOWd.otf"
TEMPLATE_IMAGE_FILE_NAME = "AneddotiStorici.png"

# --- NOMI DEI SECRET (da creare in Secret Manager) ---
SECRET_INSTA_USER_ID = "INSTAGRAM_USERNAME"
SECRET_INSTA_PASS_ID = "INSTAGRAM_PASSWORD"
SECRET_GEMINI_KEY_ID = "GEMINI_API_KEY"

# --- Configurazione API Gemini ---
GEMINI_MODEL_NAME = "gemini-1.5-flash-latest"
GENERATION_CONFIG = genai.types.GenerationConfig(
    max_output_tokens=150,
    temperature=0.7,
    top_p=0.95,
    top_k=50
)

# --- Configurazione Immagine ---
IMAGE_WIDTH = 1080
IMAGE_HEIGHT = 1350
TEXT_COLOR = (50, 50, 50)
BACKGROUND_COLOR = (255, 255, 255)
OUTPUT_IMAGE_NAME_TEMPLATE = "aneddoto_del_giorno_{day}_{month}.png"
TITLE_FONT_SIZE = 120
LINE_SPACING_ANECDOTE = 15
MARGIN_X = 80
MARGIN_X_TITLE = 150
MARGIN_Y_TITLE = int(IMAGE_HEIGHT * 0.12)
MARGIN_Y_ANECDOTE = int(IMAGE_HEIGHT * 0.4)

# Variabili globali per credenziali e percorsi, inizializzate in seguito
PROJECT_ID = None
INSTAGRAM_USERNAME = None
INSTAGRAM_PASSWORD = None
GEMINI_API_KEY = None
FONT_PATH = None
TEMPLATE_IMAGE_PATH = None
GCS_BUCKET_NAME = None # Verrà letto dalle variabili d'ambiente
GCS_SESSION_BLOB_NAME = None # Verrà letto dalle variabili d'ambiente


# --- FUNZIONI HELPER ---
def _initialize_globals():
    """Inizializza le variabili globali leggendo secrets e env vars."""
    global PROJECT_ID, INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD, GEMINI_API_KEY
    global FONT_PATH, TEMPLATE_IMAGE_PATH, GCS_BUCKET_NAME, GCS_SESSION_BLOB_NAME

    if PROJECT_ID is None: # Esegui solo una volta
        try:
            _, project_id_from_auth = google.auth.default()
            PROJECT_ID = project_id_from_auth
            print(f"Project ID recuperato: {PROJECT_ID}")

            INSTAGRAM_USERNAME = get_secret(PROJECT_ID, SECRET_INSTA_USER_ID)
            INSTAGRAM_PASSWORD = get_secret(PROJECT_ID, SECRET_INSTA_PASS_ID)
            GEMINI_API_KEY = get_secret(PROJECT_ID, SECRET_GEMINI_KEY_ID)

            base_dir = os.path.dirname(__file__)
            FONT_PATH = os.path.join(base_dir, FONT_FILE_NAME)
            TEMPLATE_IMAGE_PATH = os.path.join(base_dir, TEMPLATE_IMAGE_FILE_NAME)

            GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME")
            GCS_SESSION_BLOB_NAME = os.environ.get("GCS_SESSION_BLOB_NAME")

            if not all([PROJECT_ID, INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD, GEMINI_API_KEY, FONT_PATH, TEMPLATE_IMAGE_PATH, GCS_BUCKET_NAME, GCS_SESSION_BLOB_NAME]):
                missing = [
                    var_name for var_name, var_val in {
                        "PROJECT_ID": PROJECT_ID, "INSTAGRAM_USERNAME": INSTAGRAM_USERNAME,
                        "INSTAGRAM_PASSWORD": INSTAGRAM_PASSWORD, "GEMINI_API_KEY": GEMINI_API_KEY,
                        "FONT_PATH": FONT_PATH, "TEMPLATE_IMAGE_PATH": TEMPLATE_IMAGE_PATH,
                        "GCS_BUCKET_NAME": GCS_BUCKET_NAME, "GCS_SESSION_BLOB_NAME": GCS_SESSION_BLOB_NAME
                    }.items() if not var_val
                ]
                raise EnvironmentError(f"Una o più configurazioni globali mancano: {', '.join(missing)}")

            if not os.path.exists(FONT_PATH):
                raise FileNotFoundError(f"File font '{FONT_PATH}' non trovato nel pacchetto di deploy.")
            if not os.path.exists(TEMPLATE_IMAGE_PATH):
                print(f"Attenzione: File template '{TEMPLATE_IMAGE_PATH}' non trovato. Verrà usato sfondo di default.")


        except Exception as e:
            print(f"Errore CRITICO durante l'inizializzazione: {e}")
            # Rilancia l'eccezione per far fallire la funzione in modo controllato
            # Se l'inizializzazione fallisce, non ha senso continuare.
            raise RuntimeError(f"Inizializzazione fallita: {e}")


def get_secret(project_id, secret_id, version_id="latest"):
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_id}/versions/{version_id}"
    try:
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("UTF-8")
    except Exception as e:
        print(f"Errore nel recuperare il secret {secret_id}: {e}")
        raise # Rilancia per essere catturata da _initialize_globals

def download_from_gcs(bucket_name, source_blob_name, destination_file_name):
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(source_blob_name)
    try:
        if blob.exists(storage_client): # Passare il client per il test di esistenza
            blob.download_to_filename(destination_file_name)
            print(f"File {source_blob_name} scaricato da GCS in {destination_file_name}")
            return True
        else:
            print(f"File {source_blob_name} non trovato nel bucket {bucket_name}.")
            return False
    except Exception as e:
        print(f"Errore durante il download da GCS ({source_blob_name}): {e}")
        return False

def upload_to_gcs(bucket_name, source_file_name, destination_blob_name):
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(destination_blob_name)
    try:
        blob.upload_from_filename(source_file_name)
        print(f"File {source_file_name} caricato su GCS come {destination_blob_name}")
        return True
    except Exception as e:
        print(f"Errore durante l'upload su GCS ({source_file_name}): {e}")
        return False

def wrap_text_custom(text, max_line_length=30):
    if not text: return ""
    words = text.split(' ')
    wrapped_lines = []; current_line = ""
    for word in words:
        if len(word) > max_line_length:
            if current_line: wrapped_lines.append(current_line); current_line = ""
            wrapped_lines.append(word); continue
        if current_line:
            if len(current_line) + len(word) + 1 > max_line_length:
                wrapped_lines.append(current_line); current_line = word
            else: current_line += " " + word
        else: current_line = word
    if current_line: wrapped_lines.append(current_line)
    return "\n".join(wrapped_lines)

def get_historical_anecdote_gemini(day, month_name):
    if not GEMINI_API_KEY:
        print("Errore: La GEMINI_API_KEY non è stata inizializzata.")
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
        f"Massimo 2-3 frasi. Conciso per un post social. "
        f"LUNGHEZZA IMPORTANTE: tra 200 e 270 caratteri totali. "
        f"Non includere il prompt. Non iniziare con 'Certo, ecco...'.")
    print(f"Invio richiesta a Gemini per il {day} {month_name}...")
    try:
        response = model.generate_content(prompt, generation_config=GENERATION_CONFIG)
        if response.text:
            anecdote = response.text.strip()
            phrases_to_remove = ["certo, ecco un aneddoto:", "ecco un aneddoto:"]
            for phrase in phrases_to_remove:
                if anecdote.lower().startswith(phrase.lower()):
                    anecdote = anecdote[len(phrase):].strip()
                    if anecdote: anecdote = anecdote[0].upper() + anecdote[1:]
            return anecdote
        else:
            print(f"Risposta da Gemini vuota. Prompt Safety Feedback: {response.prompt_feedback if hasattr(response, 'prompt_feedback') else 'N/A'}")
            return None
    except Exception as e:
        print(f"Errore durante la chiamata API a Gemini: {e}")
        return None

def create_instagram_post_image(day_str, month_str_human, anecdote_text, output_filename_base, anecdote_font_size):
    global TEMPLATE_IMAGE_PATH, FONT_PATH # Usa le globali inizializzate

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

    try:
        font_title = ImageFont.truetype(FONT_PATH, TITLE_FONT_SIZE)
        font_anecdote = ImageFont.truetype(FONT_PATH, anecdote_font_size)
    except IOError:
        print(f"ERRORE CRITICO: Font '{FONT_PATH}' non caricabile anche se trovato. Verificare il file font.")
        # Non si può procedere senza font, usa un fallback o solleva eccezione
        # Per robustezza, si potrebbe tentare un font di sistema se questo fallisce, ma è meglio assicurarsi che il font custom sia valido.
        raise RuntimeError(f"Impossibile caricare il font principale: {FONT_PATH}")


    draw.text((MARGIN_X_TITLE, MARGIN_Y_TITLE), f"{day_str} {month_str_human.capitalize()}", font=font_title, fill=TEXT_COLOR)
    if anecdote_text:
        draw.multiline_text((MARGIN_X, MARGIN_Y_ANECDOTE), anecdote_text, font=font_anecdote, fill=TEXT_COLOR, align="left", spacing=LINE_SPACING_ANECDOTE)
    else:
        print("Nessun testo per l'aneddoto da disegnare.")

    # Salva l'immagine in /tmp
    output_full_path = os.path.join("/tmp", output_filename_base)
    try:
        img.save(output_full_path)
        print(f"Immagine salvata temporaneamente come: {output_full_path}")
        return output_full_path
    except Exception as e:
        print(f"Errore nel salvare l'immagine {output_full_path}: {e}")
        return None


def upload_to_instagram(image_path, caption_text):
    global INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD, GCS_BUCKET_NAME, GCS_SESSION_BLOB_NAME

    if not all([INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD, GCS_BUCKET_NAME, GCS_SESSION_BLOB_NAME]):
        print("Credenziali Instagram o configurazione GCS per la sessione mancanti. Impossibile caricare.")
        return False

    cl = Client()
    local_session_path = os.path.join("/tmp", GCS_SESSION_BLOB_NAME) # Percorso locale in /tmp

    # Tenta di scaricare e caricare la sessione da GCS
    if download_from_gcs(GCS_BUCKET_NAME, GCS_SESSION_BLOB_NAME, local_session_path):
        if os.path.exists(local_session_path) and os.path.getsize(local_session_path) > 0:
            try:
                cl.load_settings(local_session_path)
                print("Caricate impostazioni sessione da GCS (via /tmp).")
                cl.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD) # Prova a usare la sessione
                cl.get_timeline_feed() # Test per vedere se la sessione è valida
                print("Login con sessione esistente da GCS riuscito.")
            except Exception as e:
                print(f"Login con sessione da GCS fallito ({e}), tento login normale.")
                # Se fallisce, procedi con login normale
                try:
                    cl.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)
                    cl.dump_settings(local_session_path) # Salva nuova sessione in /tmp
                    upload_to_gcs(GCS_BUCKET_NAME, local_session_path, GCS_SESSION_BLOB_NAME) # E caricala su GCS
                    print("Login normale riuscito, sessione salvata su GCS.")
                except Exception as login_exc:
                    print(f"Errore durante il login normale su Instagram: {login_exc}")
                    return False
        else: # File scaricato ma vuoto o non esistente dopo download (improbabile se download_from_gcs è True)
            print("File di sessione da GCS non valido, tento login normale.")
            try:
                cl.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)
                cl.dump_settings(local_session_path)
                upload_to_gcs(GCS_BUCKET_NAME, local_session_path, GCS_SESSION_BLOB_NAME)
                print("Login normale riuscito, sessione salvata su GCS.")
            except Exception as login_exc:
                print(f"Errore durante il login normale su Instagram: {login_exc}")
                return False
    else: # Nessuna sessione trovata su GCS, primo login o GCS non accessibile
        print("Nessuna sessione GCS trovata o errore download, tento login normale.")
        try:
            cl.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)
            cl.dump_settings(local_session_path) # Salva in /tmp
            upload_to_gcs(GCS_BUCKET_NAME, local_session_path, GCS_SESSION_BLOB_NAME) # Carica su GCS
            print("Login normale riuscito, sessione salvata su GCS.")
        except Exception as e:
            print(f"Errore durante il login su Instagram: {e}")
            return False

    try:
        print(f"Caricamento di '{image_path}' su Instagram...")
        media = cl.photo_upload(path=image_path, caption=caption_text)
        if media:
            print(f"Immagine caricata con successo! Media ID: {media.id}")
            return True
        else:
            print("Caricamento Instagram fallito, nessun oggetto media restituito.")
            return False
    except Exception as e:
        print(f"Errore durante il caricamento dell'immagine su Instagram: {e}")
        return False
    finally:
        # Pulisci il file di sessione locale da /tmp se esiste
        if os.path.exists(local_session_path):
            try:
                os.remove(local_session_path)
            except OSError:
                pass


# --- ENTRY POINT DELLA CLOUD FUNCTION (HTTP Trigger) ---
def daily_instagram_post_http(request):
    """
    Cloud Function triggerata via HTTP da Cloud Scheduler.
    Il parametro 'request' non viene usato attivamente qui.
    """
    try:
        _initialize_globals() # Assicurati che tutto sia configurato
    except Exception as init_exc:
        print(f"Fallimento inizializzazione critica: {init_exc}")
        # È importante restituire un errore HTTP se l'inizializzazione fallisce.
        # Flask-based functions framework on Cloud Functions handles Python exceptions
        # and converts them to 500 Internal Server Error automatically.
        # For more control, you can import Flask and return a Response object.
        # from flask import make_response
        # return make_response(str(init_exc), 500)
        raise # Lascia che il framework di Cloud Functions gestisca l'eccezione

    print("Inizio esecuzione script giornaliero Instagram...")

    now = datetime.datetime.now()
    current_day, current_month_numeric = now.day, now.month
    month_mapping_it = {
        1: "Gennaio", 2: "Febbraio", 3: "Marzo", 4: "Aprile", 5: "Maggio", 6: "Giugno",
        7: "Luglio", 8: "Agosto", 9: "Settembre", 10: "Ottobre", 11: "Novembre", 12: "Dicembre"
    }
    current_month_name_it = month_mapping_it.get(current_month_numeric, now.strftime("%B"))

    print(f"Oggi è il {current_day} {current_month_name_it}")

    anecdote_raw = get_historical_anecdote_gemini(current_day, current_month_name_it)
    anecdote_for_image = None
    anecdote_font_size = 65

    if anecdote_raw:
        print(f"Aneddoto grezzo da Gemini: '{anecdote_raw}' (Lunghezza: {len(anecdote_raw)})")
        if len(anecdote_raw) >= 400 :
            anecdote_font_size = 45; anecdote_for_image = wrap_text_custom(anecdote_raw, 50)
        elif 335 <= len(anecdote_raw) < 400:
            anecdote_font_size = 50; anecdote_for_image = wrap_text_custom(anecdote_raw, 48)
        elif 290 <= len(anecdote_raw) < 335:
            anecdote_font_size = 55; anecdote_for_image = wrap_text_custom(anecdote_raw, 45)
        elif 275 <= len(anecdote_raw) < 290:
            anecdote_font_size = 58; anecdote_for_image = wrap_text_custom(anecdote_raw, 40)
        elif 230 <= len(anecdote_raw) < 275: # Corretto il range precedente
            anecdote_font_size = 60; anecdote_for_image = wrap_text_custom(anecdote_raw, 40)
        else: # < 230
            anecdote_font_size = 65; anecdote_for_image = wrap_text_custom(anecdote_raw, 38)

        print(f"Aneddoto wrappato per immagine:\n{anecdote_for_image}")

        output_fn_base = OUTPUT_IMAGE_NAME_TEMPLATE.format(day=current_day, month=current_month_numeric)
        generated_image_path = create_instagram_post_image(
            str(current_day), current_month_name_it, anecdote_for_image,
            output_fn_base, anecdote_font_size
        )

        if generated_image_path:
            # Usa l'aneddoto wrappato anche per la caption, o l'originale se preferisci
            instagram_caption = f"✨ Aneddoto del {current_day} {current_month_name_it} ✨\n\n{anecdote_for_image}\n\n"
            instagram_caption += "#aneddoto #storia #storiadelgiorno #accaddeoggi #curiosità"
            instagram_caption += f" #{current_month_name_it.lower().replace('à', 'a')} #{current_day}{current_month_name_it.lower().replace('à', 'a')}"
            print(f"Caption per Instagram:\n{instagram_caption}")

            upload_success = upload_to_instagram(generated_image_path, instagram_caption)

            # Pulisci l'immagine generata da /tmp
            try:
                os.remove(generated_image_path)
                print(f"Immagine temporanea {generated_image_path} rimossa.")
            except OSError as e:
                print(f"Errore rimozione immagine temporanea {generated_image_path}: {e}")

            if upload_success:
                print("Post pubblicato con successo su Instagram.")
                return "Operazione completata con successo.", 200
            else:
                print("Impossibile pubblicare il post su Instagram.")
                # Potrebbe essere un errore 500 se l'upload è critico
                return "Fallimento caricamento Instagram.", 503 # Service Unavailable o altro errore server
        else:
            print("Errore durante la creazione dell'immagine.")
            return "Fallimento creazione immagine.", 500
    else:
        print("Nessun aneddoto recuperato o generato.")
        return "Nessun aneddoto disponibile.", 200 # O 404 Not Found se lo consideri tale

    # Ritorno di fallback se nessun percorso precedente è stato preso
    return "Esecuzione terminata con stato sconosciuto.", 500
import google.generativeai as genai
import datetime
import os
from PIL import Image, ImageDraw, ImageFont
from instagrapi import Client
from instagrapi.exceptions import LoginRequired # Importa eccezione specifica

from google.cloud import secretmanager
from google.cloud import storage
import google.auth
import logging # Importa il modulo logging

# --- Configurazione del Logging ---
# Configura il logger per stampare su console (che Cloud Functions cattura)
# In un ambiente di produzione Cloud Function, i print() e logging.info() ecc.
# vengono automaticamente inviati a Cloud Logging.
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s')
logger = logging.getLogger(__name__)


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
BACKGROUND_COLOR = (255, 255, 255) # Default se template non trovato
OUTPUT_IMAGE_NAME_TEMPLATE = "aneddoto_del_giorno_{day}_{month}.png"
TITLE_FONT_SIZE = 120
LINE_SPACING_ANECDOTE = 15 # Spaziatura aggiuntiva tra le righe di testo dell'aneddoto
MARGIN_X = 80 # Margine sinistro e destro per il blocco di testo dell'aneddoto
MARGIN_X_TITLE = 150 # Margine sinistro per il titolo (se non centrato)
MARGIN_Y_TITLE = int(IMAGE_HEIGHT * 0.12) # Posizione Y del titolo
MARGIN_Y_ANECDOTE = int(IMAGE_HEIGHT * 0.40) # Posizione Y di inizio dell'aneddoto

# Variabili globali per credenziali e percorsi, inizializzate in seguito
PROJECT_ID = None
INSTAGRAM_USERNAME = None
INSTAGRAM_PASSWORD = None
GEMINI_API_KEY = None
FONT_PATH = None
TEMPLATE_IMAGE_PATH = None
GCS_BUCKET_NAME = None
GCS_SESSION_BLOB_NAME = None
_globals_initialized = False # Flag per evitare reinizializzazioni

# --- FUNZIONI HELPER ---
def _initialize_globals():
    global PROJECT_ID, INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD, GEMINI_API_KEY
    global FONT_PATH, TEMPLATE_IMAGE_PATH, GCS_BUCKET_NAME, GCS_SESSION_BLOB_NAME, _globals_initialized

    if _globals_initialized:
        logger.info("Le variabili globali sono già inizializzate.")
        return

    logger.info("Inizio inizializzazione variabili globali...")
    try:
        _, project_id_from_auth = google.auth.default()
        PROJECT_ID = project_id_from_auth
        logger.info(f"Project ID recuperato: {PROJECT_ID}")

        INSTAGRAM_USERNAME = get_secret(PROJECT_ID, SECRET_INSTA_USER_ID)
        INSTAGRAM_PASSWORD = get_secret(PROJECT_ID, SECRET_INSTA_PASS_ID)
        GEMINI_API_KEY = get_secret(PROJECT_ID, SECRET_GEMINI_KEY_ID)
        logger.info("Secret recuperati con successo.")

        base_dir = os.path.dirname(os.path.abspath(__file__)) # Più robusto per trovare la directory dello script
        FONT_PATH = os.path.join(base_dir, FONT_FILE_NAME)
        TEMPLATE_IMAGE_PATH = os.path.join(base_dir, TEMPLATE_IMAGE_FILE_NAME)
        logger.info(f"Percorso font impostato: {FONT_PATH}")
        logger.info(f"Percorso template impostato: {TEMPLATE_IMAGE_PATH}")

        GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME")
        GCS_SESSION_BLOB_NAME = os.environ.get("GCS_SESSION_BLOB_NAME")
        logger.info(f"GCS_BUCKET_NAME da env: {GCS_BUCKET_NAME}")
        logger.info(f"GCS_SESSION_BLOB_NAME da env: {GCS_SESSION_BLOB_NAME}")

        # Validazione più granulare
        required_globals = {
            "PROJECT_ID": PROJECT_ID, "INSTAGRAM_USERNAME": INSTAGRAM_USERNAME,
            "INSTAGRAM_PASSWORD": INSTAGRAM_PASSWORD, "GEMINI_API_KEY": GEMINI_API_KEY,
            "FONT_PATH": FONT_PATH, "TEMPLATE_IMAGE_PATH": TEMPLATE_IMAGE_PATH,
            "GCS_BUCKET_NAME": GCS_BUCKET_NAME, "GCS_SESSION_BLOB_NAME": GCS_SESSION_BLOB_NAME
        }
        missing = [name for name, value in required_globals.items() if not value]
        if missing:
            raise EnvironmentError(f"Configurazioni globali mancanti: {', '.join(missing)}")

        if not os.path.exists(FONT_PATH):
            # Questo dovrebbe far fallire la funzione, è critico
            raise FileNotFoundError(f"CRITICO: File font '{FONT_PATH}' non trovato nel pacchetto di deploy.")
        if not os.path.exists(TEMPLATE_IMAGE_PATH):
            logger.warning(f"File template '{TEMPLATE_IMAGE_PATH}' non trovato. Verrà usato sfondo di default.")
        
        _globals_initialized = True
        logger.info("Inizializzazione variabili globali completata.")

    except Exception as e:
        logger.critical(f"Errore CRITICO durante l'inizializzazione: {e}", exc_info=True)
        raise RuntimeError(f"Inizializzazione fallita: {e}")


def get_secret(project_id, secret_id, version_id="latest"):
    logger.info(f"Recupero secret '{secret_id}' dal progetto '{project_id}'...")
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_id}/versions/{version_id}"
    try:
        response = client.access_secret_version(request={"name": name})
        secret_value = response.payload.data.decode("UTF-8")
        logger.info(f"Secret '{secret_id}' recuperato con successo.")
        return secret_value
    except Exception as e:
        logger.error(f"Errore nel recuperare il secret {secret_id}: {e}", exc_info=True)
        raise

def download_from_gcs(bucket_name, source_blob_name, destination_file_name):
    logger.info(f"Tentativo di download di '{source_blob_name}' da bucket '{bucket_name}' a '{destination_file_name}'...")
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(source_blob_name)
    try:
        if blob.exists(storage_client):
            blob.download_to_filename(destination_file_name)
            logger.info(f"File {source_blob_name} scaricato con successo in {destination_file_name}")
            return True
        else:
            logger.warning(f"File {source_blob_name} non trovato nel bucket {bucket_name}.")
            return False
    except Exception as e:
        logger.error(f"Errore durante il download da GCS ({source_blob_name}): {e}", exc_info=True)
        return False

def upload_to_gcs(bucket_name, source_file_name, destination_blob_name):
    logger.info(f"Tentativo di upload di '{source_file_name}' a bucket '{bucket_name}' come '{destination_blob_name}'...")
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(destination_blob_name)
    try:
        blob.upload_from_filename(source_file_name)
        logger.info(f"File {source_file_name} caricato con successo su GCS come {destination_blob_name}")
        return True
    except Exception as e:
        logger.error(f"Errore durante l'upload su GCS ({source_file_name}): {e}", exc_info=True)
        return False

def wrap_text_custom(text, max_line_length=30):
    # ... (funzione di wrapping, puoi aggiungere log se necessario per debug del wrapping) ...
    if not text: return ""
    words = text.split(' ')
    wrapped_lines = []; current_line = ""
    for word in words:
        # Gestione parole più lunghe della lunghezza massima della riga
        if len(word) > max_line_length:
            if current_line: # Se c'è qualcosa sulla riga corrente, salvala prima
                wrapped_lines.append(current_line)
                current_line = ""
            # Aggiungi la parola lunga (potrebbe essere spezzata ulteriormente se necessario,
            # ma textwrap.wrap di Pillow lo fa meglio se integrato nel disegno)
            # Per semplicità qui la aggiungiamo intera, Pillow la gestirà
            wrapped_lines.append(word)
            continue

        if current_line: # Se la riga corrente non è vuota
            if len(current_line) + len(word) + 1 <= max_line_length: # +1 per lo spazio
                current_line += " " + word
            else: # La parola non ci sta, salva la riga corrente e inizia una nuova
                wrapped_lines.append(current_line)
                current_line = word
        else: # La riga corrente è vuota, inizia con questa parola
            current_line = word
    
    if current_line: # Aggiungi l'ultima riga se non è vuota
        wrapped_lines.append(current_line)
    
    return "\n".join(wrapped_lines)


def get_historical_anecdote_gemini(day, month_name):
    logger.info(f"Recupero aneddoto da Gemini per {day} {month_name}...")
    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY non inizializzata.")
        return None
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(GEMINI_MODEL_NAME)
        logger.info(f"Modello Gemini '{GEMINI_MODEL_NAME}' configurato.")
    except Exception as e:
        logger.error(f"Errore durante la configurazione del modello Gemini: {e}", exc_info=True)
        return None

    prompt = (
        f"Raccontami in italiano un brevissimo e interessante aneddoto o evento storico accaduto "
        f"il {day} {month_name}. "
        f"Massimo 2-3 frasi. Conciso per un post social. "
        f"LUNGHEZZA IMPORTANTE: tra 200 e 270 caratteri totali. " # Modificato per essere più realistico
        f"Non includere il prompt nella risposta. Non iniziare con 'Certo, ecco...'. Rispondi direttamente con l'aneddoto.")
    logger.info(f"Invio richiesta a Gemini con prompt: '{prompt[:100]}...' (prime 100 chars)") # Logga solo parte del prompt
    
    try:
        response = model.generate_content(prompt, generation_config=GENERATION_CONFIG)
        if response.text:
            anecdote = response.text.strip()
            logger.info(f"Aneddoto grezzo da Gemini: '{anecdote}'")
            # Rimozione frasi iniziali comuni
            phrases_to_remove = [
                "certo, ecco un aneddoto:", "ecco un aneddoto:", "certo, ecco un breve aneddoto:",
                "certo:", "ecco un evento storico:", "un breve aneddoto:", "un interessante aneddoto:"
            ]
            original_anecdote_lower = anecdote.lower()
            for phrase in phrases_to_remove:
                if original_anecdote_lower.startswith(phrase.lower()):
                    anecdote = anecdote[len(phrase):].strip()
                    logger.info(f"Rimosso '{phrase}' dall'aneddoto. Risultato: '{anecdote}'")
                    # Capitalizza la prima lettera se l'aneddoto non è vuoto
                    if anecdote:
                        anecdote = anecdote[0].upper() + anecdote[1:]
                    break # Rimuovi solo la prima occorrenza trovata
            logger.info(f"Aneddoto finale da Gemini: '{anecdote}'")
            return anecdote
        else:
            logger.warning(f"Risposta da Gemini vuota. Candidates: {response.candidates if hasattr(response, 'candidates') else 'N/A'}. Prompt Feedback: {response.prompt_feedback if hasattr(response, 'prompt_feedback') else 'N/A'}")
            return None
    except Exception as e:
        logger.error(f"Errore durante la chiamata API a Gemini: {e}", exc_info=True)
        return None

def create_instagram_post_image(day_str, month_str_human, anecdote_text, output_filename_base, anecdote_font_size):
    logger.info(f"Inizio creazione immagine '{output_filename_base}' con font size {anecdote_font_size}...")
    global TEMPLATE_IMAGE_PATH, FONT_PATH

    if not os.path.exists(TEMPLATE_IMAGE_PATH):
        logger.warning(f"Template '{TEMPLATE_IMAGE_PATH}' non trovato. Uso sfondo di default color {BACKGROUND_COLOR}.")
        img = Image.new('RGB', (IMAGE_WIDTH, IMAGE_HEIGHT), color=BACKGROUND_COLOR)
    else:
        try:
            img = Image.open(TEMPLATE_IMAGE_PATH).convert("RGB").resize((IMAGE_WIDTH, IMAGE_HEIGHT))
            logger.info(f"Template '{TEMPLATE_IMAGE_PATH}' caricato e ridimensionato.")
        except Exception as e:
            logger.error(f"Errore apertura template '{TEMPLATE_IMAGE_PATH}': {e}. Uso sfondo di default.", exc_info=True)
            img = Image.new('RGB', (IMAGE_WIDTH, IMAGE_HEIGHT), color=BACKGROUND_COLOR)
    draw = ImageDraw.Draw(img)

    try:
        logger.info(f"Caricamento font: '{FONT_PATH}'")
        font_title = ImageFont.truetype(FONT_PATH, TITLE_FONT_SIZE)
        font_anecdote = ImageFont.truetype(FONT_PATH, anecdote_font_size)
        logger.info("Font caricati con successo.")
    except IOError as ioe:
        logger.critical(f"ERRORE CRITICO: Font '{FONT_PATH}' non caricabile. IOError: {ioe}", exc_info=True)
        raise RuntimeError(f"Impossibile caricare il font principale: {FONT_PATH} ({ioe})")
    except Exception as e:
        logger.critical(f"ERRORE CRITICO: Eccezione generica durante caricamento font '{FONT_PATH}': {e}", exc_info=True)
        raise RuntimeError(f"Impossibile caricare il font principale: {FONT_PATH} ({e})")


    # Titolo (Data)
    title_text = f"{day_str} {month_str_human.capitalize()}"
    # Usiamo textbbox per un calcolo più accurato della larghezza
    try:
        title_bbox = draw.textbbox((0, 0), title_text, font=font_title)
        title_width = title_bbox[2] - title_bbox[0]
        title_height = title_bbox[3] - title_bbox[1]
        title_x = (IMAGE_WIDTH - title_width) / 2
        current_y_title = MARGIN_Y_TITLE
        draw.text((title_x, current_y_title), title_text, font=font_title, fill=TEXT_COLOR)
        logger.info(f"Titolo '{title_text}' disegnato a ({title_x}, {current_y_title}).")
    except Exception as e:
        logger.error(f"Errore nel disegnare il titolo: {e}", exc_info=True)


    # Aneddoto
    current_y_anecdote = MARGIN_Y_ANECDOTE # O basalo sull'altezza del titolo: current_y_title + title_height + 30 (spazio)
    
    if anecdote_text:
        # textwrap.wrap è più robusto per andare a capo
        # Calcola la larghezza massima del testo in pixel
        max_pixel_width_for_anecdote = IMAGE_WIDTH - (2 * MARGIN_X)
        # Stima i caratteri per riga (questo è molto approssimativo, dipende dal font)
        # avg_char_width = font_anecdote.getlength("a") if hasattr(font_anecdote, 'getlength') else 20 # Fallback
        # chars_per_line = int(max_pixel_width_for_anecdote / avg_char_width) if avg_char_width > 0 else 30
        # logger.info(f"Aneddoto: max_pixel_width={max_pixel_width_for_anecdote}, avg_char_width={avg_char_width}, chars_per_line={chars_per_line}")
        # Usa un numero di caratteri fisso per textwrap che sai funziona bene con il tuo font/dimensione
        # Questo è più affidabile che calcolarlo dinamicamente in modo semplice.
        # Dovrai sperimentare con questo valore.
        anecdote_lines = textwrap.wrap(anecdote_text, width=35, break_long_words=True, replace_whitespace=False)

        line_height_bbox = font_anecdote.getbbox("Ajp")
        line_height_actual = line_height_bbox[3] - line_height_bbox[1] + LINE_SPACING_ANECDOTE

        for line in anecdote_lines:
            if not line.strip(): continue
            # Centra ogni riga
            line_bbox = draw.textbbox((0,0), line, font=font_anecdote)
            line_actual_width = line_bbox[2] - line_bbox[0]
            line_x = (IMAGE_WIDTH - line_actual_width) / 2
            # line_x = MARGIN_X # Se vuoi allineato a sinistra con margine
            
            try:
                draw.text((line_x, current_y_anecdote), line, font=font_anecdote, fill=TEXT_COLOR, align="center") # o "left"
                logger.debug(f"Disegnata riga aneddoto: '{line}' a y={current_y_anecdote}")
            except Exception as e:
                logger.error(f"Errore nel disegnare la riga dell'aneddoto '{line}': {e}", exc_info=True)
            current_y_anecdote += line_height_actual
    else:
        logger.warning("Nessun testo per l'aneddoto da disegnare.")

    output_full_path = os.path.join("/tmp", output_filename_base)
    try:
        img.save(output_full_path)
        logger.info(f"Immagine salvata temporaneamente come: {output_full_path}")
        return output_full_path
    except Exception as e:
        logger.error(f"Errore nel salvare l'immagine {output_full_path}: {e}", exc_info=True)
        return None

def upload_to_instagram(image_path, caption_text):
    logger.info("Inizio upload su Instagram...")
    global INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD, GCS_BUCKET_NAME, GCS_SESSION_BLOB_NAME

    if not all([INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD, GCS_BUCKET_NAME, GCS_SESSION_BLOB_NAME]):
        logger.error("Credenziali Instagram o configurazione GCS per la sessione mancanti. Impossibile caricare.")
        return False

    cl = Client()
    local_session_path = os.path.join("/tmp", GCS_SESSION_BLOB_NAME)
    logger.info(f"Percorso sessione locale: {local_session_path}")

    session_loaded_from_gcs = False
    if download_from_gcs(GCS_BUCKET_NAME, GCS_SESSION_BLOB_NAME, local_session_path):
        if os.path.exists(local_session_path) and os.path.getsize(local_session_path) > 0:
            try:
                logger.info("Tentativo di caricare le impostazioni della sessione da file locale (scaricato da GCS)...")
                cl.load_settings(local_session_path)
                logger.info("Impostazioni sessione caricate. Tento login con sessione...")
                # cl.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD) # Questo potrebbe forzare un nuovo login
                # Invece, prova a fare un'operazione che richieda login per testare la sessione
                cl.get_timeline_feed() 
                logger.info("Login con sessione esistente da GCS riuscito (testato con get_timeline_feed).")
                session_loaded_from_gcs = True
            except LoginRequired:
                 logger.warning("LoginRequired: La sessione da GCS non è valida o è scaduta. Tento login normale.")
                 session_loaded_from_gcs = False # Forza nuovo login
            except Exception as e:
                logger.warning(f"Login con sessione da GCS fallito ({e}), tento login normale.", exc_info=True)
                session_loaded_from_gcs = False # Forza nuovo login
        else:
            logger.warning("File di sessione da GCS scaricato ma vuoto o non esistente, tento login normale.")
    else:
        logger.info("Nessuna sessione GCS trovata o errore download, tento login normale.")

    if not session_loaded_from_gcs:
        try:
            logger.info("Esecuzione login normale su Instagram...")
            cl.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)
            logger.info("Login normale riuscito. Salvataggio sessione...")
            cl.dump_settings(local_session_path)
            if upload_to_gcs(GCS_BUCKET_NAME, local_session_path, GCS_SESSION_BLOB_NAME):
                logger.info("Nuova sessione salvata su GCS.")
            else:
                logger.warning("Fallimento salvataggio nuova sessione su GCS.")
        except Exception as login_exc:
            logger.error(f"Errore CRITICO durante il login normale su Instagram: {login_exc}", exc_info=True)
            return False

    try:
        logger.info(f"Caricamento di '{image_path}' su Instagram con caption: '{caption_text[:50]}...'")
        media = cl.photo_upload(path=image_path, caption=caption_text)
        if media and hasattr(media, 'id'):
            logger.info(f"Immagine caricata con successo su Instagram! Media ID: {media.id}")
            return True
        else:
            logger.error(f"Caricamento Instagram fallito, nessun oggetto media valido restituito. Media: {media}")
            return False
    except Exception as e:
        logger.error(f"Errore durante il caricamento dell'immagine su Instagram: {e}", exc_info=True)
        return False
    finally:
        if os.path.exists(local_session_path):
            try:
                os.remove(local_session_path)
                logger.debug(f"File sessione locale {local_session_path} rimosso.")
            except OSError as e:
                logger.warning(f"Errore rimozione file sessione locale {local_session_path}: {e}")


# --- ENTRY POINT DELLA CLOUD FUNCTION (HTTP Trigger) ---
def daily_instagram_post_http(request):
    # Il parametro 'request' non viene usato attivamente qui per un trigger da Scheduler.
    # Potrebbe contenere dati se il trigger Pub/Sub invia un payload.
    # Per semplicità, lo ignoriamo se non necessario.
    
    # Assicurati che le globali siano inizializzate ad ogni invocazione
    # (Cloud Functions può riutilizzare istanze, ma è bene essere sicuri)
    global _globals_initialized
    _globals_initialized = False # Forza reinizializzazione per ogni chiamata (o gestisci diversamente se preferisci)

    try:
        _initialize_globals()
    except Exception as init_exc:
        logger.critical(f"Fallimento inizializzazione critica nella Cloud Function: {init_exc}", exc_info=True)
        # Restituisci un errore HTTP appropriato
        # Il framework di Cloud Functions di solito converte le eccezioni non gestite in 500.
        # Per un controllo più fine, potresti importare `flask` e ritornare `make_response`.
        # from flask import make_response
        # return make_response(f"Initialization failed: {init_exc}", 500)
        raise # Lascia che il framework gestisca l'eccezione e la logghi

    logger.info("Inizio esecuzione script giornaliero Instagram da Cloud Function...")

    now = datetime.datetime.now() # Per Cloud Functions, considerare l'uso di timezone UTC
                                  # now = datetime.datetime.now(datetime.timezone.utc)
                                  # e poi convertire a fuso orario italiano se necessario per la data dell'aneddoto
    current_day, current_month_numeric = now.day, now.month
    month_mapping_it = {
        1: "Gennaio", 2: "Febbraio", 3: "Marzo", 4: "Aprile", 5: "Maggio", 6: "Giugno",
        7: "Luglio", 8: "Agosto", 9: "Settembre", 10: "Ottobre", 11: "Novembre", 12: "Dicembre"
    }
    current_month_name_it = month_mapping_it.get(current_month_numeric, now.strftime("%B"))

    logger.info(f"Data per l'aneddoto: {current_day} {current_month_name_it}")

    anecdote_raw = get_historical_anecdote_gemini(current_day, current_month_name_it)
    anecdote_for_image = None
    anecdote_font_size = 65 # Default

    if anecdote_raw:
        logger.info(f"Aneddoto grezzo da Gemini: '{anecdote_raw}' (Lunghezza: {len(anecdote_raw)})")
        
        # Logica per adattare la dimensione del font e il wrapping
        raw_len = len(anecdote_raw)
        if raw_len >= 400 :
            anecdote_font_size = 45; anecdote_for_image = wrap_text_custom(anecdote_raw, 50)
        elif 335 <= raw_len < 400:
            anecdote_font_size = 50; anecdote_for_image = wrap_text_custom(anecdote_raw, 48)
        elif 290 <= raw_len < 335:
            anecdote_font_size = 55; anecdote_for_image = wrap_text_custom(anecdote_raw, 45)
        elif 275 <= raw_len < 290: # Era un errore di sovrapposizione qui
            anecdote_font_size = 58; anecdote_for_image = wrap_text_custom(anecdote_raw, 40)
        elif 230 <= raw_len < 275:
            anecdote_font_size = 60; anecdote_for_image = wrap_text_custom(anecdote_raw, 40)
        else: # < 230
            anecdote_font_size = 65; anecdote_for_image = wrap_text_custom(anecdote_raw, 38)
        logger.info(f"Aneddoto wrappato per immagine (font size {anecdote_font_size}):\n{anecdote_for_image}")

        output_fn_base = OUTPUT_IMAGE_NAME_TEMPLATE.format(day=current_day, month=current_month_numeric)
        generated_image_path = create_instagram_post_image(
            str(current_day), current_month_name_it, anecdote_for_image,
            output_fn_base, anecdote_font_size
        )

        if generated_image_path and os.path.exists(generated_image_path):
            # Costruzione Caption
            instagram_caption = f"✨ Aneddoto del {current_day} {current_month_name_it} ✨\n\n{anecdote_raw}\n\n" # Uso raw per caption
            instagram_caption += "#aneddoto #storia #storiadelgiorno #accaddeoggi #curiosità #storiaitaliana #storiaromana #storiacontemporanea" # Aggiunti più hashtag
            instagram_caption += f" #{current_month_name_it.lower().replace('à', 'a')} #{current_day}{current_month_name_it.lower().replace('à', 'a').replace(' ','')}"
            logger.info(f"Caption per Instagram (prime 100 chars): {instagram_caption[:100]}...")

            upload_success = upload_to_instagram(generated_image_path, instagram_caption)

            try:
                os.remove(generated_image_path)
                logger.info(f"Immagine temporanea {generated_image_path} rimossa.")
            except OSError as e:
                logger.warning(f"Errore rimozione immagine temporanea {generated_image_path}: {e}")

            if upload_success:
                logger.info("Post pubblicato con successo su Instagram.")
                return "Operazione completata con successo.", 200
            else:
                logger.error("Impossibile pubblicare il post su Instagram.")
                return "Fallimento caricamento Instagram.", 503
        else:
            logger.error("Errore durante la creazione dell'immagine, percorso non valido o file non esistente.")
            return "Fallimento creazione immagine.", 500
    else:
        logger.warning("Nessun aneddoto recuperato o generato da Gemini.")
        return "Nessun aneddoto disponibile.", 200 # O 404 se preferisci

    logger.error("Esecuzione terminata con stato sconosciuto (fallback).")
    return "Esecuzione terminata con stato sconosciuto.", 500

# Per testare localmente la funzione principale (simulando l'invocazione)
# Questo blocco non verrà eseguito quando deployato su Cloud Functions
# a meno che tu non lo chiami specificamente.
if __name__ == "__main__":
    logger.info("Esecuzione script in modalità test locale (__main__)...")
    # Per il test locale, potresti voler caricare le variabili d'ambiente da un file .env
    # Esempio con python-dotenv:
    # from dotenv import load_dotenv
    # load_dotenv()
    # GEMINI_API_KEY_LOCAL_TEST = os.environ.get("GEMINI_API_KEY")
    # GCS_BUCKET_NAME_LOCAL_TEST = os.environ.get("GCS_BUCKET_NAME")
    # GCS_SESSION_BLOB_NAME_LOCAL_TEST = os.environ.get("GCS_SESSION_BLOB_NAME")
    # if not all([GEMINI_API_KEY_LOCAL_TEST, GCS_BUCKET_NAME_LOCAL_TEST, GCS_SESSION_BLOB_NAME_LOCAL_TEST]):
    #     print("PER TEST LOCALE: Assicurati che le variabili d'ambiente GEMINI_API_KEY, GCS_BUCKET_NAME, GCS_SESSION_BLOB_NAME siano impostate.")
    #     exit()

    # Crea le cartelle se non esistono per il test locale
    script_dir = os.path.dirname(os.path.abspath(__file__))
    fonts_dir = os.path.join(script_dir, "fonts") # Assumendo che FONT_FILE_NAME sia solo il nome
    templates_dir = os.path.join(script_dir, "templates") # Assumendo che TEMPLATE_IMAGE_FILE_NAME sia solo il nome

    if not os.path.exists(fonts_dir): os.makedirs(fonts_dir)
    if not os.path.exists(templates_dir): os.makedirs(templates_dir)
    
    # Controlla se il font e il template esistono nei percorsi attesi
    # (FONT_PATH e TEMPLATE_IMAGE_PATH sono definiti globalmente ma usano os.path.dirname(__file__) solo quando _initialize_globals viene chiamato)
    # Per il test locale, potremmo ridefinirli o assicurarci che le globali siano inizializzate prima.
    # La cosa più semplice è far chiamare _initialize_globals anche da __main__
    
    print("Esecuzione test locale della funzione daily_instagram_post_http...")
    try:
        # Per il test locale, potremmo non avere un "request" object reale.
        # La funzione `daily_instagram_post_http` non lo usa attivamente.
        response_text, status_code = daily_instagram_post_http(request=None)
        print(f"Test locale completato. Risposta: '{response_text}', Status: {status_code}")
    except RuntimeError as e:
        # Questo catturerà l'errore dall'inizializzazione se fallisce
        print(f"Test locale fallito a causa di un errore di inizializzazione: {e}")
    except Exception as e:
        print(f"Test locale fallito con un'eccezione imprevista: {e}", exc_info=True)
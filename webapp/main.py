import os
import shutil
import tempfile
import subprocess
import base64 # Per inviare l'audio come data URL
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel # Per definire modelli di richiesta/risposta
import ollama
import whisper # Libreria Whisper
from dotenv import load_dotenv
import scipy.io.wavfile as wavf # Per salvare correttamente i file .wav
import numpy as np
import ssl

load_dotenv()

app = FastAPI()

# Monta la directory 'static' per CSS e JS
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- Configurazione ---
OLLAMA_HOST = os.getenv("OLLAMA_BASE_URL", "http://ollama_service:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL_NAME", "mistral:7b-instruct-q4_K_M")
PIPER_EXECUTABLE = "/usr/local/bin/piper" # Assicurati sia nel Dockerfile e nel PATH
PIPER_VOICE_MODEL = os.getenv("PIPER_VOICE_MODEL_PATH_DEFAULT") # Caricato da .env
WHISPER_MODEL_NAME = "medium"

# --- Caricamento Modelli (all'avvio dell'app) ---
print("Caricamento modello Whisper...")
try:
    whisper_model = whisper.load_model(WHISPER_MODEL_NAME)
    print(f"Modello Whisper '{WHISPER_MODEL_NAME}' caricato.")
except Exception as e:
    print(f"ERRORE CRITICO: Impossibile caricare il modello Whisper: {e}")
    whisper_model = None # L'app non funzionerà correttamente

if not PIPER_VOICE_MODEL or not os.path.exists(PIPER_VOICE_MODEL):
    print(f"ATTENZIONE: Modello vocale Piper non trovato o non specificato: {PIPER_VOICE_MODEL}")
    print("La sintesi vocale potrebbe non funzionare. Controlla PIPER_VOICE_MODEL_PATH_DEFAULT in .env e il Dockerfile.")


# --- Modelli Pydantic per la risposta JSON ---
class ProcessedResponse(BaseModel):
    user_text: str
    llm_response_text: str
    audio_response_data_url: str | None # Audio come stringa base64 data URL


@app.get("/", response_class=HTMLResponse)
async def get_index_page():
    try:
        with open("templates/index.html", "r", encoding="utf-8") as f:
            html_content = f.read()
        return HTMLResponse(content=html_content)
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="index.html non trovato. Controlla la struttura del progetto.")


@app.post("/process-audio/", response_model=ProcessedResponse)
async def handle_audio_processing(audio_file: UploadFile = File(...)):
    if not whisper_model:
        raise HTTPException(status_code=500, detail="Modello Whisper non caricato sul server.")
    if not PIPER_VOICE_MODEL or not os.path.exists(PIPER_VOICE_MODEL):
        raise HTTPException(status_code=500, detail=f"Modello vocale Piper non trovato: {PIPER_VOICE_MODEL}")

    # Usa directory temporanee gestite da Python
    with tempfile.TemporaryDirectory() as tmpdir:
        input_audio_path = os.path.join(tmpdir, "input_audio.wav") # Whisper preferisce estensioni note
        
        # 1. Salva l'audio uploadato
        try:
            with open(input_audio_path, "wb") as f_audio_in:
                shutil.copyfileobj(audio_file.file, f_audio_in)
            print(f"Audio salvato in: {input_audio_path}")
        except Exception as e:
            print(f"Errore salvataggio audio: {e}")
            raise HTTPException(status_code=500, detail="Errore nel salvataggio del file audio.")
        finally:
            await audio_file.close()

        # 2. Trascrivi con Whisper
        try:
            print(f"Trascrizione di: {input_audio_path}")
            transcription_result = whisper_model.transcribe(input_audio_path, fp16=False, language="it")
            user_text = transcription_result["text"].strip()
            if not user_text:
                user_text = "(Nessun input vocale rilevato)"
            print(f"Testo utente: {user_text}")
        except Exception as e:
            print(f"Errore trascrizione Whisper: {e}")
            raise HTTPException(status_code=500, detail=f"Errore durante la trascrizione: {e}")


        # 3. Ottieni risposta da Ollama
        llm_response_text = ""
        if user_text != "(Nessun input vocale rilevato)":
            try:
                print(f"Richiesta a Ollama per: '{user_text}' con modello '{OLLAMA_MODEL}'")
                ollama_client = ollama.Client(host=OLLAMA_HOST) # Assicurati che OLLAMA_HOST sia corretto
                ollama_response = ollama_client.chat(
                    model=OLLAMA_MODEL,
                    messages=[
                        {'role': 'system', 'content': 'Sei un assistente vocale utile e conciso. Rispondi in italiano.'},
                        {'role': 'user', 'content': user_text},
                    ]
                )
                llm_response_text = ollama_response['message']['content'].strip()
                print(f"Risposta LLM: {llm_response_text}")
            except Exception as e:
                print(f"Errore Ollama: {e}")
                llm_response_text = "Non sono riuscito a contattare il modello di linguaggio."
        else:
            llm_response_text = "Non ho sentito nulla, potresti ripetere?"


        # 4. Sintetizza risposta con Piper
        output_audio_path = os.path.join(tmpdir, "response.wav")
        audio_data_url = None
        try:
            print(f"Sintesi vocale per: '{llm_response_text}' con modello Piper '{PIPER_VOICE_MODEL}'")

            clean_llm_response_text = llm_response_text.replace('"', '\\"') # Semplice escaping per echo
            command_str = f'echo "{clean_llm_response_text}" | {PIPER_EXECUTABLE} --model "{PIPER_VOICE_MODEL}" --output_file "{output_audio_path}"'
            
            process = subprocess.Popen(command_str, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = process.communicate(timeout=15) # Timeout per evitare blocchi

            if process.returncode != 0:
                print(f"Errore Piper (codice {process.returncode}): {stderr.decode(errors='ignore')}")
                # Non sollevare eccezione qui, ma l'audio non sarà disponibile
                llm_response_text += " (Errore sintesi vocale)"
            elif not os.path.exists(output_audio_path) or os.path.getsize(output_audio_path) == 0:
                print(f"Errore Piper: file audio di output non creato o vuoto.")
                llm_response_text += " (Errore creazione file audio)"
            else:
                # Converti l'audio in Data URL per inviarlo direttamente nel JSON
                with open(output_audio_path, "rb") as f_audio_out:
                    audio_bytes = f_audio_out.read()
                    audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")
                    audio_data_url = f"data:audio/wav;base64,{audio_base64}"
                print("Sintesi vocale completata e audio convertito in data URL.")

        except subprocess.TimeoutExpired:
            print("Errore Piper: Timeout durante la sintesi vocale.")
            llm_response_text += " (Timeout sintesi vocale)"
        except Exception as e:
            print(f"Errore sintesi Piper: {e}")
            llm_response_text += " (Errore grave sintesi vocale)"
            # Non sollevare eccezione qui, ma l'audio non sarà disponibile

        return ProcessedResponse(
            user_text=user_text,
            llm_response_text=llm_response_text,
            audio_response_data_url=audio_data_url
        )

if __name__ == "__main__":
    import uvicorn
    ssl_key_path = "localhost+1-key.pem"
    ssl_cert_path = "localhost+1.pem"

    if not os.path.exists(ssl_key_path) or not os.path.exists(ssl_cert_path):
        print(f"ERRORE: Certificati SSL '{ssl_key_path}' o '{ssl_cert_path}' non trovati.")
        print("Assicurati di aver eseguito 'mkcert localhost 127.0.0.1' nella stessa directory.")
        print("Avvio il server senza HTTPS (getUserMedia non funzionerà).")
        uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
    else:
        print(f"Avvio server FastAPI su HTTPS: https://localhost:8000")
        print(f"Usando modello Piper: {PIPER_VOICE_MODEL}")
        print(f"Usando modello Ollama: {OLLAMA_MODEL} su host {OLLAMA_HOST}")
        uvicorn.run(
            "main:app",
            host="0.0.0.0",
            port=8000,
            reload=True,
            ssl_keyfile=ssl_key_path,      # Percorso al tuo file chiave SSL
            ssl_certfile=ssl_cert_path     # Percorso al tuo file certificato SSL
        )

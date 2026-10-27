# FreeTranscriptor

Transcripción de reuniones **100% local en Windows**, sin nube, sin API keys,
sin cuenta. Resumen con IA opcional (local o en la nube, según lo que tengas).

```
Live Captions (Win+Ctrl+L) ──► captions_watch.ps1 ──► transcriptor_core.py
        (texto del sistema)      (UI Automation, 350 ms)   │
                                                          ▼
                            servidor local (web_gui.py) ──► navegador (SSE)
                                                          │
                             transcript .md + TXT + resumen con IA ◄──────┘
```

- **Motor de transcripción:** Live Captions de Windows (el reconocimiento ya lo hace el sistema; la app lee ese texto y lo guarda con timestamps de hora real).
- **Motor de resumen:** detecta automáticamente el mejor disponible: Pi → Ollama (local) → API compatible OpenAI. Sin ninguno, la app funciona igual y los botones de resumen quedan deshabilitados.
- **Interfaz:** app web local (diseño oscuro, SSE en vivo, `127.0.0.1` únicamente).

## Requisitos

| Requisito | Detalle |
|---|---|
| Windows 11 | Live Captions ("Subtítulos en directo") es función del sistema |
| Python 3.12+ | [python.org](https://www.python.org/downloads/) marcando *"Add python.exe to PATH"*, o `winget install Python.Python.3.13` |
| Internet (solo la primera vez) | dependencias + modelo de Whisper (~100 MB) si vas a usar `transcribe.py`; después todo corre offline |

## Instalación

```powershell
git clone https://github.com/VictorQuispe0106/FreeTranscriptor.git
cd FreeTranscriptor
```

Doble clic en **`FreeTranscriptor.bat`**. En la primera corrida crea el entorno
`.venv`, instala `requirements.txt` (versiones pineadas y verificadas en
conjunto) y abre `http://127.0.0.1:8737` en el navegador. Las siguientes
corridas arrancan directo.

## Uso

| Control | Qué hace |
|---|---|
| **Transcribir** | abre Live Captions si falta y empieza a escribir el transcript en vivo |
| **Parar** | corta la grabación (Live Captions queda abierto para reutilizar) |
| **Abrir / Exportar TXT / Copiar** | el `.md` de la sesión, el texto plano con `[hh:mm:ss]`, o al portapapeles |
| **Resumir con IA** | resumen del transcript con el motor detectado (ver abajo) |
| **Resumir hasta ahora** | resumen parcial *sin cortar* la sesión en curso |
| **Auto** | al Parar, el resumen sale solo (preferencia persistente) |
| **Historial** | sesiones anteriores: Abrir, TXT, Resumir |
| **Filtro / Enter** | filtra la sesión viva; Enter busca en **todas** las sesiones |

Cada sesión guarda en `transcripts\<fecha>_live-captions.md`. Si la ventana de
Subtítulos se cierra en plena reunión, la app lo detecta y ofrece **reiniciar
en el mismo archivo** sin duplicar líneas (fija el PID de la instancia que abrió).

## Resumen con IA

Elige automáticamente el primer motor disponible (el chip de la barra muestra cuál):

| Motor | Requisito | Privacidad |
|---|---|---|
| **Pi** | CLI `pi` instalada y configurada | procesa con tu plan de Pi |
| **Ollama** | Ollama corriendo + el modelo instalado (default `llama3.1:8b`) | 100% local |
| **API** | endpoint compatible OpenAI + key (ej. OpenAI, OpenRouter, Gemini) | el transcript viaja al proveedor |

Configuración opcional en `transcriptor_config.json` (gitignoreado):

```json
{
  "summary_backend": "auto",
  "ollama_model": "llama3.1:8b",
  "cloud_base_url": "https://api.openai.com/v1",
  "cloud_model": "gpt-4o-mini",
  "cloud_api_key": "sk-..."
}
```

Sin motor configurado, los botones de resumen no se habilitan; la transcripción
no los necesita. El resumen se escribe en `transcripts\<fecha>.resumen.md` con
secciones fijas: Resumen, Puntos clave, Decisiones, Acciones, Pendientes, Citas.

## Transcribir un archivo grabado (opcional)

`transcribe.py` es el motor Whisper local para archivos sueltos (independiente de la app):

```powershell
py transcribe.py --file reunion.m4a --lang es
py transcribe.py --source loopback --lang es --model small   # en vivo por Whisper (alternativa)
py transcribe.py --list / --check                            # diagnosticar captura de audio
```

Acepta `wav`, `mp3`, `m4a`, `ogg`, `flac`, `mp4` (decodifica con PyAV, sin `ffmpeg`).

**Límites honestos del modo Live Captions:** sin separación de hablantes; la
calidad del texto es la de Windows (no reprocesa audio); la tanda de líneas que
ya estaba en pantalla al arrancar lleva timestamps aproximados.

## Desarrollo

```powershell
py -m unittest discover -s tests -p "test_*.py"        # suite hermética (sin audio, sin red)
GENTLE_E2E=1 py -m unittest tests.test_summary_render_e2e   # e2e con Playwright (msedge)
```

| Módulo | Rol |
|---|---|
| `transcriptor_core.py` | Watcher, Live Captions, historial, búsqueda, render de derivados |
| `web_gui.py` | server local + SSE + orquestación |
| `summarizer.py` | motores de resumen (Pi / Ollama / API) |
| `pi_bridge.py` | corrida headless de Pi (prompts y streaming) |
| `fluidez.py` | limpieza local del texto (sin IA) |
| `transcript_lines.py` | parse/render del formato de transcript |
| `captions_watch.ps1` | watcher de Live Captions vía UI Automation |

El diseño de referencia de la UI vive en `odd/design/index.stitch.html`.

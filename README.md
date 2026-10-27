# Transcriptor en vivo

Transcripción local de audio a texto: reuniones, llamadas y grabaciones.
Corre 100% en la máquina — sin nube, sin API keys, sin cuenta.

- **Motor:** [`faster-whisper`](https://github.com/SYSTRAN/faster-whisper) en CPU con `int8`
- **Modos:** audio del sistema (loopback), micrófono, o un archivo ya grabado
- **Salida:** Markdown con timestamps, escrito en vivo a medida que hablás

## Requisitos

Ya están instalados en esta máquina (Python 3.14):

| Paquete | Rol |
|---|---|
| `faster-whisper` + `ctranslate2` | reconocimiento de voz local |
| `scipy` | resampling 48 kHz → 16 kHz |
| `av` (PyAV) | decodificar mp3/m4a/ogg sin necesitar `ffmpeg` |
| `soundcard`, `pyaudiowpatch` | captura WASAPI (loopback y micrófono) |

Modelos ya descargados: `tiny`, `base`, `small`. No hace falta internet para transcribir.

## Formas de arrancar

| Archivo | Qué hace |
|---|---|
| `Transcriptor App.bat` | la app de escritorio (recomendado para el día a día) |
| `Transcriptor Web.bat` | la misma app en el navegador (`http://127.0.0.1:8737`) |
| `TRANSCRIBIR EN VIVO.bat` | modo crudo: corre solo el watcher contra Subtitulos en vivo, sin la app |

El diseño de referencia de la interfaz (export original de Stitch) vive en
`odd/design/index.stitch.html`.

## App (recomendado para usar todos los días)

Vos no abrís nada: la app hace todo sola, sin tocar **Win+Ctrl+L**.

Doble clic en **`Transcriptor App.bat`** (o `py transcriptor_app.py`).

1. **Transcribir** - abre Live Captions sola si no está corriendo, empieza a escribir
   el transcript y te muestra las líneas en la ventana.
2. **Parar** - corta la grabación; Live Captions queda abierto para la próxima.
3. **Abrir transcript** - abre el `.md` del que se escribió en esa sesión.
4. **Exportar TXT** - escribe el texto plano con timestamps (`[hh:mm:ss] frase`) y lo abre.
5. **Resumir con Pi** - genera el resumen del transcript con IA (ver abajo).
6. **Cancelar resumen** - corta el resumen en curso.
7. **Resumir hasta ahora** - resume lo dicho hasta ese momento *mientras sigue
   transcribiendo*: no corta la sesión ni toca los botones de transcripción.
8. **Copiar / Copiar resumen** - manda el transcript (o el resumen) al portapapeles para
   pegar directo en Teams o en un mail.
9. **Resumen al parar** - checkbutton persistente (queda guardado entre sesiones en
   `transcriptor_config.json`): cuando está marcado, **Parar** arranca el resumen con
   Pi solo, sobre la sesión recién cerrada. Cerrar la app no lo dispara nunca.
10. **Historial** - lista los `*_live-captions.md` anteriores de `transcripts\`; con
   **Abrir**, **TXT**, **Resumir** y **Buscar** sobre lo que elijas, sin salir de la app.
   **Buscar** abre un diálogo de búsqueda global: busca en TODAS las sesiones a medida que
   escribís (sin distinguir mayúsculas) y abre el transcript con doble click.

Cada sesión guarda en `transcripts\<fecha>_live-captions.md`. La ventana usa un tema
oscuro (diseño generado con Stitch, paleta near-black + turquesa). Los timestamps del
archivo son de **hora real** (reloj de pared), así que la primera tanda de líneas ya no
sale como una ráfaga de `00:00:00`. Si la ventana de Subtítulos se cierra en plena
reunión, la app lo detecta y ofrece **reiniciar en el mismo archivo** (sin duplicar
líneas). La app tampoco lee otra instancia vieja de Live Captions: fija el PID de la
que abrió.

## App web (diseño Stitch, nuevo)

Mismo motor, misma pipeline: solo cambia la piel. Doble clic en **`Transcriptor Web.bat`
** — levanta un servidor local (`web_gui.py`) en `http://127.0.0.1:8737` y abre el
navegador solo. Todo lo que hace la app tkinter lo hace acá, **más fluido**: el
transcript se renderiza con el HTML de Stitch tal cual (el look exacto del diseño),
con pestañas **Transcripción / Resumen (Pi)**, historial con selector, filtro de
palabras en vivo (Enter = búsqueda global en todas las sesiones, con resultados
abriendo el transcript con un click) y banner de **Reanudar** si la ventana de
Subtítulos se cae. En la barra de resumen vive el toggle **Auto**: marcado, al cortar
la sesión el resumen con Pi sale solo (el ajuste es el mismo que el de la app tkinter).

- **Empuje real**: las líneas llegan por SSE apenas el watcher las emite (sin
  polling del navegador; latencia ≈ tkinter).
- **Solo escucha en 127.0.0.1**: no expone nada a la red.
- **Sin dependencias nuevas**: `http.server` de la stdlib; el primer render carga
  Tailwind/Google Fonts de CDN (con internet), el transcript sigue siendo 100% local.
- Puerto configurable con la variable `TRANSCRIPTOR_PORT` (default 8737).
- La app tkinter sigue funcionando igual: dos frentes, un mismo watcher.

## Resumen con IA (Pi local, sin API keys)

**Resumir con Pi** manda el transcript al asistente Pi que ya tenés instalado y configurado
(usando tu modelo de siempre, una corrida por clic, como una llamada de API):

- Escribe primero `transcripts\<stem>.limpio.md`: sin IA, junta frases cortadas, colapsa
  las líneas repetidas que reescribe Live Captions y quita repeticiones de palabras.
- Resume **sobre el texto limpio** y escribe `transcripts\<stem>.resumen.md` con secciones fijas:
  `## Resumen`, `## Puntos clave`, `## Decisiones`, `## Acciones`, `## Pendientes`, `## Citas`.
- El resumen vive y se muestra en su **pestaña propia** de la app, separado del
  transcript; hay botón para copiarlo al portapapeles.
- Guarda una copia exacta de lo resumido en `transcripts\.snapshots\`.
- Podés **Cancelar** en cualquier momento; el transcript crudo y el limpio nunca se tocan.
- El proceso de Pi corre contenido (solo lectura de archivos). No consume keys, no usa sesión,
  no instala nada; sí consume los tokens de tu plan de Pi, por eso es siempre a botón
  (o con el toggle **Resumen al parar** / **Auto**, que es la única excepción: sale solo
  cuando vos cortás la sesión).

## Modo Live Captions (recomendado: funciona hoy)

Windows tiene su propio transcriptor en vivo: **Subtítulos en vivo** (**Win+Ctrl+L**).
Se banca el audio del sistema aunque el driver de captura esté roto, porque usa una ruta
privilegiada del sistema. Entonces no capturamos audio: **leemos el texto que ya muestra**
por UI Automation.

```
1. Win + Ctrl + L                    -> aparece la barra de Subtítulos en vivo
2. py transcribe.py --check          -> confirma que la barra ve audio (opcional)
3. powershell -ExecutionPolicy Bypass -File captions_watch.ps1
```

El `.md` aparece en `transcripts\<fecha>_live-captions.md` y crece solo. `Ctrl+C` para cortar.

**Ventajas:** sin captura de audio, sin driver, sin nube, sin instalar nada, y sin usar la CPU
(faster-whisper no interviene: el reconocimiento ya lo hizo Windows).

**Opciones:**

| Flag | Para qué |
|---|---|
| `-Out ruta.md` | salida explícita |
| `-IntervalMs 350` | cada cuánto lee la ventana (no subir de 800: el buffer es rotativo) |
| `-CaptionsPid 1234` | fijar un proceso LiveCaptions puntual |
| `-Dump` | imprime el buffer actual una vez y sale (prueba rápida) |
| `-Quiet` | no escribe en consola |

**Límites honestos:**

- Sin atribución de quién habla: es un flujo continuo de líneas.
- Requiere Live Captions abierto y con el paquete de idioma instalado.
- El buffer que Live Captions ya traía en pantalla al arrancar entra con hora de pared
  aproximada (los timestamps ya no salen `00:00:00`, pero el clock de esa tanda es el
  de la lectura, no el de cuando se habló).
- Live Captions mantiene una ventana rotativa, por eso el script lee cada 350 ms.
- La calidad del texto es la de Windows, no la de Whisper: no se puede reprocesar el audio.

Si querés texto de mejor calidad y no te importa esperar, grabá la reunión y usá `--file`
con faster-whisper.

## Uso

Todos los comandos se corren desde esta carpeta:

```powershell
py transcribe.py --list          # qué se puede capturar acá
py transcribe.py --check         # abre cada entrada y muestra el error real
py transcribe.py --demo          # prueba el motor de voz sin micrófono
py transcribe.py                 # en vivo: audio del sistema -> transcripts/*.md
py transcribe.py --source mic    # en vivo: micrófono
py transcribe.py --file reunion.m4a
```

### En vivo (reunión / llamada)

```powershell
py transcribe.py --source loopback --lang es --model small
```

Escucha **lo que sale por los parlantes** (lo que dice el otro lado de la llamada).
Abrí el archivo `.md` que aparece en `transcripts/` y va creciendo solo. `Ctrl+C` para cortar.

Captura por bloques de 100 ms y cierra una "frase" cuando detecta:
- `--silence` segundos de silencio (default `0.6`), o
- `--max-utt` segundos seguidos hablando (default `25`).

`--threshold` (default `0.006`) es el umbral de RMS que decide qué es silencio.
Si en una sala ruidosa corta demasiado, subilo; si no corta nunca, bajalo.

Opciones útiles:

| Flag | Para qué |
|---|---|
| `--device <handle>` | elegir un dispositivo puntual de `--list` |
| `--backend soundcard\|pyaudio` | forzar una librería de captura (default `auto`) |
| `--model tiny\|base\|small\|medium` | `tiny`/`base` = menos latencia, `small` = mejor precisión |
| `--lang es\|auto` | idioma fijo o detección automática |
| `--out ruta.md` | salida explícita |

### Archivo grabado

```powershell
py transcribe.py --file "C:\ruta\reunion.m4a" --lang es
```

Segmenta con VAD, respeta los tiempos reales de cada frase y escribe el Markdown de una vez.
Acepta lo que decodifique PyAV: `wav`, `mp3`, `m4a`, `ogg`, `flac`, `mp4`.

## Salida

```markdown
# Transcript en vivo — 2026-10-21 14:03

- **Fuente**: loopback · Auriculares (Realtek(R) Audio)
- **Backend**: pyaudio · 48000 Hz · 2 ch
- **Modelo**: small · es

---

- **[00:00:04]** Buen día, arrancamos con el estado del proyecto.
- **[00:00:11]** El proveedor confirmó la entrega para el jueves.
```

## Problemas conocidos

### La captura de audio no abre en esta máquina

`--check` da **8 de 8 entradas fallando**:

```
FAIL [soundcard/loopback] Altavoces (Realtek(R) Audio)  RuntimeError: invalid argument
FAIL [soundcard/mic]      Micrófono (Realtek(R) Audio)  RuntimeError: invalid argument
FAIL [pyaudio/loopback]   Auriculares (Realtek(R) Audio) [Loopback]  OSError: [Errno -9996] Invalid device
FAIL [pyaudio/mic]        Micrófono (Realtek(R) Audio)  OSError: [Errno -9996] Invalid device
...
0 working, 8 failing.
```

No es el script: es el driver de captura. Evidencia recolectada:

- Falla el **micrófono común** igual que el loopback → no es un problema de loopback.
- Dos librerías independientes fallan en `IAudioClient::Initialize` / `PaWasapi` con
  `E_INVALIDARG` y `-9996`, pero `GetMixFormat()` responde bien → el endpoint existe y el
  driver rechaza el stream.
- **La reproducción funciona** (`sounddevice.play` OK) → códec y salida sanos.
- Privacidad de micrófono en `Allow`, `Audiosrv` y `AudioEndpointBuilder` corriendo, sesión
  local (no RDP).
- En el registro, la mayoría de endpoints de captura figuran `NOT PRESENT`, con duplicados
  obsoletos de auriculares desconectados (`thinkplus-LP75`).

**Qué probar, en orden:**

1. `Configuración → Sistema → Sonido → Entrada`: hablar y ver si la barra de nivel se mueve.
   Si queda en cero, es driver.
2. Desconectar/Reconectar los auriculares, o probar con otro dispositivo de entrada.
3. `Restart-Service Audiosrv` (suele requerir consola de administrador).
4. Actualizar o reinstalar el driver **Realtek High Definition Audio (SST)** desde el
   fabricante del equipo (no el genérico de Windows Update).
5. Reiniciar Windows: los endpoints WASAPI trabados se reenumeran al arrancar.

Cuando `py transcribe.py --check` devuelva al menos un `OK`, el modo en vivo funciona sin
tocar nada del código.

### Desfase entre lo que se dice y lo que aparece

Cada frase se transcribe **después** de que termina (al detectar el silencio), así que hay
una demora de `--silence` + el tiempo de inferencia. Bajar `--silence`, usar `--model base`
o `tiny` la reduce a costa de precisión.

### Acentos raros en la consola

El script reconfigura la salida a UTF-8 y el `.md` siempre se escribe en UTF-8, así que tanto el
archivo como la ventana de la app muestran los acentos correctos (desde `15f4f88`, el stdout del
watcher se fija en UTF-8; antes la ventana mostraba `�` porque PowerShell 5.1 emite el OEM). Si ves
caracteres rotos en otra herramienta, abrí el `.md` como UTF-8.

## Verificado

**Modo Live Captions** (contra una reunión real de Teams, en vivo):

- `-Dump` devolvió el texto que estaba en pantalla, en un solo bloque.
- Corrida de 18 s → 18 líneas escritas, con contenido real de la reunión.
- Corrida de 35 s → 13 líneas: 11 del buffer inicial en `00:00:00`, después `00:00:13` y
  `00:00:15`. Los timestamps avanzan y la deduplicación por solapamiento no repite líneas.
- `'{0:hh\:mm\:ss}' -f (New-TimeSpan -Seconds 65)` → `00:01:05` (formato de tiempo correcto).
- El texto se escribe en UTF-8 real, con acentos correctos.

**Modo archivo / motor de voz:**

- `--list` → 8 dispositivos enumerados (4 soundcard, 4 pyaudio), acentos correctos en consola.
- `--check` → 8/8 fallos reproducidos con el error real de cada backend.
- `--demo` → audio sintético en español (voz SAPI *Sabina*) transcrito correctamente:
  `Esta es una prueba del transcriptor local. La reunión comienza a las 3 de la tarde.`
- `--file` con `.m4a` (grabación local de Zoom) y `.mp4` (grabación de Teams) → ambos
  decodificados y transcriptos con timestamps.
- Modo en vivo por captura de audio → `exit 1`, error de cada backend y hint a `--check`,
  sin streams colgados.

## Verificado por WASAPI directo (por qué el live por audio no va)

Cliente COM escrito a mano, sin librerías de audio:

| Endpoint | `Initialize` |
|---|---|
| Render (parlante), `flags=0` | ✅ OK, `Start()` OK, buffer 1056 |
| Render, `LOOPBACK` | ❌ `0x80070057 E_INVALIDARG` |
| Captura (micrófono), `flags=0` | ❌ `0x80070057 E_INVALIDARG` |

En el endpoint de captura el driver **se contradice**: `IsFormatSupported(mix, shared) = S_OK`
y `Initialize(...) = E_INVALIDARG` con las 7 duraciones de buffer y los 2 juegos de flags
probados. `soundcard` (E_INVALIDARG), `pyaudiowpatch` (-9996) y `sounddevice` (-9999) fallan
igual. Es un defecto de driver/APO, no de la aplicación: por eso la salida Live Captions.

# Feature: pi-summary-export

Mejora del transcriptor en vivo: exportar TXT, mejorar la fluidez del texto y resumir con IA
usando Pi headless. Alcance acordado con el usuario: **N2, disparo manual a botón**.

Este documento es la especificación. Los subagentes leen **hasta `## Log`** (el Log es solo
historial de decisiones).

## Specs

- **S1** — Pedido textual del usuario: «que permita descargar un txt con toda la transcripcion».
  Destino: `transcripts/<stem>.txt`, derivado del crudo. El crudo `.md` **nunca se modifica**.
- **S2** — Pedido textual del usuario: «que mejore la fluencia».
  Capa 1: local, determinística, sin IA y sin red. Reglas exactas en `## Interfaces`.
- **S3** — Pedido textual del usuario: «que resuma con IA jeje».
  Motor: Pi headless, sin API keys nuevas, usando el modelo ya configurado por el usuario.
- **S4** — Decisión del usuario (Log L5): el TXT lleva timestamps, formato `[hh:mm:ss] texto`.
- **S5** — Decisión del usuario (Log L5): «Solo a botón». El resumen jamás se dispara solo.
- **S6** — Idea del usuario (Log L2), **fuera de alcance**: consultar Pi por RPC (`--mode rpc`)
  con chat de seguimiento, selector de modelo y resumen rodante. El puente debe tener una
  interfaz chica para poder caer a RPC después sin reescribir la app.

## Interfaces (pinneadas — no inventar otras)

### `transcript_lines.py` (T1)

```python
@dataclass(frozen=True)
class Utterance:
    t: str     # "hh:mm:ss", o "" si la línea no trae timestamp
    text: str

def parse_markdown(text: str) -> list[Utterance]
def read_markdown(path) -> list[Utterance]        # utf-8, errors="replace"
def to_txt(utterances) -> str                     # "[hh:mm:ss] texto\n"; sin t -> "texto\n"
```

Reglas del parser: ignora `# ...`, `---`, líneas vacías y metadatos con forma
`- **Clave**: valor`. Reconoce `- **[hh:mm:ss]** texto` (Live Captions y faster-whisper usan
el mismo formato) y también `- **[hh:mm:ss.fff]** texto`. Una línea con texto pero sin
timestamp es una `Utterance` con `t=""`, nunca se descarta.

### `fluidez.py` (T1)

```python
def clean(utterances) -> list[Utterance]
def render_markdown(title, meta, utterances) -> str
```

Reglas de `clean`, en este orden, todas determinísticas:

1. Normalizar espacios internos (`" ".join(text.split())`) y descartar textos vacíos.
2. Colapsar duplicados **consecutivos** exactos (comparando sin distinguir mayúsculas).
3. Colapsar duplicados **consecutivos** casi iguales (`difflib.SequenceMatcher.ratio() >= 0.92`),
   conservando el más largo: Live Captions reescribe la última línea mientras crece.
4. Unir fragmentos huérfanos: si una frase **no** termina en `.` `?` `!` `…` `:` `"` **y** la
   siguiente arranca en minúscula, se unen en una sola `Utterance` conservando el timestamp de
   la primera. Se corta de unir si el texto acumulado supera 400 caracteres.
5. Colapsar repetición inmediata de la misma palabra solo si es una stopword española
   (`de la que el y a en es un se no lo con por para una los las del`) o si se repite 3+ veces.

**Guardarraíl verificable:** `clean` no puede agregar palabras y no puede perder palabras fuera
de las que eliminan las reglas 2, 3 y 5. Se prueba con un test que compara multiset de palabras.

### `pi_bridge.py` (T2)

```python
class PiUnavailable(RuntimeError): ...
class SummaryError(RuntimeError): ...

def available() -> bool
def build_prompt(path, upto: str | None = None) -> str
def summarize(path, *, on_delta=None, timeout_s: float = 900.0) -> str
```

Invocación (verificada por smoke test real, obligatorio como evidencia de T2):

```
cmd /c pi --mode json --no-session --no-extensions --no-mcp --tools read <prompt>
```

Contención: `--tools read` (el hijo **solo** puede leer), `--no-session` (no ensucia las
sesiones del usuario), `--no-extensions --no-mcp` (sin herramientas de terceros ni servidores
MCP). **Nunca** `--api-key`: hereda el modelo ya configurado.

Lectura del stream: bytes de stdout decodificados en utf-8, partidos **solo por `\n`**.
Prohibido `readline`/`TextIOWrapper` con `readline`: también corta en `U+2028` y `U+2029`, que
son válidos dentro de un string JSON. Se lee stdout **siempre**, sin pausas: si no, Pi se
estanca. `stderr` es diagnóstico: nunca se parsea como protocolo.

Eventos: `message_update` → `assistantMessageEvent.type == "text_delta"` → `on_delta(delta)`;
el texto final autoritativo sale de `message_end` (rol assistant) o de `text_end.content`;
`agent_settled` cierra. Se ignoran los registros `type == "session"` y los ilegibles.
Salida distinta de 0 → `SummaryError` con el final de stderr. Timeout → matar y `SummaryError`.

Contrato del prompt (idioma: el del transcript, nunca el del código):

```
## Resumen            (3-6 lineas)
## Puntos clave
## Decisiones
## Acciones           (tabla: Que | Quien | Para cuando)
## Pendientes
## Citas              (- `[hh:mm:ss]` "frase textual")
```

Reglas del prompt: leer el archivo completo paginando con `offset`/`limit`; no inventar datos;
si no hay decisiones o acciones escribir «Sin datos en el transcript»; sin preámbulo.

### `transcriptor_app.py` (T3)

- Botones nuevos: `Exportar TXT`, `Resumir con Pi`, `Cancelar`.
- Flujo de resumen: congelar un snapshot del crudo en `transcripts/.snapshots/<stem>-<hhmmss>.md`
  (evidencia de qué se resumió) → `fluidez.clean` → escribir `transcripts/<stem>.limpio.md` →
  `pi_bridge.summarize` **sobre el limpio** → escribir `transcripts/<stem>.resumen.md` con
  encabezado (fecha, líneas, «resumido hasta»).
- Los deltas se muestran en la ventana a medida que llegan. `Cancelar` mata el proceso hijo.
- Nunca tocar widgets de tkinter desde el hilo worker: siempre `root.after(0, ...)`.
- Si `pi_bridge.available()` es falso, el botón queda deshabilitado con estado
  «Pi no está disponible».
- Derivados (`.txt`, `.limpio.md`, `.resumen.md`) se pueden reescribir. El crudo, jamás.

## Tasks

| ID | Spec | Qué | Superficie de edición | Commit |
|---|---|---|---|---|
| T1 | S1, S2, S4 | `transcript_lines.py` + `fluidez.py` + tests | `transcript_lines.py`, `fluidez.py`, `tests/` | C2 |
| T2 | S3, S5 | `pi_bridge.py` + tests + smoke test real | `pi_bridge.py`, `tests/` | C3 |
| T3 | S1, S3, S5 | Integración en `transcriptor_app.py` + README | `transcriptor_app.py`, `README.md` | C4 |
| T4 | todas | Suite completa + smoke manual de la app + evidencia | — | — |

Correr los tests desde la raíz del proyecto:

```
py -m unittest discover -s tests -t . -v
```

## Estado final

- **T1** ✓ commit `b363efe` — `transcript_lines.py` + `fluidez.py` + 36 tests (GREEN tras RED con 20 fallas).
- **T2** ✓ commit `12292dd` — `pi_bridge.py` + 12 tests herméticos; smoke real 9.4 s (sintético) y 21.8 s
  (transcript real de reunión); cancelación verificada vía watchdog (0.2 s, sin tokens).
- **T3** ✓ commit `9addab1` — app con 6 botones, snapshot del crudo en `.snapshots/`, README actualizado.
- **T4** ✓ suite: 48 tests OK (1 skip = smoke real bajo `PI_SMOKE=1`); app construida y cerrada sin errores
  en headless (6 botones verificados en la toolbar).
- **Evidencia extra**: pipeline aplicado al transcript real `2026-10-21_111159`: 13 líneas crudo ->
  12 limpias con "activation date 21 / del 10." bien unidas (regla 4 sobre dato real).
- **Nota (L7)**: la delegación a workers no estuvo disponible por el harness (no resuelve el doc de
  feature fuera del workspace de la sesión); T1-T4 se ejecutaron inline respetando las mismas specs.

## Log

- **L1** (pedido original, verbatim): «mejorame el proyecto de trancriptor»
- **L2** (aclaración, verbatim): «que permita descargar un txt con toda la transcripcion, que
  mejore la fluencia, y que resuma con IA jeje»
- **L3** (idea del usuario, verbatim): «osea que lo consulte por rpc y responda con el resumen o
  algo asi, nose ayduame a idearlo»
- **L4** (pregunta, verbatim): «y puede ser este gentleshell que haga el resumen?»
- **L5** (decisiones del usuario): alcance **N2** («N2 (recomendado)»), disparo **«Solo a botón»**,
  TXT **«Con timestamps (recomendado)»**.
- **L6** (decisión del usuario): «Sí, git init (recomendado)» → el proyecto pasa a estar bajo git.
- **Evidencia de exploración** (heredada por T1/T2/T3):
  - `cmd /c pi -p --no-session "Responde solo: OK"` desde Python → `rc: 0`, stdout `OK`, sin stderr.
  - Live Captions (`captions_watch.ps1`) y faster-whisper (`TranscriptWriter.add`) escriben el
    mismo formato de línea: `- **[hh:mm:ss]** texto`.
  - El proyecto no tenía git ni tests antes de esta feature.

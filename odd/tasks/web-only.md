# Solo web: un front, un bat

Rama `feature/web-only` sobre `main` (93d1a33). El usuario decidió mantener
solo la versión web de la app; la GUI tkinter y el modo crudo se eliminan.

## Specs

- **S1**: El core compartido vive en `transcriptor_core.py` (WatcherSession,
  Live Captions, historial, búsqueda, render de derivados) sin tkinter ni
  código de GUI. La web (`web_gui.py`) es el único front.
- **S2**: Un solo launcher: `FreeTranscriptor.bat` (web con bootstrap de
  `.venv`). `Transcriptor App.bat` y `TRANSCRIBIR EN VIVO.bat` borrados.
- **S3**: README solo documenta la app web; sin referencias a tkinter.
- **S4**: `transcribe.py` (motor whisper CLI para archivos) se mantiene: es
  un modo documentado independiente, no una GUI ni un bat.

## Tasks

- T1 (S1): extracción del core. Commit: pendiente.
- T2 (S2, S3): bats + README. Commit: pendiente.
- T3 (S4): suite + e2e + push. Commit: pendiente.

## Log

- L1 (usuario): "claro si no los tiene, que no se habiliten bro jajaj buena.
  mas bien solo me gustaria mantener la version web, las otras las puedes
  eliminar, que solo haya un solo bat bro" — valida el comportamiento actual
  (botones de resumen deshabilitados sin motor) y pide consolidar a web-only.
- L2: el resumen con motores alternativos (Ollama/API) queda como feature
  futura; no entra en esta rama.

# Transcriptor web GUI — frontend de navegador con el diseño Stitch

Servidor local mínimo (stdlib, sin dependencias nuevas) que sirve el HTML de Stitch
y empuja las líneas del transcript por SSE. El backend existente (watcher, fluidez,
Pi) no se toca; el tkinter queda como front alternativo.

## Specs

- **S1** (verbatim): «sii, porque el diseño del mismo stich esta bien chevere, en el
  tkinker no se visualiza igual que el del stich» — el web front reproduce el diseño
  de Stitch tal cual, con la paleta del screen generado (proyecto 1175727566459132727).
- **S2** Latencia: las líneas aparecen por SSE empujadas al navegador apenas el
  watcher las emite; no polling del navegador con intervalos largos.
- **S3** Funciones paridad con tkinter: Transcribir/Parar, Abrir transcript (descarga
  del .md), Exportar TXT, Copiar transcript, Resumir con Pi, Resumir hasta ahora
  (sin cortar la sesión), Cancelar resumen, Copiar resumen, Historial
  (abrir/TXT/resumir), status bar, dos pestañas Transcripción / Resumen (Pi).
- **S4** Sin dependencias nuevas: http.server stdlib + EventSource del navegador.
  Servidor escucha solo en 127.0.0.1 (no expone la red).
- **S5** El canal watcher (captions_watch.ps1, 250 ms, hilos) no cambia; el mismo
  WatcherSession alimenta ambos fronts. El tkinter sigue existiendo y funcionando.
- **S6** Fantasías del HTML generado (hablantes, VU meters, "Pi 3.1 Flash", cache,
  conf: 0.98) se quitan: la UI muestra datos reales del sistema, nada inventado.

## Tareas

- **T1** (S1) Descargar el HTML de Stitch a `web/index.html` y conservar tokens/CSS. Route: inline. Commit: W1.
- **T2** (S2, S4) `web_gui.py`: ThreadingHTTPServer en 127.0.0.1, SSE `/events`, fanout thread-safe, orquestación con WatcherSession + launch helpers + fluidez + pi_bridge reutilizando transcriptor_app. Route: inline. Commit: W1.
- **T3** (S3, S6) Limpiar el HTML (sin widgets ficticios) y cablear JS: EventSource, botones → fetch POST, copy con navigator.clipboard, historial, status bar. Route: inline. Commit: W2.
- **T4** (S4) `Transcriptor Web.bat`: levanta servidor y abre el navegador. Route: inline. Commit: W2.
- **T5** (S1-S6) Tests herméticos de endpoints + SSE (sin LiveCaptions real) y verificación: suite completa + corrida local del servidor. Route: inline. Commit: W2.
- **T6** README + doc Log. Route: inline. Commit: W3.

## Estado final

- **W1** commit `d06e317` + `c058593` — fix pendiente del watcher (Write-Host para que
  exit 2 sea alcanzable) + servidor `web_gui.py` (SSE, Hub, Orchestrator, acciones con
  validación de nombres) + 6 tests herméticos. RED→GREEN documentado por el worker.
- **W2** commit `dd20029` — `web/index.html` limpio (sweep de fantasías en cero matches)
  + JS vanilla cableado + `Transcriptor Web.bat`. Suite completa: 67 tests OK.
- **Verificación propia del padre**: suite 67 green + smoke real del servidor (GET /,
  GET /api/state en puerto aleatorio). Pendiente del usuario: abrir `Transcriptor Web.bat`
  en una reunión real y mirar la pestaña Resumen.

## Log

- **L1** (usuario, verbatim): «bro y si lo hacen en gui por WEB asi tal cual, el flujo
  de live transcript seria lento?»
- **L2** (usuario, verbatim): «sii, porque el diseño del mismo stich esta bien chevere,
  en el tkinker no se visualiza igual que el del stich» — decisión: web GUI con el
  diseño Stitch como front adicional, tkinter no se borra.
- **L3** Respuesta técnica dada y aceptada: latencia con SSE ≈ tkinter (30-80 ms extra),
  el cuello de botella es Live Captions (250 ms de UIA), no la GUI.

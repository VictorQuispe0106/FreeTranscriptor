# Transcriptor mejoras — panel de historial, fluido, copiar, timestamps, robustez

Mejoras de uso diario sobre `transcriptor_app.py` + `captions_watch.ps1`, preservando la
fluidez actual de la app (nunca agregar overhead en el bucle de lectura).

## Specs

- **S1** Historial de sesiones: la GUI lista los `.md` de `transcripts/` y permite
  abrir, exportar TXT y resumir cualquiera de ellos, no solo el de la sesión activa.
- **S2** Autoscroll inteligente: el widget de líneas solo sigue el final cuando el
  usuario ya está leyendo el final (hoy `see("end")` en cada línea lo tira abajo siempre).
- **S3** Copiar: botones para copiar el transcript y el resumen al portapapeles.
- **S4** El `.md` del watcher escribe timestamps de hora real (reloj de pared), sin
  tanda inicial en `00:00:00` por el buffer pre-existente.
- **S5** El watcher lee solo la instancia de Live Captions que lanzó la app
  (`-CaptionsPid` fija el PID de `launch_livecaptions()`).
- **S6** Si el watcher muere en plena sesión (p. ej. cerraron la ventana de Subtítulos),
  la app lo detecta y ofrece reiniciar la transcripción conservando el mismo archivo.
- **S7** El área de salida del resumen de Pi queda separada del transcript de la reunión.
- **S8** «Resumir hasta ahora»: permite pedir el resumen mientras sigue transcribiendo,
  sin cortar la transcripción.
- **S9** (guardarrail del usuario, verbatim) «Pero que no malogre su fluided»: ninguna
  mejora puede degradar la fluidez — el intervalo del watcher (350 ms) y el modelo de
  hilos no cambian; toda funcionalidad nueva es event-driven o por botón.

## Tareas


- **T1** (S1) Panel Historial en la GUI: lista + abrir + exportar TXT + resumir el elegido. Route: inline. Commit: P1.
- **T2** (S2, S9) Autoscroll solo al fondo: VAT-only in `_on_line`/`_on_tail`. Route: inline. Commit: P1.
- **T3** (S3) Copiar transcript / copiar resumen. Route: inline. Commit: P1.
- **T4** (S4, S9) Timestamp de hora real en captions_watch.ps1 + display coherente. Route: inline. Commit: P2.
- **T5** (S5) Pass pid desde launch_livecaptions() al watcher. Route: inline. Commit: P2.
- **T6** (S6) Detección de watcher muerto + reinicio conservando archivo. Route: inline. Commit: P2.
- **T7** (S7) Zona de resumen separada. Route: inline. Commit: P3.
- **T8** (S8) Botón «Resumir hasta ahora» sin cortar la transcripción. Route: inline. Commit: P3.
- **T9** (S1-S9) Verificación: suite de tests (pytest tests/) + corrida real de la app. Route: inline. Commit: P3.
- **T10** (Decisión L5) Tema visual del app según diseño Stitch (DESKTOP): paleta oscura,
  tipografía y espaciado por ttk.Style; sin cambiar nada de comportamiento. Route: inline. Commit: P4.

Commits: P1 usabilidad, P2 watcher/timestamps/robustez, P3 resumen + verificación,
~rebanadas reviewables. C1^ nunca en default branch: branch `feature/transcriptor-mejoras`.

## Estado final

- **P1** commit `591b6e7` — S1 (Historial: listado `list_transcripts`, fila con combobox y
  Abrir/TXT/Resumir), S2 (autoscroll solo al fondo, helper `at_end`), S3 parcial (Copiar
  transcript). 56 tests green + smoke headless.
- **P2** commit `757d437` — S4 (estampa hora real `HH:mm:ss` en el watcher), S5
  (`-CaptionsPid` del PID que lanzó la app), S6 (watcher sale con `exit 2` si la ventana
  desaparece tras ~15 s; header solo en archivo nuevo; seed de dedupe desde el archivo;
  app ofrece reiniciar en el mismo archivo). 60 tests green + syntax-check del .ps1.
- **P3** commit `7ddd956` — S7 (pestaña propia «Resumen (Pi)»), S8 («Resumir hasta
  ahora» sin cortar la sesión, flag `live` end-to-end), S3 completo (Copiar resumen,
  Copiar habilitado con transcript), README actualizado. Smoke+suite green.
- **Verificación funcional (T9)** corrida real contra LiveCaptions: header correcto en
  archivo nuevo; reinicio en el mismo archivo NO trunca ni duplica (`dupes=0`); muerte
  por ventana perdida → `exit 2` en ~20 s. Corridas previas a la reunión real emitieron
  5 líneas con dedupe 0.
- **Aprendizaje nuevo**: con `$ErrorActionPreference='Stop'`, `Write-Error` mata el
  proceso con exit 1 antes de llegar al `exit 2` — el mensaje va con `Write-Host`.
- **Pendiente del usuario**: test de clic en una reunión real (Transcribir → líneas →
  Resumir hasta ahora → Parar); mismo criterio que dejó pendiente la feature anterior.

## Log

- **L1** (usuario, verbatim): «bro la app de transcripto esta fluido y funciona bien,
  que podriamos mejorarle»
- **L2** (usuario, verbatim): «Pero que no malogre su fluided» — guardarrail S9.
- **L3** (usuario, selección en el cuadro): Historial+autoscroll+copiar (1-3), Timestamps
  reales+CaptionsPid (5-6), Robustez: reinicio, zona resumen, resumen parcial (7-9),
  Todas, en ese orden.
- **L4** Incidente entorno: bash.exe de la sesión de Pi roto (Git Local sin
  usr\bin\bash.exe). settings.json de Pi reapuntado a `C:\Program Files\Git\bin\bash.exe`;
  requiere /reload. Mientras tanto se avanza con lo no requerido de comandos.
- **L5** (usuario): «Bro podrias mejorar la interfaz con stitch?» → decisión tomada en el
  cuadro: *Diseño + tema tkinter* (Stitch genera la pantalla DESKTOP y la identidad se
  aplica al tkinter con ttk.Style; sin reescritura web).",

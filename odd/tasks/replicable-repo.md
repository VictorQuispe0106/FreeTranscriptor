# Repo replicable (Fase 1)

Rama `feature/replicable-repo` sobre `feature/mejoras-flujo`. Meta: que
cualquiera clone `github.com/VictorQuispe0106/FreeTranscriptor` y arranque
con doble clic.

## Specs

- **S1**: `requirements.txt` con las versiones pineadas del set verificado
  (numpy incluido: transcribe.py lo importa directo).
- **S2**: los launchers Python (`Transcriptor App.bat`, `Transcriptor
  Web.bat`) hacen bootstrap de `.venv` en la primera corrida: eligen `py` o
  `python` del PATH, exigen Python 3.12+ (los wheels pineados), crean el
  venv e instalan requirements. Sin rutas de máquina.
- **S3**: README con instalación desde cero para un clon de GitHub
  (Windows 11, Python 3.12+, internet solo la primera vez, Pi opcional).
- **S4**: `.gitignore` con `.venv/`.

## Tasks

- T1 (S1-S4): requirements + launchers bootstrap + README + gitignore +
  test del set pineado. Commit: `61017b6`.

## Log

- L1 (usuario): "me gustaria que este proyecto sea replicable, osea que
  cualquier persona pueda replicarlo en maquina a traves de mi github...
  que sea como una app bro que no se amarre tanto a mi bat local".
- L2 (usuario): repo `https://github.com/VictorQuispe0106/FreeTranscriptor`;
  decisión: mergear todo a main y subir.
- L3: verificación funcional: bootstrap del venv real ejecutado (misma
  secuencia del .bat, BOOTSTRAP-OK en 1m43s); imports completos desde el
  venv (IMPORTS-OK); server web arranca y sirve /api/state (SERVER-OK);
  suite completa verde desde el venv.

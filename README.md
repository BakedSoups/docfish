# Angler

A concise, sea-inspired coding and documentation assistant for the models installed in Ollama. It discovers models automatically, lets you switch between them, and streams responses.

## Run

Make sure Ollama is running, then:

```bash
venv/bin/python server.py
```

Open <http://127.0.0.1:8080>.

If Ollama is on another machine or port:

```bash
OLLAMA_HOST=http://192.168.1.10:11434 venv/bin/python server.py
```

## Documentation RAG

The left sidebar browses the installed Godot, Pandas, Go, JavaScript/MDN, NumPy, React, Python, and Git manuals. Click **Index** beside a library once, then enable **RAG mode** to ground answers in that selected documentation. Indexing runs in the background and stores local vectors under `Documentation/qdrant`.

To recreate the Python environment:

```bash
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
```

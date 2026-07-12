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

The Library browses the installed Godot, Pandas, Go, JavaScript/MDN, NumPy, React, Python, Git, and PDF manuals. Click **Index** beside a library once, then enable **RAG mode** to ground answers in that selected documentation. Qdrant runs locally in Docker and stores its data under `Documentation/qdrant-server`.

```bash
docker start angler-qdrant
venv/bin/python server.py
```

The sidebar’s **Google ↗** button runs a normal `site:stackoverflow.com` Google search in a new tab. It does not scrape Google or require an API key.

PDF files placed in `Documentation/PDF_docss` appear automatically in the library. They can be read in the document pane, searched by page text, and indexed for page-numbered RAG citations.

To recreate the Python environment:

```bash
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
```

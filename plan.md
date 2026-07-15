# Docfish Product and Implementation Plan

## Product goal

Docfish should be a lightweight, local-first RAG learning tool. It should help a user turn a rough request into a precise one-to-three-shot question, retrieve focused evidence from sources the user controls, answer with citations, and reinforce the result through explanation, notes, or a short quiz.

The default installation must not bundle a large documentation warehouse. Models, documentation packs, and large indexes are separate, opt-in downloads.

## Target user experience

1. Install and launch Docfish.
2. Connect to or select a local Ollama model.
3. Add a PDF, Markdown/text file, or HTML documentation folder.
4. Review the estimated source and index size.
5. Index only that source, with progress that survives restarts.
6. Write a rough question and use **Improve question** to clarify its goal, context, constraints, and desired output.
7. Retrieve relevant evidence and generate a cited answer.
8. Save the result as a learning note or generate a short quiz.

## Size and packaging targets

- Keep the core application and embedding dependencies below 1 GB where practical.
- Keep the normal first-run footprint below 3 GB, excluding Ollama models and user content.
- Bundle no documentation collections by default.
- Show download, source, and estimated index sizes before an operation begins.
- Offer large collections such as Stack Overflow only as clearly labeled, optional content packs.
- Allow each index to be deleted without deleting its source files.

The current `Documentation/StackOverflow` directory is about 119 GB and must never be part of the default installer. The current HTML documentation is about 4.2 GB and should also become optional.

## Architecture direction

### Core application

- Keep the UI and HTTP service small and local.
- Use SQLite for the source registry, file manifests, persistent jobs, and learning history.
- Use SQLite FTS5 for exact terms, identifiers, and error messages.
- Keep vector search behind a backend interface.
- Support a lightweight embedded vector backend by default.
- Retain Qdrant server as an optional backend for large libraries.
- Keep Ollama configurable rather than bundling an LLM.

### Source model

Replace the hard-coded `DOCS` dictionary with user-managed source records containing:

- Stable source ID and display name
- Source type: PDF, HTML, Markdown, or text
- Local path
- Include and exclude patterns
- Optional home page and cover metadata
- Parser and embedding versions
- Index state, size, and timestamps

Later source adapters may add Git repositories, documentation archives, and explicitly downloaded web content without changing the indexing core.

### Indexing model

Indexing must be incremental and restart-safe. For each file, persist its path, content hash, modification time, parser version, and chunk IDs. A refresh should add new files, re-index changed files, remove deleted files, and skip unchanged files.

Do not delete an entire working collection before a replacement has completed. Failed or interrupted jobs must leave the previous usable index intact.

### Retrieval model

Use hybrid retrieval:

- Vector similarity for concepts and paraphrases
- FTS5/BM25-style lexical search for exact names and errors
- Result fusion and deduplication
- Neighbor expansion for surrounding context
- Source, document, section, and page citations

Answers should clearly distinguish supported claims from model inference and should not silently answer from general model knowledge when the user requests source-only grounding.

### Learning workflow

The question-crafting workflow should expose editable fields:

- Goal
- Known context
- Constraints
- Exact question
- Desired response format
- Selected sources

Primary actions:

- Improve question
- Identify missing context
- Answer from selected sources
- Explain simply
- Test me
- Save learning note

## Planned commits

Each commit should leave the application runnable and include focused verification.

### 1. `chore: rename Angler to Docfish and define product boundaries`

- Replace all remaining Angler product references with Docfish across code, UI, documentation, metadata, container names, configuration examples, accessibility labels, and generated messages.
- Rename internal identifiers and assets where doing so improves clarity.
- Preserve temporary aliases or migration handling for existing container names, storage paths, environment variables, and browser settings so current installations are not broken by the rename.
- Verify that no old user-facing product references remain with a repository-wide search.
- Add this plan and document default size limits.
- Describe core install versus optional models, sources, and content packs.
- Document privacy expectations: local paths and document contents remain local.

Acceptance: the UI and documentation consistently say Docfish, existing local data remains usable, the README links to this plan, and the README does not imply bundled documentation is required.

### 2. `refactor: introduce source and vector backend interfaces`

- Define source, parser, chunk, search-result, and vector-store contracts.
- Wrap current Qdrant calls behind a vector backend.
- Preserve current behavior while removing Qdrant-specific operations from indexing logic.

Acceptance: existing Git/Go/Python searches return the same citation fields through the new interface.

### 3. `feat: persist source registry and jobs in sqlite`

- Add a small SQLite schema for sources, documents, chunks, and index jobs.
- Add schema initialization and migrations.
- Import currently detected sources as optional legacy entries without hard-coding them into runtime logic.
- Replace in-memory `_status` and the JSON completion manifest.

Acceptance: source state and job progress remain correct after restarting Docfish.

### 4. `feat: add source management api`

- Add list, create, inspect, update, and remove endpoints.
- Validate and normalize local paths.
- Support include/exclude patterns and prevent path traversal.
- Separate “remove index” from “delete source record”; never delete user files implicitly.

Acceptance: a source can be registered, listed, updated, and removed using the API without editing Python code.

### 5. `feat: add pdf markdown text and html source adapters`

- Extract current HTML and PDF parsing into adapters.
- Add Markdown and plain-text adapters.
- Standardize document titles, section anchors, page numbers, and source metadata.
- Report skipped, unsupported, and unreadable files.

Acceptance: fixtures for all four formats produce normalized chunks and citations.

### 6. `feat: make indexing incremental and restart safe`

- Persist file hashes, modification times, parser versions, and chunk identities.
- Add, update, and remove only affected documents.
- Store checkpoints and resume interrupted jobs.
- Build replacement data safely before removing a usable prior index.
- Add cancel and retry operations.

Acceptance: changing one file re-embeds only that file; restarting during indexing resumes without rebuilding completed documents.

### 7. `feat: add embedded lexical and vector retrieval`

- Add SQLite FTS5 indexing and lexical search.
- Implement the default embedded vector backend.
- Fuse lexical and vector rankings and deduplicate adjacent chunks.
- Retain Qdrant as an optional configuration for large collections.

Acceptance: Docfish runs without Docker for a small library, while the existing Qdrant configuration remains supported.

### 8. `feat: add source import and storage controls to library ui`

- Add an import flow for files and folders.
- Show source size, estimated index size, supported file count, and warnings before indexing.
- Show persistent queued/indexing/ready/error/cancelled states.
- Add re-index, cancel, remove index, and remove source controls.
- Never start indexing every discovered source automatically by default.

Acceptance: a new user can add and index a source without touching the filesystem layout or configuration code.

### 9. `feat: add structured one-shot question crafting`

- Add an editable question workspace for goal, context, constraints, exact question, and response format.
- Add **Improve question** and **Identify missing context** actions.
- Preserve the original question and show the proposed rewrite before use.
- Store the final crafted question with its selected sources.

Acceptance: users can review and edit every part of the final prompt before submitting it.

### 10. `feat: generate grounded answers with citation checks`

- Build prompts from the crafted question and retrieved evidence.
- Require stable source references in generated answers.
- Display evidence beside the answer and link citations to the local document location.
- Report insufficient evidence instead of silently falling back to an ungrounded answer.

Acceptance: each grounded factual claim has a resolvable citation, or the UI clearly reports that the sources are insufficient.

### 11. `feat: add local learning notes and quizzes`

- Save questions, crafted prompts, answers, citations, and user corrections locally.
- Add **Explain simply** and **Test me** actions.
- Generate short recall and application questions from cited material.
- Allow export to Markdown or JSON.

Acceptance: a completed session can be reopened, reviewed, quizzed, and exported without network services.

### 12. `feat: add optional content pack manifest`

- Define a content-pack manifest with name, version, license, source URL, download size, installed size, and checksum.
- Keep every pack opt-in.
- Support install, update, verify, and uninstall operations.
- Start with small official documentation packs; keep Stack Overflow an advanced, separately documented option.

Acceptance: the base installation contains no documentation pack and the user sees exact size and license information before downloading one.

### 13. `chore: add packaging diagnostics and cleanup tools`

- Add a first-run health check for Ollama, embedding availability, writable storage, and the configured vector backend.
- Add per-source disk usage and stale-index cleanup.
- Add import/export for settings and source definitions without exporting documents by default.
- Provide a non-Docker quick start and an optional Docker Compose deployment.

Acceptance: a clean machine can reach its first cited answer through documented setup, and all generated storage can be located and removed from the UI.

### 14. `test: cover ingestion retrieval recovery and security`

- Add parser and chunking unit tests.
- Add incremental-index and restart-recovery tests.
- Add hybrid-retrieval and citation-resolution tests.
- Test traversal prevention, malformed files, duplicate sources, cancelled jobs, and unavailable Ollama/vector backends.
- Add a small end-to-end fixture library suitable for CI.

Acceptance: the core workflow is exercised without downloading large documentation sets or an LLM during CI.

## Release stages

### Milestone 1: User-managed local RAG

Commits 1–8. A user can install the small core, add supported local sources, index incrementally, and search without a hard-coded catalog.

### Milestone 2: Question crafting and learning

Commits 9–11. Docfish becomes a focused learning product rather than a generic Ollama chat interface.

### Milestone 3: Distribution readiness

Commits 12–14. Optional content, storage controls, packaging, recovery, and automated tests make the application suitable for other users.

## Explicit non-goals for the first release

- Bundling Ollama models
- Bundling Stack Overflow or multi-gigabyte documentation sets
- Crawling arbitrary websites
- Multi-user accounts or cloud synchronization
- Training or fine-tuning models
- Replacing full documentation browsers or note-taking applications

## Definition of ready for other users

Docfish is ready for an initial public release when a new user can install the core without a large content download, connect a local model, add one source, see its storage cost, survive an interrupted index, craft a one-to-three-shot question, receive a cited answer, and fully remove the generated index through the UI.

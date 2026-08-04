# Knowledge Base and deterministic RAG

Stage 13 extends the existing `KnowledgeSource`, `KnowledgeDocument`, and
`KnowledgeChunk` records instead of creating a parallel knowledge subsystem. A
project points to one knowledge base and each new Call or Simulator session pins the
currently published `KnowledgeBaseRevision`. Draft changes therefore cannot affect an
active or historical conversation.

## Revision lifecycle

- A base has at most one editable `draft` revision and one active published revision.
- Publishing is transactional and requires every non-archived document version to be
  `ready`.
- Published revisions, document versions, chunks, and citation text are immutable.
- A new draft copies the published document versions and chunks. Uploading a changed
  file creates a new `KnowledgeDocumentVersion` inside that draft.
- Archiving affects future revisions and retrieval. Historical revisions and citations
  remain readable through tenant- and project-authorized endpoints.
- `Call.knowledge_base_revision_id` records the exact published revision used by the
  call. A project without a published revision continues without knowledge retrieval.

Document processing uses the canonical states `uploaded`, `queued`, `extracting`,
`chunking`, `embedding`, `ready`, `failed`, `needs_ocr`, and `archived`. All changes use
the lifecycle service and optimistic `lock_version`. The Worker claims jobs with
`FOR UPDATE SKIP LOCKED`, a lease token, heartbeat, retry backoff, and stale-job
recovery. Two workers cannot finish the same leased version.

## Upload and storage boundary

Supported files are UTF-8 TXT and Markdown, text-layer PDF, and non-macro DOCX. The
default upload limit is 25 MiB; PDF pages, extracted characters, chunks, ZIP expansion,
compression ratio, parser time, and Worker memory are separately bounded. The API
checks extension, declared MIME type, magic bytes, actual parser structure, empty or
encrypted PDF, path traversal, external DOCX relationships, macros, executable ZIP
members, and ZIP bombs. A scanned PDF becomes `needs_ocr`; OCR is intentionally not
performed in this stage.

Originals are stored in the existing private MinIO bucket. Object keys are generated
server-side from tenant, project, base, document, and version identifiers; the client
cannot supply an object key. SHA-256, content length, and content type are recorded.
Downloads use a short-lived signed URL after backend RBAC and tenant/project checks.
Repeated upload requests are idempotent and checksum duplicates are detected within
the knowledge-base scope. Files are archived, not hard-deleted.

Relevant configuration names (values must remain secret where applicable):

- `KNOWLEDGE_MAX_FILE_BYTES`
- `KNOWLEDGE_MAX_PDF_PAGES`
- `KNOWLEDGE_MAX_EXTRACTED_CHARS`
- `KNOWLEDGE_MAX_CHUNKS`
- `KNOWLEDGE_MAX_DOCX_EXPANDED_BYTES`
- `KNOWLEDGE_MAX_ZIP_RATIO`
- `KNOWLEDGE_CHUNK_SIZE_CHARS`
- `KNOWLEDGE_CHUNK_OVERLAP_CHARS`
- `KNOWLEDGE_EMBEDDING_PROVIDER`
- `KNOWLEDGE_EMBEDDING_MODEL`
- `KNOWLEDGE_EMBEDDING_DIMENSION`
- `KNOWLEDGE_PARSER_TIMEOUT_SECONDS`
- `WORKER_MEMORY_LIMIT`
- `MINIO_ENDPOINT`, `MINIO_BUCKET`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`

## Extraction and chunking

Extraction preserves page boundaries, headings, sections, paragraphs, important
numbers, and Unicode text. Language codes use normalized lowercase BCP 47 form:
`ru`, `uz`, `en`, `kaa`, `kaa-latn`, and `kaa-cyrl` are supported, while `ka` remains
Georgian. Automatic language detection is advisory; administrators select the stored
language.

Chunking is deterministic. It first follows document, page, section, and paragraph
boundaries, then uses sentence-aware splitting for oversized blocks and a configured
character overlap. Every chunk records its revision, document version, language, page,
section, ordinal, normalized text, checksum, character/token estimate, embedding
provider/model/dimension, and index version. A changed algorithm, model, dimension, or
index is represented by a new revision/index; incompatible vectors are never mixed.

## Embeddings and retrieval

The embedding interface has two explicit modes:

- `deterministic_mock` is available only in development and tests. It creates stable
  vectors for repeatable tests and must not be described as production semantic search.
- A configured production provider reports unavailable until its implementation and
  credentials are verified. No OpenAI or other external embedding request is made in
  Stage 13.

PostgreSQL uses the pinned pgvector 0.8.2 image and a `vector` column. Retrieval always
filters by authorized tenant, project, published revision, ready status, language,
allowed document IDs, provider, model, dimension, and index version before ranking.
Russian uses PostgreSQL `russian` text search, English uses `english`, and Uzbek,
Karakalpak, Georgian, and unknown languages use `simple` to avoid incorrect stemming.

The documented fusion formula is:

`combined_score = 0.60 * max(vector_cosine_similarity, 0) + 0.40 * min(lexical_rank, 1)`

Exact phrase occurrence is a bounded lexical fallback. Candidates below the configured
threshold are removed, duplicate chunk checksums are collapsed, and `top_k` is limited
to 20. Retrieval is calculated in PostgreSQL. A result contains the exact chunk,
document/version, base revision, page, section, excerpt, lexical/vector/combined scores,
provider/model/index metadata, latency, and usage estimate. A missing result is
`no_match`; neither the API nor Simulator invents an answer.

## Citations and untrusted content

A citation endpoint resolves the stored chunk and its immutable document version after
tenant/project authorization. It never manufactures a page or section. Historical
citations keep working after a newer revision is published.

Document text is untrusted data. Retrieved content is labelled as cited context and
cannot alter system instructions, permissions, tools, tenant selection, tasks, or
transfers. Prompt-injection phrases in documents are returned only as document text.
Simulator uses deterministic retrieval without an LLM, records the retrieval execution
and citations in `ToolExecution`, and offers transfer/no-information handling on
`no_match`.

## Security and live boundary

Knowledge tables use tenant-aware foreign keys, project scope, indexes, and PostgreSQL
`ENABLE ROW LEVEL SECURITY` plus `FORCE ROW LEVEL SECURITY`. Owners and managers manage
drafts and originals; analysts have read-only published access; operators consume only
published knowledge for assigned projects. Realtime status changes reuse the durable
Stage 12 outbox and send only minimal identifiers followed by a REST refetch.

Production embedding quality, external provider credentials, OCR, antivirus scanning,
generative answers, retention cleanup, OpenAI Realtime, SIP, and deployment are outside
this stage. Until a real provider is configured and tested, the explicit status is:

`BLOCKED — LIVE EMBEDDING VERIFICATION REQUIRED`

Run `py -3.12 scripts/run_knowledge_migration_test.py` to verify pgvector availability,
legacy data preservation, RLS, upgrade, and downgrade in an isolated local database.

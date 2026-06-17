import hashlib
import json
import math
import re
from pathlib import Path


DATA_DIR = Path(__file__).parent / "data"
MEDICAL_DOCS_DIR = Path(__file__).parent / "medical"
KNOWLEDGE_PATH = DATA_DIR / "elderly_medical_knowledge.json"
VECTOR_DB_PATH = DATA_DIR / "elderly_medical_vector_db.json"
EMBEDDING_DIMENSIONS = 384
CHUNK_MAX_CHARS = 420
CHUNK_OVERLAP_CHARS = 80
SUPPORTED_DOCUMENT_EXTENSIONS = {".md", ".txt"}
SOURCE_SCHEMA_VERSION = 2


def tokenize(text):
    tokens = re.findall(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]", text.lower())
    cjk_chars = [token for token in tokens if re.fullmatch(r"[\u4e00-\u9fff]", token)]
    cjk_bigrams = [
        f"{cjk_chars[index]}{cjk_chars[index + 1]}"
        for index in range(len(cjk_chars) - 1)
    ]
    return tokens + cjk_bigrams


def embed_text(text):
    vector = {}
    for token in tokenize(text):
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        index = int(digest[:8], 16) % EMBEDDING_DIMENSIONS
        vector[str(index)] = vector.get(str(index), 0.0) + 1.0

    norm = math.sqrt(sum(value * value for value in vector.values()))
    if not norm:
        return vector

    return {
        index: value / norm
        for index, value in vector.items()
    }


def cosine_similarity(left, right):
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(index, 0.0) for index, value in left.items())


def chunk_text(text, max_chars=CHUNK_MAX_CHARS, overlap_chars=CHUNK_OVERLAP_CHARS):
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n+", text) if paragraph.strip()]
    chunks = []
    current = ""

    for paragraph in paragraphs:
        if not current:
            current = paragraph
        elif len(current) + len(paragraph) + 1 <= max_chars:
            current = f"{current}\n{paragraph}"
        else:
            chunks.append(current)
            overlap = current[-overlap_chars:] if overlap_chars else ""
            current = f"{overlap}\n{paragraph}".strip()

    if current:
        chunks.append(current)

    return chunks


def get_document_paths():
    if not MEDICAL_DOCS_DIR.exists():
        return []
    return sorted(
        path
        for path in MEDICAL_DOCS_DIR.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_DOCUMENT_EXTENSIONS
    )


def get_source_paths():
    document_paths = get_document_paths()
    if document_paths:
        return document_paths
    return [KNOWLEDGE_PATH]


def get_source_fingerprint():
    return {
        "schema_version": SOURCE_SCHEMA_VERSION,
        "sources": [
        {
            "path": path.relative_to(Path(__file__).parent).as_posix(),
            "mtime": path.stat().st_mtime,
        }
        for path in get_source_paths()
        if path.exists()
        ],
    }


def document_title(path, content):
    for line in content.splitlines():
        stripped = line.strip()
        if path.suffix.lower() == ".md" and stripped.startswith("#"):
            return stripped.lstrip("#").strip() or path.stem
        if path.suffix.lower() == ".txt" and stripped:
            return path.stem
    return path.stem


def load_medical_documents():
    documents = []
    for path in get_document_paths():
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            continue

        relative_path = path.relative_to(Path(__file__).parent).as_posix()
        documents.append({
            "id": f"{path.suffix.lower().lstrip('.')}:{relative_path}",
            "title": document_title(path, content),
            "source": relative_path,
            "url": f"file://{relative_path}",
            "keywords": [],
            "content": content,
        })

    return documents


def load_knowledge_base():
    medical_documents = load_medical_documents()
    if medical_documents:
        return medical_documents

    with KNOWLEDGE_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def build_vector_db():
    chunks = []
    for document_index, document in enumerate(load_knowledge_base()):
        keywords = ", ".join(document.get("keywords", []))
        source_text = "\n".join([
            f"Title: {document['title']}",
            f"Keywords: {keywords}",
            f"Content: {document['content']}",
        ])

        for chunk_index, chunk in enumerate(chunk_text(source_text)):
            chunk_id = f"doc-{document_index}-chunk-{chunk_index}"
            chunks.append({
                "id": chunk_id,
                "document_id": document.get("id", f"doc-{document_index}"),
                "title": document["title"],
                "source": document["source"],
                "url": document["url"],
                "text": chunk,
                "embedding": embed_text(chunk),
            })

    vector_db = {
        "embedding_model": "local-hash-bow-v1",
        "embedding_dimensions": EMBEDDING_DIMENSIONS,
        "chunk_max_chars": CHUNK_MAX_CHARS,
        "chunk_overlap_chars": CHUNK_OVERLAP_CHARS,
        "source_fingerprint": get_source_fingerprint(),
        "chunks": chunks,
    }
    return vector_db


def write_vector_db(vector_db):
    DATA_DIR.mkdir(exist_ok=True)
    with VECTOR_DB_PATH.open("w", encoding="utf-8") as file:
        json.dump(vector_db, file, ensure_ascii=True, indent=2)


def vector_db_is_stale():
    if not VECTOR_DB_PATH.exists():
        return True

    with VECTOR_DB_PATH.open("r", encoding="utf-8") as file:
        vector_db = json.load(file)

    return vector_db.get("source_fingerprint") != get_source_fingerprint()


def ensure_vector_db():
    if vector_db_is_stale():
        write_vector_db(build_vector_db())


def load_vector_db():
    ensure_vector_db()
    with VECTOR_DB_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def retrieve_medical_context(query, limit=3, min_score=0.05):
    query_embedding = embed_text(query)
    scored_chunks = [
        (cosine_similarity(query_embedding, chunk["embedding"]), chunk)
        for chunk in load_vector_db()["chunks"]
    ]
    return [
        {
            "score": score,
            **chunk,
        }
        for score, chunk in sorted(scored_chunks, key=lambda item: item[0], reverse=True)
        if score >= min_score
    ][:limit]


def format_medical_context(query, limit=3):
    chunks = retrieve_medical_context(query, limit=limit)
    if not chunks:
        return ""

    sections = []
    for index, chunk in enumerate(chunks, start=1):
        sections.append(
            "\n".join([
                f"[{index}] {chunk['title']} (score: {chunk['score']:.3f})",
                f"Source: {chunk['source']} - {chunk['url']}",
                f"Chunk: {chunk['text']}",
            ])
        )

    return "\n\n".join(sections)

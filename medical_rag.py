import json
import re
from functools import lru_cache
from pathlib import Path


KNOWLEDGE_PATH = Path(__file__).parent / "data" / "elderly_medical_knowledge.json"


def _tokenize(text):
    return re.findall(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]{2,}", text.lower())


@lru_cache(maxsize=1)
def load_knowledge_base():
    with KNOWLEDGE_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def _score_document(query, document):
    score = 0
    query_lower = query.lower()
    query_tokens = set(_tokenize(query))
    document_text = " ".join([
        document["title"],
        document["content"],
        " ".join(document.get("keywords", [])),
    ]).lower()

    for keyword in document.get("keywords", []):
        if keyword.lower() in query_lower:
            score += 4

    for token in query_tokens:
        if token in document_text:
            score += 1

    return score


def retrieve_medical_context(query, limit=3):
    scored_documents = [
        (_score_document(query, document), document)
        for document in load_knowledge_base()
    ]
    matches = [
        document
        for score, document in sorted(scored_documents, key=lambda item: item[0], reverse=True)
        if score > 0
    ]

    return matches[:limit]


def format_medical_context(query, limit=3):
    documents = retrieve_medical_context(query, limit=limit)
    if not documents:
        return ""

    sections = []
    for index, document in enumerate(documents, start=1):
        sections.append(
            "\n".join([
                f"[{index}] {document['title']}",
                f"來源：{document['source']} - {document['url']}",
                f"重點：{document['content']}",
            ])
        )

    return "\n\n".join(sections)

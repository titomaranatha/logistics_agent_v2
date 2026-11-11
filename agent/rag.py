"""Operational knowledge retrieval: heading-aware chunking + BM25. Deterministic, no external service.
Upgrade path: swap `search` for pgvector/embedding retrieval behind the same interface."""
import math
import re
from pathlib import Path

STOP = set("a an the of to in on for and or is are be it this that with at by from as how what do does i we you my our".split())


SUFFIXES = ("ations", "ation", "ings", "ing", "ions", "ion", "ies", "es", "ed", "s")


def stem(w):
    for suf in SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


def tokenize(text):
    return [stem(t) for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOP]


def _parse_markdown(f):
    """Split one SOP file into (doc, section, text) chunks on '## ' headings."""
    title, section, buf, out = f.stem, None, [], []

    def flush():
        if section and buf:
            out.append({"source": f.name, "doc": title, "section": section, "text": " ".join(buf)})

    for line in f.read_text().splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
        elif line.startswith("## "):
            flush()
            section, buf = line[3:].strip(), []
        elif line.strip():
            buf.append(line.strip())
    flush()
    return out


class KnowledgeBase:
    def __init__(self, chunks, k1=1.5, b=0.75):
        self.chunks, self.k1, self.b = chunks, k1, b
        self.toks = [tokenize(c["section"] + " " + c["text"]) for c in chunks]
        self.avgdl = (sum(map(len, self.toks)) / len(self.toks)) if self.toks else 0
        self.df = {}
        for t in self.toks:
            for w in set(t):
                self.df[w] = self.df.get(w, 0) + 1

    @classmethod
    def load(cls, directory):
        chunks = []
        for f in sorted(Path(directory).glob("*.md")):
            chunks.extend(_parse_markdown(f))
        return cls(chunks)

    def search(self, query, k=3, min_score=1.0):
        q, n, scored = tokenize(query), len(self.chunks), []
        for c, toks in zip(self.chunks, self.toks, strict=True):
            score = 0.0
            for w in set(q):
                tf = toks.count(w)
                if not tf:
                    continue
                idf = math.log((n - self.df[w] + 0.5) / (self.df[w] + 0.5) + 1)
                score += idf * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * len(toks) / self.avgdl))
            if score >= min_score:
                scored.append((score, c))
        scored.sort(key=lambda x: -x[0])
        return [{"source": c["source"], "doc": c["doc"], "section": c["section"], "score": round(s, 2), "text": c["text"]}
                for s, c in scored[:k]]

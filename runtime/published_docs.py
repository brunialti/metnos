"""Canonical inventory of the HTML documents published on metnos.com.

The deployment root is also the Tutor publication boundary.  This module is
used both by the deploy preflight and by the Tutor knowledge compiler so a
public document is never registered twice in two independent inventories.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from html.parser import HTMLParser
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit, urlunsplit


REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLICATION_ROOT = REPO_ROOT / "docs"
PUBLIC_HOST = "metnos.com"
_MAX_DOCUMENTS = 512
_MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
_LANG = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{1,8})*$", re.IGNORECASE)
_ROBOTS_SPLIT = re.compile(r"[\s,]+")


@dataclass(frozen=True, slots=True)
class PublishedDocument:
    """One indexable HTML document admitted by the public deployment."""

    path: Path
    relative_path: str
    lang: str
    canonical_url: str
    concept_key: str

    @property
    def source_id(self) -> str:
        stem = re.sub(r"[^a-z0-9]+", "-", self.path.stem.casefold()).strip("-")
        stem = (stem or "document")[:32].rstrip("-")
        digest = hashlib.sha256(self.relative_path.encode("utf-8")).hexdigest()[:12]
        return f"public-{stem}-{digest}"


@dataclass(frozen=True, slots=True)
class _Head:
    lang: str
    canonical: str
    alternates: tuple[tuple[str, str], ...]
    robots: frozenset[str]
    refresh: bool


class _HeadParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lang = ""
        self.canonical = ""
        self.alternates: list[tuple[str, str]] = []
        self.robots: set[str] = set()
        self.refresh = False
        self._in_head = True

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.casefold()
        values = {
            str(key).casefold(): str(value or "").strip()
            for key, value in attrs
        }
        if tag == "html":
            self.lang = values.get("lang", "").casefold()
            return
        if tag == "body":
            self._in_head = False
            return
        if not self._in_head:
            return
        if tag == "meta":
            name = values.get("name", "").casefold()
            if name in {"robots", "googlebot"}:
                self.robots.update(
                    token for token in _ROBOTS_SPLIT.split(
                        values.get("content", "").casefold()) if token
                )
            if values.get("http-equiv", "").casefold() == "refresh":
                self.refresh = True
            return
        if tag != "link":
            return
        relations = set(values.get("rel", "").casefold().split())
        href = values.get("href", "")
        if "canonical" in relations:
            if self.canonical:
                raise ValueError("multiple canonical links")
            self.canonical = href
        if "alternate" in relations and values.get("hreflang"):
            self.alternates.append(
                (values["hreflang"].casefold(), href)
            )

    def result(self) -> _Head:
        return _Head(
            lang=self.lang,
            canonical=self.canonical,
            alternates=tuple(self.alternates),
            robots=frozenset(self.robots),
            refresh=self.refresh,
        )


def _public_url(raw: str, *, label: str) -> str:
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{label}: malformed URL {raw!r}") from exc
    if (parsed.scheme.casefold() != "https"
            or parsed.hostname is None
            or parsed.hostname.casefold() != PUBLIC_HOST
            or port is not None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not parsed.path.startswith("/")):
        raise ValueError(
            f"{label}: URL must be a canonical https://{PUBLIC_HOST} path"
        )
    return urlunsplit(("https", PUBLIC_HOST, parsed.path, "", ""))


def _read_head(path: Path) -> _Head:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ValueError(f"public document unavailable: {path}") from exc
    if size > _MAX_DOCUMENT_BYTES:
        raise ValueError(f"public document exceeds 2 MiB: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"public document is not valid UTF-8: {path}") from exc
    parser = _HeadParser()
    try:
        parser.feed(text)
    except ValueError as exc:
        raise ValueError(f"{path}: {exc}") from exc
    return parser.result()


def catalog(root: Path = PUBLICATION_ROOT) -> tuple[PublishedDocument, ...]:
    """Return every indexable HTML page in the exact deployment root.

    ``noindex`` pages are deployable redirects or utility pages, not Tutor
    evidence.  Every admitted page is validated before it can be deployed or
    compiled into the Tutor catalog.
    """

    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"publication root unavailable: {root}")
    candidates = sorted(root.rglob("*.html"), key=lambda item: item.as_posix())
    if len(candidates) > _MAX_DOCUMENTS:
        raise ValueError(
            f"publication contains more than {_MAX_DOCUMENTS} HTML documents"
        )

    rows: list[tuple[Path, str, _Head, str, dict[str, str]]] = []
    for path in candidates:
        if path.is_symlink():
            raise ValueError(f"public document may not be a symlink: {path}")
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError(f"public document escapes deployment root: {path}") from exc
        head = _read_head(resolved)
        if not _LANG.fullmatch(head.lang):
            raise ValueError(f"{relative}: missing or invalid html lang")
        canonical = _public_url(head.canonical, label=f"{relative} canonical")
        alternate_by_lang: dict[str, str] = {}
        for alternate_lang, raw_url in head.alternates:
            if alternate_lang != "x-default" and not _LANG.fullmatch(alternate_lang):
                raise ValueError(
                    f"{relative}: invalid hreflang {alternate_lang!r}"
                )
            if alternate_lang in alternate_by_lang:
                raise ValueError(
                    f"{relative}: duplicate hreflang {alternate_lang!r}"
                )
            alternate_by_lang[alternate_lang] = _public_url(
                raw_url, label=f"{relative} hreflang {alternate_lang}"
            )
        if "noindex" in head.robots:
            continue
        if head.refresh:
            raise ValueError(
                f"{relative}: an indexable document may not use meta refresh"
            )
        rows.append((resolved, relative, head, canonical, alternate_by_lang))

    canonical_rows: dict[str, tuple[Path, str, _Head, str, dict[str, str]]] = {}
    for row in rows:
        canonical = row[3]
        if canonical in canonical_rows:
            raise ValueError(
                f"duplicate public canonical URL: {canonical}"
            )
        canonical_rows[canonical] = row

    group_by_canonical: dict[str, frozenset[str]] = {}
    for _path, relative, head, canonical, alternates in rows:
        group = frozenset(
            {canonical}
            | {url for lang, url in alternates.items() if lang != "x-default"}
        )
        for lang, url in alternates.items():
            if lang == "x-default":
                continue
            target = canonical_rows.get(url)
            if target is None:
                raise ValueError(
                    f"{relative}: hreflang {lang!r} is not a published document"
                )
            if target[2].lang != lang:
                raise ValueError(
                    f"{relative}: hreflang {lang!r} points to lang "
                    f"{target[2].lang!r}"
                )
        if head.lang in alternates and alternates[head.lang] != canonical:
            raise ValueError(
                f"{relative}: its own hreflang does not match its canonical URL"
            )
        group_by_canonical[canonical] = group

    for _path, relative, _head, canonical, _alternates in rows:
        group = group_by_canonical[canonical]
        for member in group:
            if group_by_canonical.get(member) != group:
                raise ValueError(
                    f"{relative}: hreflang translations are not reciprocal"
                )

    documents: list[PublishedDocument] = []
    for path, relative, head, canonical, _alternates in rows:
        family = "\n".join(sorted(group_by_canonical[canonical]))
        concept_key = hashlib.sha256(family.encode("utf-8")).hexdigest()[:20]
        documents.append(PublishedDocument(
            path=path,
            relative_path=relative,
            lang=head.lang,
            canonical_url=canonical,
            concept_key=concept_key,
        ))
    return tuple(documents)


def _main(argv: list[str]) -> int:
    if argv not in (["validate"], []):
        print("usage: published_docs.py [validate]", file=sys.stderr)
        return 2
    try:
        documents = catalog()
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    languages = sorted({document.lang for document in documents})
    print(
        f"Public documentation valid: {len(documents)} indexable HTML "
        f"documents; languages={','.join(languages)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))

"""
post/citations.py  (research-paper metadata + citation copy)

Pure helpers, no DB: validate / normalise the metadata a research-paper resource carries and build the
APA 7 and MLA 9 reference text the app's "Copy citation" buttons put on the clipboard.

  title    -> Post.title          subject -> Post.subject (both already exist on the post)
  authors  -> ["Given Family", "Family, Given", ...]   (max MAX_AUTHORS, each max 120 chars)
  abstract -> free text (max 5000 chars)
  doi      -> stored WITHOUT the URL prefix: "10.1000/xyz123"   (link form: https://doi.org/10.1000/xyz123)
  link     -> any http(s) URL (publisher page, arXiv ...)
  year     -> 1800 .. current year + 1
  venue    -> journal / conference / publisher

`clean_paper(raw)` returns (clean_dict, None) or (None, PaperError). The Flutter side mirrors the formatting in
lib/post/models/paper_models.dart so "Copy citation" also works offline; keep both in step.
"""
import re
from datetime import date
from urllib.parse import urlparse

MAX_AUTHORS = 50
MAX_AUTHOR_LEN = 120
MAX_ABSTRACT = 5000
MAX_VENUE = 200
MIN_YEAR = 1800

_DOI_PREFIX = re.compile(r'^\s*(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', re.IGNORECASE)
_DOI_RE = re.compile(r'^10\.\d{4,9}/\S+$')
_SPACES = re.compile(r'\s+')


class PaperError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# ----------------------------------------------------------------------------- field cleaners
def normalize_doi(raw):
    """'https://doi.org/10.1000/ABC' / 'doi:10.1000/ABC' / '10.1000/ABC' -> '10.1000/ABC' ('' if not a DOI)."""
    if not raw:
        return ''
    doi = _DOI_PREFIX.sub('', str(raw)).strip().rstrip('.,;')
    return doi if _DOI_RE.match(doi) else ''


def doi_url(doi):
    return f'https://doi.org/{doi}' if doi else ''


def clean_link(raw):
    """http(s) URL or ''. Raises PaperError for something that is plainly not a web link."""
    link = (raw or '').strip()
    if not link:
        return ''
    if not re.match(r'^[a-z][a-z0-9+.-]*://', link, re.IGNORECASE):
        link = 'https://' + link
    parsed = urlparse(link)
    if parsed.scheme.lower() not in ('http', 'https') or not parsed.netloc or ' ' in link or len(link) > 500:
        raise PaperError('paper_link_invalid', 'The paper link is not a valid web address.')
    return link


def clean_authors(raw):
    """list (or JSON-ish / ';' / newline separated string) -> de-duplicated list of names, order kept."""
    if raw is None or raw == '':
        return []
    if isinstance(raw, str):
        parts = re.split(r'[;\n]+', raw)
    else:
        parts = list(raw)
    out, seen = [], set()
    for p in parts:
        name = _SPACES.sub(' ', str(p or '')).strip().strip(',')
        if not name:
            continue
        name = name[:MAX_AUTHOR_LEN]
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    if len(out) > MAX_AUTHORS:
        raise PaperError('too_many_authors', f'You can list at most {MAX_AUTHORS} authors.')
    return out


def clean_year(raw):
    if raw in (None, ''):
        return None
    try:
        year = int(str(raw).strip())
    except (TypeError, ValueError):
        raise PaperError('paper_year_invalid', 'The year must be a number like 2024.')
    if year < MIN_YEAR or year > date.today().year + 1:
        raise PaperError('paper_year_invalid', f'The year must be between {MIN_YEAR} and {date.today().year + 1}.')
    return year


def clean_paper(raw):
    """Validate the `paper` object of a create / edit request.

    Returns ({authors, abstract, doi, link, year, venue}, None) - every key always present - or (None, PaperError).
    An object with nothing filled in returns ({}, None): the caller then stores no paper row."""
    if not isinstance(raw, dict):
        return {}, None
    try:
        authors = clean_authors(raw.get('authors'))
        abstract = (raw.get('abstract') or '').strip()
        if len(abstract) > MAX_ABSTRACT:
            raise PaperError('abstract_too_long', f'The abstract can be at most {MAX_ABSTRACT} characters.')
        doi_raw = (raw.get('doi') or '').strip()
        doi = normalize_doi(doi_raw)
        if doi_raw and not doi:
            raise PaperError('paper_doi_invalid', 'That DOI does not look right. It should be like 10.1000/xyz123.')
        link = clean_link(raw.get('link'))
        year = clean_year(raw.get('year'))
        venue = _SPACES.sub(' ', (raw.get('venue') or '')).strip()[:MAX_VENUE]
    except PaperError as exc:
        return None, exc
    clean = {'authors': authors, 'abstract': abstract, 'doi': doi, 'link': link, 'year': year, 'venue': venue}
    if not any([authors, abstract, doi, link, year, venue]):
        return {}, None
    return clean, None


# ----------------------------------------------------------------------------- name handling
def _split_name(name):
    """'Given Middle Family' / 'Family, Given Middle' -> (family, [given words])."""
    name = name.strip()
    if ',' in name:
        family, _, given = name.partition(',')
        return family.strip(), given.split()
    words = name.split()
    if len(words) <= 1:
        return name, []
    return words[-1], words[:-1]


def _initials(given):
    out = []
    for g in given:
        parts = [p for p in re.split(r'[-.]', g) if p]
        out.extend(p[0].upper() + '.' for p in parts[:1] or [])
    return ' '.join(out)


def _apa_name(name):
    family, given = _split_name(name)
    ini = _initials(given)
    return f'{family}, {ini}' if ini else family


def _mla_first(name):
    family, given = _split_name(name)
    return f'{family}, {" ".join(given)}' if given else family


def _mla_other(name):
    family, given = _split_name(name)
    return f'{" ".join(given)} {family}' if given else family


def _end_dot(text):
    text = (text or '').strip()
    return text if text.endswith(('.', '?', '!')) else text + '.'


# ----------------------------------------------------------------------------- citations
def format_apa(title, paper):
    """APA 7:  Family, G. M., Family, G., & Family, G. (Year). Title. Venue. https://doi.org/..."""
    authors = [_apa_name(a) for a in paper.get('authors') or []]
    if not authors:
        who = ''
    elif len(authors) == 1:
        who = authors[0]
    elif len(authors) == 2:
        who = f'{authors[0]}, & {authors[1]}'
    elif len(authors) <= 20:
        who = ', '.join(authors[:-1]) + f', & {authors[-1]}'
    else:  # APA 7: first 19, an ellipsis, then the last author
        who = ', '.join(authors[:19]) + f', . . . {authors[-1]}'
    year = paper.get('year')
    when = f'({year})' if year else '(n.d.)'
    parts = []
    if who:
        parts.append(_end_dot(who))
    parts.append(f'{when}.')
    if title:
        parts.append(_end_dot(title))
    if paper.get('venue'):
        parts.append(_end_dot(paper['venue']))
    tail = doi_url(paper.get('doi')) or paper.get('link') or ''
    if tail:
        parts.append(tail)
    return ' '.join(parts)


def format_mla(title, paper):
    """MLA 9:  Family, Given, and Given Family. "Title." Venue, Year, doi.org/... (1 author / 2 authors / 3+ = et al.)"""
    authors = paper.get('authors') or []
    if not authors:
        who = ''
    elif len(authors) == 1:
        who = _end_dot(_mla_first(authors[0]))
    elif len(authors) == 2:
        who = _end_dot(f'{_mla_first(authors[0])}, and {_mla_other(authors[1])}')
    else:
        who = _end_dot(f'{_mla_first(authors[0])}, et al')
    parts = []
    if who:
        parts.append(who)
    if title:
        t = title.strip()
        t = t if t.endswith(('.', '?', '!')) else t + '.'
        parts.append(f'\u201c{t}\u201d')
    tail = []
    if paper.get('venue'):
        tail.append(paper['venue'].strip())
    if paper.get('year'):
        tail.append(str(paper['year']))
    ref = (doi_url(paper.get('doi')) or paper.get('link') or '')
    if ref:
        tail.append(re.sub(r'^https?://', '', ref))
    if tail:
        parts.append(_end_dot(', '.join(tail)))
    return ' '.join(parts)


def citations_for(title, paper):
    """{'apa': ..., 'mla': ...} for a paper dict (as returned by clean_paper / paper_payload)."""
    return {'apa': format_apa(title, paper), 'mla': format_mla(title, paper)}

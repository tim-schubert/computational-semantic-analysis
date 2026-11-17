"""
Text helpers: masking, verbatim and sentence checks, structured-abstract
sections.
"""
import re
import unicodedata
from difflib import SequenceMatcher

from config import MASK


# ---------------------------------------------------------------- entity masking
def mask_entity(text: str | None, mask_patterns: list[tuple[str, str]],
                mask: str = MASK) -> str | None:
    """Replace the paper's own substance with the mask token, for blinded scoring.

    mask_patterns: list of (regex, replacement), applied in order, so the
    most specific patterns (e.g. 'dopamine D2 receptor') must come first.
    """
    if text is None:
        return None
    out = text
    for pat, repl in mask_patterns:
        out = re.sub(pat, repl, out, flags=re.IGNORECASE)
    # collapse any accidental doubling like "[substance X] [substance X]"
    tok = re.escape(mask)
    out = re.sub(rf"({tok})(\s+{tok})+", r"\1", out)
    return out


_ABBR_DEF = re.compile(r"\(([A-Za-z][A-Za-z0-9-]{1,11})\)")


def _long_form(short: str, before: str) -> str | None:
    """The words just before "(short)" that `short` abbreviates, or None.

    Walking backwards, every letter and digit of the short form must occur in
    order in the long form, and its first character must start a word.
    """
    chars = [c.lower() for c in short if c.isalnum()]
    if short.endswith("s") and short[:-1].isupper():
        chars = chars[:-1]                    # plural
    if not chars:
        return None
    words = before.split()
    cand = " ".join(words[-min(len(chars) + 5, 2 * len(chars)):])
    i = len(cand) - 1
    for k in range(len(chars) - 1, -1, -1):
        c = chars[k]
        while i >= 0 and (cand[i].lower() != c or
                          (k == 0 and i > 0 and cand[i - 1].isalnum())):
            i -= 1
        if i < 0:
            return None
        i -= 1
    start = cand.rfind(" ", 0, i + 2) + 1
    return cand[start:].strip() or None


def substance_abbreviations(text: str | None,
                            mask_patterns: list[tuple[str, str]]
                            ) -> dict[str, tuple[str, str]]:
    """Mask patterns for abbreviations the text defines for a phrase that
    contains the substance.

    Returns {abbreviation: (regex, replacement)}. Each pattern replaces the
    abbreviation (case-sensitively, singular or plural) with its masked long
    form ("[substance X]-related genes"), so a span that names the substance
    only by such an abbreviation is masked and counts as naming it.
    Abbreviations the field's patterns already mask are left to
    them. These patterns go before the field's patterns.
    """
    out = {}
    for m in _ABBR_DEF.finditer(text or ""):
        short = m.group(1)
        if short in out or mask_entity(short, mask_patterns) != short:
            continue
        if sum(c.isupper() for c in short) < 2:
            continue                          # not an acronym
        long = _long_form(short, text[:m.start()])
        if not long:
            continue
        masked = mask_entity(long, mask_patterns)
        if masked == long:
            continue                          # the phrase is not the substance
        stem = short[:-1] if short.endswith("s") and short[:-1].isupper() \
            else short
        out[short] = (rf"(?-i:\b{re.escape(stem)}s?\b)", masked)
    return out


def mask_leaks(masked: str | None, roots: list[str]) -> list[str]:
    """Word roots of the paper's own substance that survived masking (should be [])."""
    low = (masked or "").lower()
    return [r for r in roots if r.lower() in low]


# ------------------------------------------------------------ text / matching
def normalize_text(s: str | None) -> str:
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", s).lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return s.strip()


def fuzzy_ratio(a: str, b: str) -> float:
    """0..1 similarity of two strings after normalization."""
    return SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio()


_SENT_END = re.compile(r"[.!?][\"')\]]?\s+(?=[A-Z(\[])")
_ABBREV = re.compile(r"\b(e\.g|i\.e|cf|vs|approx|Fig|et al|No|vol|Dr|Prof|"
                     r"[A-Z])\.\s*$")


def sentence_count(text: str | None) -> int:
    """How many sentences the span contains (abbreviation-aware). Step 04
    uses it to check the conclusion's sentence limit and that its main
    sentence is one sentence."""
    if not text:
        return 0
    n = 1
    for m in _SENT_END.finditer(text):
        if not _ABBREV.search(text[:m.start() + 1]):
            n += 1
    return n


# ------------------------------------------------------ structured abstracts
# A section label is a short run of known heading words at the start of the
# abstract or of a sentence, followed by ":" (e.g. "RESULTS:", "Subjects and
# methods:"). Requiring every word to be a heading word stops ordinary
# sentences that end in a colon from being read as labels. Without a colon,
# only a single core heading followed by a capital or digit counts
# ("Results In the ...", "Methods 109 participants").
_HEADING_WORDS = set("""
background introduction objective objectives aim aims purpose context rationale
hypothesis hypotheses importance question questions methods method materials
material design study setting settings participants patients subjects animals
intervention interventions measurements measurement main outcome outcomes
measures procedures procedure approach experimental data sources analysis
analyses results result findings finding conclusion conclusions
interpretation implications significance discussion relevance statement
summary trial registration key points and of the review recent level evidence
selection criteria collection limitations clinical basic principal
observations case presentation report keywords words funding methodology
authors synthesis integration extraction search strategy
""".split())
_CORE_HEADINGS = ("background|introduction|objectives?|aims?|methods?|results?|"
                  "findings|conclusions?|discussion|interpretation")
_LABEL = re.compile(
    r"(?:^|(?<=[.!?)\]]\s)|(?<=[.!?)\]]))"
    r"(?:(?P<colon>[A-Za-z][A-Za-z/&,'\- ]{1,60}?)\s*:\s*"
    r"|(?P<bare>(?:" + _CORE_HEADINGS + r"))\s+(?=(?-i:[A-Z0-9])))",
    re.IGNORECASE)


def _label_kind(label):
    words = re.split(r"[\s/&,\-]+", label.strip().lower().replace("'", ""))
    if not words or not all(w in _HEADING_WORDS for w in words if w):
        return None
    if "significance" in words and "statement" in words:
        return "other"
    if any(w.startswith(("result", "finding")) for w in words):
        return "results"
    if any(w in ("conclusion", "conclusions", "interpretation", "implications",
                 "significance", "discussion") for w in words):
        return "conclusions"
    return "other"


def section_labels(abstract: str | None) -> list[tuple[str, int, int]]:
    """[(kind, label_start, text_start)] for every section label found."""
    out = []
    for m in _LABEL.finditer(abstract or ""):
        if m.group("bare") and not m.group("bare")[0].isupper():
            continue
        kind = _label_kind(m.group("colon") or m.group("bare"))
        if kind:
            out.append((kind, m.start(), m.end()))
    return out


def leading_label_end(span: str | None) -> int:
    """Length of a section label at the very start of `span`, or 0."""
    labels = section_labels(span or "")
    return labels[0][2] if labels and labels[0][1] == 0 else 0


def split_structured(abstract: str) -> list[tuple[str, str]] | None:
    """Split a structured abstract into its labelled sections."""
    labels = section_labels(abstract)
    kinds = {k for k, _, _ in labels}
    if not {"results", "conclusions"} <= kinds:
        return None
    out = []
    for i, (kind, _, start) in enumerate(labels):
        end = labels[i + 1][1] if i + 1 < len(labels) else len(abstract)
        text = abstract[start:end].strip()
        if text:
            out.append((kind, text))
    return out


def names_agent(masked_span: str | None, mask: str) -> bool:
    """Does the masked span contain the mask token, i.e. name the substance?"""
    return bool(masked_span) and mask.lower() in masked_span.lower()


def is_verbatim(span: str, source: str,
                tolerance: float = 0.97) -> tuple[bool, float]:
    """Does `span` occur in `source`?

    Exact (normalised) substring match first; otherwise the best matching window
    of the same length, so trivial differences (a stripped citation marker, a
    unicode dash) still pass while an invented or paraphrased span fails.
    """
    s_norm, src_norm = normalize_text(span), normalize_text(source)
    if not s_norm:
        return False, 0.0
    if s_norm in src_norm:
        return True, 1.0
    n = len(s_norm)
    if n > len(src_norm):
        return False, fuzzy_ratio(s_norm, src_norm)
    best = 0.0
    step = max(1, n // 12)
    for i in range(0, len(src_norm) - n + 1, step):
        r = SequenceMatcher(None, s_norm, src_norm[i:i + n]).ratio()
        if r > best:
            best = r
            if best >= tolerance:
                break
    return best >= tolerance, best

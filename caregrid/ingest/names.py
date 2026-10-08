"""Cue-less person-name masking without Presidio/spaCy.

A first-name pool (Faker en_IN + en_US lists, plus a small curated Indian/staff supplement because Faker's en_IN list
misses common names such as Vikram, Asha, Kiran, Rahul) drives two rules:
  1. "<KnownFirstName> <Capitalized word>"            -> one person  ("Anita Rao wants ...")
  2. a standalone known first name that is followed by a possessive or a verb, or preceded by a cue word
     ("Vikram's queue", "Sara called", "assign to Vikram")
Business terms (team names, page titles, cities, ...) are excluded through a stopword set; names that are also common
English words are only masked by rule 1.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from functools import lru_cache

# Curated additions to Faker's lists: common Indian first names and the staff in users.csv.
SUPPLEMENT = """aarav aditya akash amit ananya anil anjali arjun arun asha ashok deepak divya gaurav geeta gopal harish harsh ishaan jaya
kavita kiran krishna lakshmi madhu mahesh manish meena meera mohan mukesh nandini naveen neeta neha nikhil nisha pooja prakash pranav
priyanka rahul rajesh rakesh ramesh ravi rekha rohan rohit sachin sanjana sanjay santosh shweta siddharth sneha sunil sunita suresh
sushma swati tanvi tarun uma usha varun vijay vikram vinod vivek yash""".split()

# Business vocabulary, places and sentence words that must never be read as a name part.
STOP_STATIC = frozenset("""provider providers enrollment operations operation triage service desk utilization management claims claim
compliance privacy clinical review senior team teams manager portal policy policies request requests address billing record records
document documents department division group office clinic hospital health care plan member patient network pharmacy
authorization prior access reset password login status update change changes name names number numbers email phone
road street lane avenue nagar colony marg cross boulevard layout block sector path chowk circle
chennai mumbai delhi bengaluru pune hyderabad kolkata jaipur austin denver texas california
monday tuesday wednesday thursday friday saturday sunday january february march april may june july august september october
november december
please thanks thank regards hello dear sir madam urgent kindly also then this that these those there here with from
period notice form forms standard general basic basics draft approval approved expired active stale
""".split())

# Names that are also everyday English words/places: masked only inside a "First Capitalized" pair, never standalone.
AMBIGUOUS = frozenset("""will mark grace rose chase hope joy faith dawn bill page art rich sue pat ray rod dean lane hunter summer winter
drew gene ward wade max sandy carol jay guy earl miles sky rain ivy lily amber crystal daisy heather holly iris jasmine olive pearl
ruby violet basil brooks cliff dale mason miller porter ross victoria france jordan paris florence austin sydney cody dallas
""".split())

_VERBS = ("wants|needs|called|calls|asked|asks|said|says|is|was|has|had|will|would|can|could|moved|moves|changed|emailed|"
          "requested|requests|submitted|sent|confirmed|reported|left|works|worked|lives|lived|signed|approved|rejected|wrote|"
          "phoned|messaged|replied|mentioned|noted|came|visited|wishes|got|did|may|might|should|must")
FOLLOWS = re.compile(r"(?:['’]s\b|\s+(?:" + _VERBS + r")\b)")
PRECEDED = re.compile(r"(?i)\b(?:to|for|from|by|with|contact|call|ask|assign(?:ed)?|forward(?:ed)?|cc|attn|attention|regarding|named|"
                      r"called|email|emailed)\s+$")
CAP_WORD = re.compile(r"(?<![\w\[\]-])[A-Z][a-z]+(?!\w)")
_GAP = re.compile(r"[ \t]{1,2}")


@lru_cache(maxsize=1)
def first_names() -> frozenset[str]:
    names: set[str] = set(SUPPLEMENT)
    try:
        from faker.providers.person import en_IN, en_US

        for mod in (en_IN, en_US):
            for attr in ("first_names", "first_names_male", "first_names_female", "first_names_nonbinary"):
                pool = getattr(mod.Provider, attr, None)
                if pool:
                    names.update(str(n).lower() for n in (pool.keys() if hasattr(pool, "keys") else pool))
    except Exception:  # Faker missing: the curated supplement still works
        pass
    return frozenset(n for n in names if len(n) >= 3 and n.isalpha())


def stop_terms_from_titles(*texts: str) -> set[str]:
    """Lower-cased alphabetic words (len>=3) from team names, page titles, field names, ..."""
    return {w.lower() for t in texts for w in re.findall(r"[A-Za-z]{3,}", t)}


def mask_cueless_names(s: str, stop: set[str] | frozenset[str], token: Callable[[str], str],
                       first_token: Callable[[str], str | None]) -> tuple[str, bool]:
    """Returns (masked, changed). `token(name)` mints/returns a stable token; `first_token(first)` returns the token
    of an already-seen full name starting with that first name (so 'Sara' after 'Sara Khan' is the same person)."""
    pool = first_names()
    toks = list(CAP_WORD.finditer(s))
    edits: list[tuple[int, int, str]] = []
    i = 0
    while i < len(toks):
        m = toks[i]
        word = m.group(0)
        lw = word.lower()
        if lw in pool and lw not in stop:
            if i + 1 < len(toks):
                nxt = toks[i + 1]
                if _GAP.fullmatch(s[m.end():nxt.start()]) and nxt.group(0).lower() not in stop:
                    edits.append((m.start(), nxt.end(), token(f"{word} {nxt.group(0)}")))
                    i += 2
                    continue
            if lw not in AMBIGUOUS and (FOLLOWS.match(s, m.end()) or PRECEDED.search(s[: m.start()])):
                edits.append((m.start(), m.end(), first_token(lw) or token(word)))
        i += 1
    for start, end, repl in sorted(edits, reverse=True):
        s = s[:start] + repl + s[end:]
    return s, bool(edits)

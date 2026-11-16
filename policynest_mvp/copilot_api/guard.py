"""Input and output guardrails shared by /api/copilot and /api/assistant. Standard library only."""
import re

# A 12-digit Aadhaar, a PAN, or a long (9+ digit) number such as a bank account. Digit groups may be separated by one
# space, hyphen or dot ("1234 5678 9012", "1234-5678-9012", "1234.5678.9012", "123-456-789-012"), so writing a number
# with separators does not get it past the filter. NUMBER_TEXT is the digits-only part (no PAN pattern).
_SEP = r"[\s.\-]?"
NUMBER_TEXT = re.compile(r"\b\d{4}" + _SEP + r"\d{4}" + _SEP + r"\d{4}\b|(?<!\d)\d(?:" + _SEP + r"\d){8,}(?!\d)")
ID_NUMBER = re.compile(NUMBER_TEXT.pattern + r"|\b[A-Za-z]{5}\d{4}[A-Za-z]\b")
# The rulebook's own PAN *format example* (AAAAA9999A: one repeated letter, one repeated digit) is not a real number.
_PLACEHOLDER = re.compile(r"^([A-Za-z])\1{4}(\d)\2{3}[A-Za-z]$")


def id_in(text):
    """True if the text holds a real-looking ID number. Format examples such as AAAAA9999A are allowed."""
    return any(not _PLACEHOLDER.match(m.group(0)) for m in ID_NUMBER.finditer(str(text)))


# Legacy combined filter (the older /api/copilot route): words, the 35% pass mark and outcome claims, unconditionally.
BANNED_OUT = re.compile(
    r"\b(fraud\w*|fake|forged|tamper\w*|suspicious)\b|\byou (are|have been) (approved|rejected)\b"
    r"|\byou (are|will be) (eligible|ineligible)\b|\byour application (is|will be|has been) (approved|rejected)\b|\bI (approve|reject)\b"
    r"|\byou (will|'ll|should|are likely to|are going to) (probably |definitely |certainly )?(be )?(pass|approved|rejected|get approved)\b"
    # the uploaded manual's 35% exam pass mark contradicts the rulebook and this app (50%): never state it
    r"|\bpass\w*[^.\n]{0,50}\b35\s?(%|percent)|\b35\s?(%|percent)[^.\n]{0,50}\bpass"
    r"|(फ़|फ)र्ज़ी|धोखाधड़ी|जालसाज़ी",
    re.I)

# Assistant filter: banned words and the wrong pass mark always; an outcome claim only when its sentence is not negated.
_WORDS = re.compile(
    r"\b(fraud\w*|fake|forged|tamper\w*|suspicious)\b"
    r"|\bpass\w*[^.\n]{0,50}\b35\s?(%|percent)|\b35\s?(%|percent)[^.\n]{0,50}\bpass"
    r"|(फ़|फ)र्ज़ी|धोखाधड़ी|जालसाज़ी", re.I)
_OUTCOME = re.compile(
    r"\byou (are|have been|will be|'ll be) (approved|rejected|eligible|ineligible)\b"
    r"|\byour application (is|will be|has been) (approved|rejected)\b|\bI (approve|reject)\b"
    r"|\byou (will|should|are likely to|are going to) (probably |definitely |certainly )?(be )?(pass|approved|rejected|get approved)\b"
    r"|\b(is|are|will be|has been|have been) (now )?(approved|rejected) by (me|us)\b", re.I)
_NEGATED = re.compile(r"\b(cannot|can't|can’t|can not|not able|unable|won't|don't|do not|doesn't|no one can)\b|नहीं", re.I)


def fraud_wording(text):
    """True if the text uses fraud wording (or the wrong pass mark). Used for reviewer messages to applicants,
    which may legitimately state an outcome but must never accuse anyone."""
    return bool(_WORDS.search(str(text or "")))


def violates(text):
    """True if the text uses banned words, the wrong pass mark, or claims an outcome in a sentence that is not negated."""
    t = str(text or "")
    if _WORDS.search(t):
        return True
    for sentence in re.split(r"(?<=[.!?।\n])\s+", t):
        if _OUTCOME.search(sentence) and not _NEGATED.search(sentence):
            return True
    return False


SAFE = ("I can't say how your application will be decided; the verification checks and our team do that. "
        "Ask me about your documents, the steps, or what to fix next.")
SAFE_HI = ("आपका आवेदन कैसे तय होगा, यह मैं नहीं बता सकता; यह सत्यापन जाँच और हमारी टीम तय करती है। "
           "आप दस्तावेज़ों, चरणों या आगे क्या ठीक करना है, इसके बारे में पूछ सकते हैं।")
REFUSED = "I can't help with that one. Ask me about your documents, the steps, or what to fix next."
REFUSED_HI = "इस बारे में मैं मदद नहीं कर सकता। आप अपने दस्तावेज़ों, चरणों या आगे क्या ठीक करना है, इसके बारे में पूछ सकते हैं।"
INJECTION = re.compile(
    r"ignore (all |any |the |your |previous |above )*(instructions|rules|prompt)|disregard (all |the |your |previous )*(instructions|rules)"
    r"|system prompt|developer message|your (hidden |secret )?(instructions|prompt)|another applicant|other applicant|someone else'?s (application|documents|details)"
    r"|reveal.*(secret|password|token|key)|pretend (you are|to be)|jailbreak|\bDAN\b|act as (an? )?(unrestricted|admin|reviewer)"
    r"|पिछले (सभी )?निर्देश|सिस्टम प्रॉम्प्ट", re.I)

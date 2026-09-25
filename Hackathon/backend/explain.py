"""
explain.py -- turns a recommendation into a friendly sentence, in the farmer's
own language.

The main function is describe(option, language). It sends a prompt to a local
Ollama server and asks the qwen2.5:3b model for one or two plain-language
sentences in the chosen language.

Ollama is a program that runs large language models on your own computer:
    https://ollama.com
    ollama pull qwen2.5:3b     # download the model once
    (then just leave Ollama running in the background)

If Ollama is not running, is too slow, answers in the wrong language, or returns
something strange, we fall back to fallback_sentence(), which writes a sentence
in the same language from the numbers themselves. The app therefore always has
an explanation to show, in every supported language.

TIP: the 3-billion-parameter model is fine in English but weak in Swahili, Hindi
and Urdu. For a real deployment try a bigger or more multilingual model, e.g.
`ollama pull qwen2.5:7b` and set OLLAMA_MODEL=qwen2.5:7b.
"""

import os

import engine  # for normalize_language() and the list of supported languages

try:
    import requests  # listed in requirements.txt
except ImportError:  # pragma: no cover - only happens before pip install
    requests = None

# ---------------------------------------------------------------------------
# SETTINGS (change these with environment variables if you like)
# ---------------------------------------------------------------------------
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")

# A local 3-billion-parameter model needs a few seconds per sentence. If it takes
# longer than this we give up and use the template instead.
OLLAMA_TIMEOUT_SECONDS = int(os.environ.get("OLLAMA_TIMEOUT", "25"))

# Set MAZRAA_NO_LLM=1 to skip Ollama completely. Handy if your laptop is busy,
# or if you want the fastest possible demo.
USE_LLM = os.environ.get("MAZRAA_NO_LLM") != "1"

# The languages we can write explanations in. The codes must match
# engine.SUPPORTED_LANGUAGES and frontend/i18n.js.
LANGUAGE_NAMES = {
    "en": "English",
    "ar": "Arabic",
    "fr": "French",
    "es": "Spanish",
    "sw": "Swahili",
    "hi": "Hindi",
    "ur": "Urdu",
}
assert set(LANGUAGE_NAMES) == set(engine.SUPPORTED_LANGUAGES), (
    "explain.LANGUAGE_NAMES and engine.SUPPORTED_LANGUAGES are out of sync"
)

# Instructions for the model, always written in English (models follow English
# instructions most reliably), plus one line asking for the answer language.
BASE_SYSTEM_PROMPT = (
    "You are an agronomy advisor writing for a smallholder farmer. "
    "Use simple, everyday language. "
    "Answer in ONE OR TWO short sentences. "
    "Use ONLY the numbers given to you -- never invent a number, a price or a fact. "
    "Do not use bullet points, headings or markdown. "
    "Do not repeat the question."
)

# ---------------------------------------------------------------------------
# THE FALLBACK SENTENCES (one set per language)
# ---------------------------------------------------------------------------
# "template" builds the sentence; the three "payback_*" strings describe how the
# investment is paid back, so the sentence stays grammatical in every language.
# The {placeholders} are filled in by fallback_sentence() below.
TEMPLATES = {
    "en": {
        "template": (
            "{crop} in a {system} scores {score} out of 100 here, producing about "
            "{harvest} tonnes a year on {area} hectares, using {water} cubic metres "
            "of water and {energy} kWh of electricity. Start-up cost is about {capex} "
            "US dollars with {opex} dollars a year to run, so {payback}."
        ),
        "payback_never": "it does not pay back its start-up cost at the current yield and price",
        "payback_under_year": "the start-up cost is paid back in under a year",
        "payback_years": "the start-up cost is paid back in about {n} years",
    },
    "ar": {
        "template": (
            "{crop} بنظام {system} يحصل على {score} من 100 هنا، وينتج نحو {harvest} طن "
            "في السنة على مساحة {area} هكتار، باستهلاك {water} متر مكعب من المياه "
            "و{energy} كيلوواط ساعة من الكهرباء. التكلفة الأولية نحو {capex} دولار "
            "أمريكي وتكاليف التشغيل {opex} دولار في السنة، و{payback}."
        ),
        "payback_never": "التكلفة الأولية لا تُسترد بالإنتاج والسعر الحاليين",
        "payback_under_year": "التكلفة الأولية تُسترد في أقل من سنة",
        "payback_years": "التكلفة الأولية تُسترد خلال نحو {n} سنة",
    },
    "fr": {
        "template": (
            "{crop}, système « {system} » : {score} sur 100 ici, environ {harvest} "
            "tonnes par an sur {area} hectares, {water} mètres cubes d'eau et "
            "{energy} kWh d'électricité. L'investissement de départ est d'environ "
            "{capex} dollars US, les charges de {opex} dollars par an, donc {payback}."
        ),
        "payback_never": "il ne rembourse pas son investissement avec ce rendement et ce prix",
        "payback_under_year": "l'investissement est remboursé en moins d'un an",
        "payback_years": "l'investissement est remboursé en environ {n} ans",
    },
    "es": {
        "template": (
            "{crop} con el sistema {system} obtiene {score} sobre 100 aquí, con unas "
            "{harvest} toneladas al año en {area} hectáreas, {water} metros cúbicos "
            "de agua y {energy} kWh de electricidad. La inversión inicial es de unos "
            "{capex} dólares y los gastos de {opex} dólares al año, así que {payback}."
        ),
        "payback_never": "no recupera la inversión con esta producción y este precio",
        "payback_under_year": "la inversión se recupera en menos de un año",
        "payback_years": "la inversión se recupera en unos {n} años",
    },
    "sw": {
        "template": (
            "{crop} kwa mfumo wa {system} inapata {score} kati ya 100 hapa, ikitoa "
            "takriban tani {harvest} kwa mwaka kwenye hekta {area}, kwa kutumia mita "
            "za ujazo {water} za maji na kWh {energy} za umeme. Gharama ya kuanzisha "
            "ni takriban dola {capex} na gharama za mwaka dola {opex}, hivyo {payback}."
        ),
        "payback_never": "haitarejeshi gharama yake ya kuanzisha kwa mavuno na bei hizi",
        "payback_under_year": "gharama ya kuanzisha inarejeshwa ndani ya mwaka mmoja",
        "payback_years": "gharama ya kuanzisha inarejeshwa baada ya miaka {n}",
    },
    "hi": {
        "template": (
            "{system} प्रणाली में {crop} को यहाँ 100 में से {score} अंक मिलते हैं: "
            "{area} हेक्टेयर पर सालाना लगभग {harvest} टन उपज, {water} घन मीटर पानी और "
            "{energy} kWh बिजली। शुरुआती लागत करीब {capex} डॉलर और सालाना खर्च {opex} "
            "डॉलर है, इसलिए {payback}।"
        ),
        "payback_never": "इस उपज और भाव पर शुरुआती लागत वसूल नहीं होती",
        "payback_under_year": "शुरुआती लागत एक साल से कम में वसूल हो जाती है",
        "payback_years": "शुरुआती लागत लगभग {n} साल में वसूल होती है",
    },
    "ur": {
        "template": (
            "{system} نظام میں {crop} کو یہاں 100 میں سے {score} ملتے ہیں: {area} ہیکٹر "
            "پر سالانہ تقریباً {harvest} ٹن پیداوار، {water} مکعب میٹر پانی اور "
            "{energy} kWh بجلی۔ ابتدائی لاگت تقریباً {capex} ڈالر اور سالانہ خرچ "
            "{opex} ڈالر ہے، اس لیے {payback}۔"
        ),
        "payback_never": "ابتدائی لاگت اس پیداوار اور قیمت پر واپس نہیں ہوتی",
        "payback_under_year": "ابتدائی لاگت ایک سال سے کم میں واپس ہو جاتی ہے",
        "payback_years": "ابتدائی لاگت تقریباً {n} سال میں واپس ہوتی ہے",
    },
}

# Languages that use a non-Latin script. If the model forgets and answers in
# English, we can notice it and use the template instead.
SCRIPT_RANGES = {
    "ar": (0x0600, 0x06FF),   # Arabic block (also covers Urdu letters)
    "ur": (0x0600, 0x06FF),
    "hi": (0x0900, 0x097F),   # Devanagari
}

# How each language writes numbers, as (thousands separator, decimal separator).
# A French farmer reads "1 234,5" and a Spanish farmer reads "1.234,5", so the
# sentences in those languages use their own punctuation.
NUMBER_SEPARATORS = {
    "en": (",", "."),
    "ar": (",", "."),
    "fr": ("\u202f", ","),   # narrow no-break space, as French typography wants
    "es": (".", ","),
    "sw": (",", "."),
    "hi": (",", "."),
    "ur": (",", "."),
}


# ---------------------------------------------------------------------------
# PUBLIC FUNCTIONS
# ---------------------------------------------------------------------------
def describe(option, language=engine.DEFAULT_LANGUAGE):
    """
    Explain one recommendation in one or two sentences.

    Parameters
    ----------
    option : dict
        The recommendation dictionary built by app.py. It should contain at
        least: crop_name, system_name, suitability, yield_tonnes, water_m3,
        energy_kwh, capex_usd, opex_usd, revenue_usd, payback_years, area_ha.
    language : str
        A code like "en", "ar", "fr"... Anything unknown becomes English.

    Returns
    -------
    str
        A sentence from the local AI model, or a template sentence if the model
        is unavailable or answers in the wrong language.
    """
    language = engine.normalize_language(language)

    if not USE_LLM or requests is None:
        return fallback_sentence(option, language)

    prompt = build_prompt(option, language)
    try:
        text = _tidy(_ask_ollama(prompt, language))
        if not text:
            print("[explain.py] Ollama returned an empty answer, using the template.")
        elif not _looks_like_language(text, language):
            print("[explain.py] Ollama answered in the wrong language (%s), "
                  "using the template." % language)
        else:
            return text
    except Exception as exc:
        print("[explain.py] Ollama unavailable (%s). Using the template sentence."
              % _short_error(exc))
    return fallback_sentence(option, language)


def build_prompt(option, language=engine.DEFAULT_LANGUAGE):
    """
    Build the text we send to the model.

    We list the numbers explicitly and tell the model to use nothing else, which
    is the simplest way to stop a small model from making things up. The data
    lines stay in English (models follow that reliably); only the answer language
    changes.
    """
    language = engine.normalize_language(language)

    payback = option.get("payback_years")
    if payback is None:
        payback_text = "it never pays back its investment with these numbers"
    else:
        payback_text = "about %s years" % _number(payback, 1)

    return (
        "A farm of {area} hectares was suggested for {crop} grown using the "
        "{system} system.\n"
        "Suitability score: {score} out of 100.\n"
        "Expected harvest: {harvest} tonnes per year.\n"
        "Water use: {water} cubic metres per year.\n"
        "Energy use: {energy} kilowatt-hours per year.\n"
        "Start-up cost (CapEx): {capex} US dollars.\n"
        "Running cost (OpEx): {opex} US dollars per year.\n"
        "Income: {revenue} US dollars per year.\n"
        "Payback time: {payback}.\n"
        "\n"
        "In one or two sentences, explain to the farmer why this recommendation "
        "makes sense (or what its main trade-off is), using only these numbers."
    ).format(
        area=_number(option.get("area_ha"), 2),
        crop=option.get("crop_name", option.get("crop", "this crop")),
        system=option.get("system_name", option.get("system", "this system")),
        score=_number(float(option.get("suitability", 0.0)) * 100.0, 1),
        harvest=_number(option.get("yield_tonnes"), 1),
        water=_number(option.get("water_m3"), 0),
        energy=_number(option.get("energy_kwh"), 0),
        capex=_number(option.get("capex_usd"), 0),
        opex=_number(option.get("opex_usd"), 0),
        revenue=_number(option.get("revenue_usd"), 0),
        payback=payback_text,
    )


def fallback_sentence(option, language=engine.DEFAULT_LANGUAGE):
    """
    A plain sentence built from the numbers, in the chosen language.

    This is never "wrong": every number here comes straight from the
    recommendation, and the grammar comes from a template a human wrote, so it is
    a safe thing to show to a farmer or to judges.

    It is also the sentence used when the small AI model is not installed, so it
    has to be complete on its own.
    """
    language = engine.normalize_language(language)
    words = TEMPLATES[language]

    # Every number in this sentence is formatted the local way.
    def num(value, decimals=1):
        return _number(value, decimals, language)

    payback = option.get("payback_years")
    if payback is None:
        payback_text = words["payback_never"]
    elif payback < 1:
        payback_text = words["payback_under_year"]
    else:
        payback_text = words["payback_years"].format(n=num(payback, 1))

    return words["template"].format(
        crop=option.get("crop_name", option.get("crop", "?")),
        system=option.get("system_name", option.get("system", "?")),
        score=num(float(option.get("suitability", 0.0)) * 100.0, 1),
        harvest=num(option.get("yield_tonnes"), 1),
        area=num(option.get("area_ha"), 2),
        water=num(option.get("water_m3"), 0),
        energy=num(option.get("energy_kwh"), 0),
        capex=num(option.get("capex_usd"), 0),
        opex=num(option.get("opex_usd"), 0),
        payback=payback_text,
    )


# ---------------------------------------------------------------------------
# OLLAMA
# ---------------------------------------------------------------------------
def _ask_ollama(prompt, language):
    """Send one prompt to Ollama and return the text it produced."""
    response = requests.post(
        OLLAMA_URL.rstrip("/") + "/api/generate",
        json={
            "model": OLLAMA_MODEL,
            "system": _system_prompt(language),
            "prompt": prompt,
            "stream": False,       # wait for the whole answer at once
            "options": {"temperature": 0.2},  # low = calm, repeatable wording
        },
        timeout=OLLAMA_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    return (payload.get("response") or "").strip()


def _system_prompt(language):
    """The instructions for the model, including which language to answer in."""
    language_name = LANGUAGE_NAMES[language]
    return BASE_SYSTEM_PROMPT + " Write your whole answer in %s." % language_name


# ---------------------------------------------------------------------------
# SMALL HELPERS
# ---------------------------------------------------------------------------
def _number(value, decimals=1, language=engine.DEFAULT_LANGUAGE):
    """
    Format a number the way the chosen language writes numbers, or return '?'.

    English: 1,234.5    French: 1 234,5    Spanish: 1.234,5
    """
    if value is None:
        return "?"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "?"

    if decimals <= 0:
        text = "{:,.0f}".format(number)
    else:
        text = "{:,.{d}f}".format(number, d=decimals)

    thousands, decimal = NUMBER_SEPARATORS.get(language, (",", "."))
    if (thousands, decimal) != (",", "."):
        # Swap the separators. The temporary \x00 marker stops the thousands
        # separator from being eaten by the decimal swap.
        text = text.replace(",", "\x00").replace(".", decimal).replace("\x00", thousands)
    return text


def _tidy(text):
    """Clean up small-model habits: stray quotes, label prefixes, line breaks."""
    text = (text or "").strip().strip('"').strip()
    text = " ".join(text.split())  # collapse newlines and double spaces
    for prefix in ("Answer:", "Explanation:", "Sure,", "Sure!,"):
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix):].strip()
    # Keep it short: cut at the third sentence at the very most.
    sentences = text.split(". ")
    if len(sentences) > 3:
        text = ". ".join(sentences[:3]).rstrip(".") + "."
    return text


def _looks_like_language(text, language):
    """
    True when the answer seems to be written in a non-Latin script language.

    A small model sometimes ignores the instruction and answers in English. For
    Arabic, Urdu and Hindi we can check whether any letter comes from the
    expected alphabet; if not, the caller uses the template instead, so a farmer
    never sees an English sentence in the middle of an Arabic page.
    """
    script_range = SCRIPT_RANGES.get(language)
    if script_range is None:
        return True  # French, Spanish and Swahili all use the Latin alphabet
    low, high = script_range
    return any(low <= ord(character) <= high for character in text)


def _short_error(exc):
    """A one-line version of an error message."""
    message = str(exc).strip() or exc.__class__.__name__
    return message if len(message) <= 120 else message[:117] + "..."


# Running this file on its own tests Ollama quickly:
#     python explain.py            # English
#     python explain.py ar         # Arabic
if __name__ == "__main__":
    import sys

    example = {
        "crop_name": "Tomato",
        "system_name": "Greenhouse",
        "area_ha": 2.0,
        "suitability": 0.88,
        "yield_tonnes": 180.4,
        "water_m3": 5400.0,
        "energy_kwh": 6300.0,
        "capex_usd": 244000.0,
        "opex_usd": 45000.0,
        "revenue_usd": 126280.0,
        "payback_years": 3.0,
    }

    # Same console-encoding guard as app.py, because this script prints Arabic,
    # Hindi and Urdu text.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    for code in engine.SUPPORTED_LANGUAGES:
        option = dict(example)
        option["crop_name"] = engine.crop_name("tomato", code)
        option["system_name"] = engine.system_name("greenhouse", code)
        print("[%s] template: %s" % (code, fallback_sentence(option, code)))
    print()
    print("Ollama:", describe(example, "en"))

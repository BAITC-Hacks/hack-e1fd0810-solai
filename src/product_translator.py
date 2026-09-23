"""Display-only product-name translation with Google Cloud and local caching."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from pathlib import Path
from urllib.request import Request, urlopen


API_URL = "https://translation.googleapis.com/language/translate/v2"
LANGUAGE_CODES = {"en": "en", "ru": "ru", "kz": "kk", "kk": "kk"}
_CACHE_LOCK = threading.Lock()

# Known official electrical brands are protected from machine translation.
# Technical/model tokens are also detected generically below.
_BRANDS = (
    "Schneider Electric", "Systeme Electric", "Phoenix Contact",
    "IEK", "ABB", "Legrand", "EKF", "CHINT", "Siemens", "Eaton",
    "Hager", "WAGO", "DEKraft", "DKC",
)
_TECHNICAL_TOKEN = re.compile(
    r"(?<!\w)(?=[A-Za-zА-Яа-яЁёІіҚқӘәҒғҢңӨөҰұҮүҺһ0-9._/+%-]*\d)"
    r"[A-Za-zА-Яа-яЁёІіҚқӘәҒғҢңӨөҰұҮүҺһ0-9][A-Za-zА-Яа-яЁёІіҚқӘәҒғҢңӨөҰұҮүҺһ0-9._/+%-]*(?!\w)"
)


def _cache_path() -> Path:
    """Use the OS user cache directory, never the project/repository directory."""
    root = os.environ.get("LOCALAPPDATA")
    if root:
        return Path(root) / "Solai" / "product_translations.sqlite3"
    root = os.environ.get("XDG_CACHE_HOME")
    return (Path(root) if root else Path.home() / ".cache") / "solai" / "product_translations.sqlite3"


def _cache_read(path: Path, original: str, language: str) -> str | None:
    try:
        db = sqlite3.connect(path, timeout=2)
        try:
            row = db.execute(
                "SELECT translated FROM translations WHERE original = ? AND language = ?",
                (original, language),
            ).fetchone()
        finally:
            db.close()
        return row[0] if row and row[0] else None
    except (OSError, sqlite3.Error):
        return None


def _cache_write(path: Path, original: str, language: str, translated: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path, timeout=2)
        try:
            db.execute(
                "CREATE TABLE IF NOT EXISTS translations ("
                "original TEXT NOT NULL, language TEXT NOT NULL, translated TEXT NOT NULL, "
                "PRIMARY KEY (original, language))"
            )
            db.execute(
                "INSERT OR REPLACE INTO translations(original, language, translated) VALUES (?, ?, ?)",
                (original, language, translated),
            )
            db.commit()
        finally:
            db.close()
    except (OSError, sqlite3.Error):
        # Cache failures must never prevent the dashboard from rendering.
        pass


def _already_in_language(text: str, language: str) -> bool:
    cyrillic = sum("\u0400" <= char <= "\u052f" for char in text)
    latin = sum("A" <= char <= "Z" or "a" <= char <= "z" for char in text)
    if language == "en":
        return latin > 0 and latin >= cyrillic
    if cyrillic <= latin:
        return False
    kazakh_letters = set("ӘәҒғҚқҢңӨөҰұҮүҺһІі")
    has_kazakh = any(char in kazakh_letters for char in text)
    return has_kazakh if language in {"kk", "kz"} else not has_kazakh


def _protect_technical_terms(text: str) -> tuple[str, dict[str, str]]:
    terms = sorted(_BRANDS, key=len, reverse=True)
    terms.extend(match.group(0) for match in _TECHNICAL_TOKEN.finditer(text))
    # Match brands as phrases and protect technical terms as whole tokens.
    unique_terms = sorted(set(terms), key=len, reverse=True)
    if not unique_terms:
        return text, {}
    pattern = re.compile(
        "|".join(re.escape(term) for term in unique_terms), re.IGNORECASE
    )
    protected: dict[str, str] = {}

    def replace(match: re.Match) -> str:
        marker = f"ZXQPROTECTED{len(protected):04d}XZQ"
        protected[marker] = match.group(0)
        return marker

    return pattern.sub(replace, text), protected


def _google_translate_many(texts: list[str], language: str, api_key: str, timeout: float = 8) -> list[str]:
    protected_rows = [_protect_technical_terms(text) for text in texts]
    request = Request(
        API_URL,
        data=json.dumps({"q": [row[0] for row in protected_rows], "target": LANGUAGE_CODES[language], "format": "text"}).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8", "X-goog-api-key": api_key},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    translations = payload["data"]["translations"]
    if len(translations) != len(texts):
        raise ValueError("Translation service returned an unexpected result count")
    results = []
    for source, result, (_, protected) in zip(texts, translations, protected_rows):
        translated = result["translatedText"]
        # Google may HTML-escape response text. Decode only standard entities.
        translated = (translated.replace("&amp;", "&").replace("&lt;", "<")
                      .replace("&gt;", ">").replace("&#39;", "'").replace("&quot;", '"'))
        for marker, original_term in protected.items():
            if marker not in translated:
                # Preserve the source rather than return a translation that altered a code.
                translated = source
                break
            translated = translated.replace(marker, original_term)
        results.append(translated.strip() or source)
    return results


def _translate_names(
    names, target_language: str, *, api_key: str | None = None, cache_path: Path | None = None
) -> dict[str, str]:
    originals = list(dict.fromkeys(str(value).strip() for value in names if value is not None and str(value).strip()))
    language = (target_language or "en").lower()
    results = {name: name for name in originals}
    if language not in {"en", "ru", "kz", "kk"}:
        return results
    cache_language = language
    cache = cache_path or _cache_path()
    misses = []
    for name in originals:
        if _already_in_language(name, language):
            continue
        cached = _cache_read(cache, name, cache_language)
        if cached:
            results[name] = cached
        else:
            misses.append(name)
    key = api_key or os.environ.get("TRANSLATION_API_KEY")
    if not key or not misses:
        return results

    # Google Translation v2 accepts an array of q values. Batch at most 100
    # unique names per request (well below the service's per-request text cap).
    with _CACHE_LOCK:
        pending = []
        for name in misses:
            cached = _cache_read(cache, name, cache_language)
            if cached:
                results[name] = cached
            else:
                pending.append(name)
        for offset in range(0, len(pending), 100):
            batch = pending[offset:offset + 100]
            try:
                translated_batch = _google_translate_many(batch, language, key)
            except Exception:
                continue
            for original, translated in zip(batch, translated_batch):
                results[original] = translated or original
                _cache_write(cache, original, cache_language, results[original])
    return results


def translate_product_name(
    product_name: object,
    target_language: str,
    *,
    api_key: str | None = None,
    cache_path: Path | None = None,
) -> str:
    """Translate a display name; always fall back to the source on failure.

    API key configuration: ``TRANSLATION_API_KEY``. Google auto-detects the
    source language. Original names remain unchanged in application data.
    """
    original = "" if product_name is None else str(product_name).strip()
    if not original:
        return {"ru": "Неизвестный товар", "kz": "Белгісіз тауар", "kk": "Белгісіз тауар"}.get((target_language or "en").lower(), "Unknown product")
    return _translate_names([original], target_language, api_key=api_key, cache_path=cache_path).get(original, original)


def translate_product_names(names, target_language: str) -> dict[str, str]:
    """Translate unique non-empty values in batches and return a display map."""
    return _translate_names(names, target_language)

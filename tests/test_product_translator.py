import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.data_loader import load_sample_data
from src.forecasting import forecast_demand
from src.replenishment import calculate_replenishment
from src.product_translator import translate_product_name, translate_product_names


class FakeResponse:
    def __init__(self, translations):
        self.payload = json.dumps({"data": {"translations": [
            {"translatedText": value} for value in translations
        ]}}).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.payload


class ProductTranslatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cache = Path(self.temp.name) / "translations.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def response_for(self, func):
        def fake_urlopen(request, timeout):
            payload = json.loads(request.data.decode("utf-8"))
            return FakeResponse([func(value, payload["target"]) for value in payload["q"]])
        return fake_urlopen

    def test_russian_to_english_and_new_name(self):
        with patch("src.product_translator.urlopen", side_effect=self.response_for(lambda _q, _target: "Cold Orange Juice")) as api:
            value = translate_product_name("Холодный апельсиновый сок", "en", api_key="test", cache_path=self.cache)
        self.assertEqual(value, "Cold Orange Juice")

    def test_russian_to_kazakh_uses_google_language_code(self):
        def translate(_q, target):
            self.assertEqual(target, "kk")
            return "Салқын апельсин шырыны"
        with patch("src.product_translator.urlopen", self.response_for(translate)):
            value = translate_product_name("Холодный апельсиновый сок", "kz", api_key="test", cache_path=self.cache)
        self.assertEqual(value, "Салқын апельсин шырыны")

    def test_english_to_russian_and_kazakh(self):
        with patch("src.product_translator.urlopen", self.response_for(lambda _q, target: {"ru": "Выключатель", "kk": "Ажыратқыш"}[target])):
            ru = translate_product_name("Circuit Breaker", "ru", api_key="test", cache_path=self.cache)
            kz = translate_product_name("Circuit Breaker", "kz", api_key="test", cache_path=self.cache)
        self.assertEqual((ru, kz), ("Выключатель", "Ажыратқыш"))

    def test_cache_reuses_translation_across_calls(self):
        with patch("src.product_translator.urlopen", side_effect=self.response_for(lambda _q, _target: "Cold Orange Juice")) as api:
            first = translate_product_name("Холодный апельсиновый сок", "en", api_key="test", cache_path=self.cache)
            second = translate_product_name("Холодный апельсиновый сок", "en", api_key="test", cache_path=self.cache)
        self.assertEqual(first, second)
        self.assertEqual(api.call_count, 1)

    def test_original_language_skips_api(self):
        with patch("src.product_translator.urlopen") as api:
            self.assertEqual(translate_product_name("Холодный апельсиновый сок", "ru", cache_path=self.cache), "Холодный апельсиновый сок")
            self.assertEqual(translate_product_name("Cold Orange Juice", "en", cache_path=self.cache), "Cold Orange Juice")
            api.assert_not_called()

    def test_language_switch_uses_distinct_cached_targets(self):
        def translated(_q, target):
            return {"en": "Cold Orange Juice", "ru": "Холодный апельсиновый сок", "kk": "Салқын апельсин шырыны"}[target]
        with patch("src.product_translator.urlopen", side_effect=self.response_for(translated)) as api:
            values = [translate_product_name("Холодный апельсиновый сок", lang, api_key="test", cache_path=self.cache) for lang in ("en", "ru", "kz", "en", "ru", "kz")]
        self.assertEqual(values[0], "Cold Orange Juice")
        self.assertEqual(values[1], "Холодный апельсиновый сок")
        self.assertEqual(values[2], "Салқын апельсин шырыны")
        self.assertEqual(api.call_count, 2)  # RU is recognized as already in Russian.

    def test_technical_brand_model_sku_and_rating_are_restored(self):
        original = "Автоматический выключатель Schneider Electric Acti9 iC60N 16A SKU-123"
        def machine_text(q, _target):
            return q.replace("Автоматический выключатель", "Circuit Breaker")
        with patch("src.product_translator.urlopen", self.response_for(machine_text)):
            value = translate_product_name(original, "en", api_key="test", cache_path=self.cache)
        self.assertIn("Schneider Electric", value)
        self.assertIn("Acti9", value)
        self.assertIn("iC60N", value)
        self.assertIn("16A", value)
        self.assertIn("SKU-123", value)

    def test_service_failure_falls_back_to_original(self):
        with patch("src.product_translator.urlopen", side_effect=OSError("offline")):
            value = translate_product_name("Холодный апельсиновый сок", "en", api_key="test", cache_path=self.cache)
        self.assertEqual(value, "Холодный апельсиновый сок")

    def test_missing_key_falls_back_to_original(self):
        with patch.dict(os.environ, {}, clear=True), patch("src.product_translator.urlopen") as api:
            value = translate_product_name("Холодный апельсиновый сок", "en", cache_path=self.cache)
        self.assertEqual(value, "Холодный апельсиновый сок")
        api.assert_not_called()

    def test_five_thousand_rows_are_batched_as_unique_names(self):
        names = [f"Новый товар {index % 100}" for index in range(5000)]
        with patch.dict(os.environ, {"TRANSLATION_API_KEY": "test"}), patch(
            "src.product_translator._cache_path", return_value=self.cache
        ), patch("src.product_translator.urlopen", side_effect=self.response_for(lambda q, _target: q)) as api:
            translated = translate_product_names(names, "en")
        self.assertEqual(len(translated), 100)
        self.assertEqual(api.call_count, 1)
        request_payload = json.loads(api.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(len(request_payload["q"]), 100)

    def test_translations_are_display_only_and_calculations_stay_identical(self):
        data = load_sample_data(Path(__file__).resolve().parents[1] / "data" / "sample_data.csv")
        original_names = data["product_name"].copy(deep=True)
        forecasts, audit = forecast_demand(data)
        expected = calculate_replenishment(forecasts, audit)
        expected = expected[["sku", "forecast_demand", "recommended_order_qty", "status"]]
        with patch.dict(os.environ, {"TRANSLATION_API_KEY": "test"}), patch(
            "src.product_translator._cache_path", return_value=self.cache
        ), patch("src.product_translator.urlopen", side_effect=self.response_for(lambda q, target: f"{target}: {q}")):
            for language in ("en", "ru", "kz"):
                display_names = translate_product_names(data["product_name"].unique(), language)
                self.assertEqual(len(display_names), data["product_name"].nunique())
                repeated = calculate_replenishment(forecasts, audit)
                pd.testing.assert_frame_equal(
                    expected,
                    repeated[["sku", "forecast_demand", "recommended_order_qty", "status"]],
                )
        pd.testing.assert_series_equal(data["product_name"], original_names)


if __name__ == "__main__":
    unittest.main()

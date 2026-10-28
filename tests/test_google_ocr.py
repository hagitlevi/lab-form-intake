import unittest

from backend.pipeline.google_ocr import parse_words

RESP = {
    "fullTextAnnotation": {
        "pages": [
            {
                "blocks": [
                    {
                        "paragraphs": [
                            {
                                "words": [
                                    {
                                        "confidence": 0.9,
                                        "boundingBox": {"vertices": [{"x": 10, "y": 20}, {"x": 110, "y": 20}, {"x": 110, "y": 60}, {"x": 10, "y": 60}]},
                                        "symbols": [{"text": "מ"}, {"text": "ר"}],
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ]
    }
}


class GoogleOcrParse(unittest.TestCase):
    def test_word_box_and_scale(self):
        w = parse_words(RESP, scale=2)[0]
        self.assertEqual((w.text, w.left, w.top, w.width, w.height, w.conf), ("מר", 5, 10, 50, 20, 90.0))

    def test_empty_response(self):
        self.assertEqual(parse_words({}), [])


if __name__ == "__main__":
    unittest.main()



class TestGoogleRetry(unittest.TestCase):
    def test_transient_error_is_retried_then_succeeds(self):
        import numpy as np
        from unittest import mock
        from backend.pipeline import google_ocr as g
        calls = {"n": 0}

        def flaky(_):
            calls["n"] += 1
            if calls["n"] == 1:
                raise g.GoogleOcrError("Google Vision: The service is currently unavailable.")
            return {}
        with mock.patch.object(g, "_annotate", flaky), mock.patch.object(g.time, "sleep"):
            self.assertEqual(g.google_ocr(np.zeros((10, 10, 3), np.uint8)), [])
        self.assertEqual(calls["n"], 2)

    def test_permanent_error_is_not_retried(self):
        import numpy as np
        from unittest import mock
        from backend.pipeline import google_ocr as g
        calls = {"n": 0}

        def bad(_):
            calls["n"] += 1
            raise g.GoogleOcrError("Google Vision: API key not valid")
        with mock.patch.object(g, "_annotate", bad), mock.patch.object(g.time, "sleep"):
            with self.assertRaises(g.GoogleOcrError):
                g.google_ocr(np.zeros((10, 10, 3), np.uint8))
        self.assertEqual(calls["n"], 1)

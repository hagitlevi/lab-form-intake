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

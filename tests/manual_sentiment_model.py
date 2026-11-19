r"""실제 모델로 한국어 5문장을 분류한다. 일반 pytest 실행에는 자동 포함하지 않는다.

실행: .\.venv\Scripts\python.exe tests/manual_sentiment_model.py
최초 실행은 Hugging Face 다운로드가 필요하며, Gemini·Spring·TourAPI는 호출하지 않는다.
"""

import json
import unittest

from jamkkanjeju_agent.services.sentiment_classifier import KoreanSentimentClassifier


SAMPLES = [
    ("뷰가 정말 아름답고 직원분들도 친절해서 만족스러웠어요.", "POSITIVE"),
    ("음식이 맛있고 매장이 깨끗해서 다시 방문하고 싶어요.", "POSITIVE"),
    ("쓰레기가 많고 냄새가 심해서 다시는 오고 싶지 않아요.", "NEGATIVE"),
    ("입구가 잠겨 있어서 들어갈 수 없었고 안내도 없어 불편했어요.", "NEGATIVE"),
    ("날씨가 좋아 산책하기 편하고 경치도 정말 좋았어요.", "POSITIVE"),
]


class RealSentimentModelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.classifier = KoreanSentimentClassifier()
        cls.results = cls.classifier.classify([text for text, _ in SAMPLES])
        for (text, expected), result in zip(SAMPLES, cls.results, strict=True):
            print(json.dumps({"text": text, "expected": expected, "label": result.label,
                              "score": round(result.score, 6)}, ensure_ascii=False), flush=True)

    def check_sample(self, index):
        self.assertEqual(self.results[index].label, SAMPLES[index][1])
        self.assertGreaterEqual(self.results[index].score, 0.5)
        self.assertLessEqual(self.results[index].score, 1.0)

    def test_positive_view(self):
        self.check_sample(0)

    def test_positive_food(self):
        self.check_sample(1)

    def test_negative_cleanliness(self):
        self.check_sample(2)

    def test_negative_access(self):
        self.check_sample(3)

    def test_positive_walk(self):
        self.check_sample(4)


if __name__ == "__main__":
    unittest.main(verbosity=2)

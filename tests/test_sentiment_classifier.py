"""외부 다운로드 없이 라벨·미확인·배치 처리 규칙을 검증한다."""

from types import SimpleNamespace

import pytest

from jamkkanjeju_agent.services.sentiment_classifier import KoreanSentimentClassifier


def test_empty_or_blank_text_does_not_load_model(monkeypatch):
    classifier = KoreanSentimentClassifier()
    monkeypatch.setattr(classifier, "_load", lambda: pytest.fail("빈 텍스트에서 모델을 불러오면 안 됩니다."))
    assert classifier.classify([]) == []
    results = classifier.classify([None, "", " \t\n"])
    assert [result.label for result in results] == ["UNKNOWN"] * 3
    assert all(result.score is None for result in results)


@pytest.mark.parametrize("batch_size", [0, -1])
def test_invalid_batch_size_is_rejected(batch_size):
    with pytest.raises(ValueError, match="batch_size"):
        KoreanSentimentClassifier().classify(["맛있어요"], batch_size=batch_size)


@pytest.mark.parametrize("texts", ["목록 대신 문장", [123]])
def test_invalid_input_is_rejected(texts):
    with pytest.raises(TypeError):
        KoreanSentimentClassifier().classify(texts)


def test_model_labels_and_original_positions_are_preserved(monkeypatch):
    torch = pytest.importorskip("torch")
    classifier = KoreanSentimentClassifier()
    classifier._torch = torch
    seen = []
    def tokenize(texts, **settings):
        seen.append(texts)
        assert settings["max_length"] == 512
        return {"input_ids": torch.ones((len(texts), 1), dtype=torch.long)}
    classifier._tokenizer = tokenize
    classifier._model = lambda **_: SimpleNamespace(logits=torch.tensor([[5.0, 1.0], [1.0, 5.0]]))
    monkeypatch.setattr(classifier, "_load", lambda: None)
    results = classifier.classify(["  첫 글  ", None, "둘째 글", " "])
    assert [result.label for result in results] == ["NEGATIVE", "UNKNOWN", "POSITIVE", "UNKNOWN"]
    assert seen == [["첫 글", "둘째 글"]]
    assert 0.5 < results[0].score <= 1
    assert results[1].score is None


def test_large_input_is_split_into_batches(monkeypatch):
    torch = pytest.importorskip("torch")
    classifier = KoreanSentimentClassifier()
    classifier._torch = torch
    sizes = []
    def tokenize(texts, **_):
        sizes.append(len(texts))
        return {"input_ids": torch.ones((len(texts), 1), dtype=torch.long)}
    classifier._tokenizer = tokenize
    classifier._model = lambda input_ids: SimpleNamespace(logits=torch.tensor([[1.0, 5.0]] * len(input_ids)))
    monkeypatch.setattr(classifier, "_load", lambda: None)
    assert [result.label for result in classifier.classify(["글"] * 5, batch_size=2)] == ["POSITIVE"] * 5
    assert sizes == [2, 2, 1]

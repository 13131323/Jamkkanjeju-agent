"""한국어 감성 분류 모델을 지연 로딩하여 로컬 CPU에서 방명록 텍스트를 분류한다."""

from collections.abc import Sequence
from dataclasses import dataclass
from threading import RLock
from typing import Literal


MODEL_ID = "daekeun-ml/koelectra-small-v3-nsmc"
MODEL_REVISION = "d1c0233fc013ae8bd879d7b61f2bec69aa3af4c9"
# 모델 카드의 classes=['Neg', 'Pos'] 기준이다. config.id2label에는 숫자만 저장되어 있다.
LABELS = ("NEGATIVE", "POSITIVE")


@dataclass(frozen=True, slots=True)
class SentimentPrediction:
    """입력 순서에 대응하는 감성 결과. UNKNOWN은 텍스트가 없는 경우에만 사용한다."""

    label: Literal["POSITIVE", "NEGATIVE", "UNKNOWN"]
    score: float | None


class KoreanSentimentClassifier:
    """동일 객체에서 모델을 한 번만 로딩하며 외부 LLM API는 호출하지 않는다."""

    def __init__(self):
        self._load_lock = RLock()
        self._tokenizer = None
        self._model = None
        self._torch = None

    def _load(self) -> None:
        """최초 텍스트 분석 때만 공개 모델을 내려받고, 이후 Hugging Face 로컬 캐시를 사용한다."""
        with self._load_lock:
            if self._model is not None:
                return
            try:
                import torch
                from transformers import AutoModelForSequenceClassification, AutoTokenizer
            except ImportError as exc:
                raise RuntimeError('감성 분석 의존성을 설치해 주세요: python -m pip install -e ".[sentiment]"') from exc
            tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, revision=MODEL_REVISION, trust_remote_code=False)
            model = AutoModelForSequenceClassification.from_pretrained(
                MODEL_ID, revision=MODEL_REVISION, use_safetensors=True, trust_remote_code=False,
            ).to("cpu")
            if model.config.num_labels != 2:
                raise RuntimeError("감성 모델은 긍정·부정 이진 분류여야 합니다.")
            model.eval()
            self._torch = torch
            self._tokenizer = tokenizer
            self._model = model

    def classify(self, texts: Sequence[str | None], *, batch_size: int = 16) -> list[SentimentPrediction]:
        """공백·null은 미확인으로 유지하고 텍스트는 최대 512토큰까지 긍정/부정으로 분류한다."""
        if isinstance(texts, (str, bytes)):
            raise TypeError("문장 하나가 아닌 문장 목록을 전달해 주세요.")
        if batch_size <= 0:
            raise ValueError("batch_size는 1 이상이어야 합니다.")
        if any(text is not None and not isinstance(text, str) for text in texts):
            raise TypeError("텍스트는 문자열 또는 None이어야 합니다.")
        results = [SentimentPrediction("UNKNOWN", None) for _ in texts]
        active = [(index, text.strip()) for index, text in enumerate(texts) if text is not None and text.strip()]
        if not active:
            return results
        self._load()
        for start in range(0, len(active), batch_size):
            batch = active[start:start + batch_size]
            inputs = self._tokenizer(
                [text for _, text in batch], padding=True, truncation=True, max_length=512, return_tensors="pt",
            )
            with self._torch.inference_mode():
                probabilities = self._model(**inputs).logits.softmax(dim=-1)
                scores, labels = probabilities.max(dim=-1)
            for (index, _), label, score in zip(batch, labels.tolist(), scores.tolist(), strict=True):
                results[index] = SentimentPrediction(LABELS[label], float(score))
        return results

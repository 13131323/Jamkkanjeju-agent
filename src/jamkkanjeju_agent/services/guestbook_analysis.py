"""개별 방명록 감성을 장소별 ID 배열과 전체 대비 부정 비율로 집계한다."""

from functools import lru_cache
from .sentiment_classifier import KoreanSentimentClassifier


@lru_cache(maxsize=1)
def get_sentiment_classifier() -> KoreanSentimentClassifier:
    """모델은 최초 분석 때 로드하고 이후 재사용한다."""
    return KoreanSentimentClassifier()


def analyze_guestbooks(locations: list[dict], guestbooks: list[dict], classifier) -> list[dict]:
    """원문·점수 없이 집계하며 실패하면 결과를 반환하지 않는다."""
    grouped = {row["locationId"]: {"locationId": row["locationId"], "locationName": row["title"],
               "positive": [], "negative": [], "unknown": []} for row in locations}
    seen = set()
    for start in range(0, len(guestbooks), 100):
        batch = guestbooks[start:start + 100]
        predictions = classifier.classify([row["text"] for row in batch])
        for row, prediction in zip(batch, predictions, strict=True):
            guestbook_id = row["guestbookId"]
            if row["locationId"] not in grouped or guestbook_id in seen:
                raise ValueError("조회 도중 장소 또는 방명록 데이터가 변경되었습니다.")
            seen.add(guestbook_id)
            expected = {"POSITIVE", "NEGATIVE"} if row["text"] and row["text"].strip() else {"UNKNOWN"}
            if prediction.label not in expected:
                raise ValueError("감성 모델 분류 결과가 계약과 다릅니다.")
            grouped[row["locationId"]][prediction.label.lower()].append(guestbook_id)
    for row in grouped.values():
        for label in ("positive", "negative", "unknown"):
            row[label + "Count"] = len(row[label])
        row["totalCount"] = sum(row[label + "Count"] for label in ("positive", "negative", "unknown"))
        row["negativeRatio"] = row["negativeCount"] / row["totalCount"] if row["totalCount"] else None
    return list(grouped.values())

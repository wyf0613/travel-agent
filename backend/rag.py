"""RAG 最小实现（独立模块，零 Key 零依赖，不改动 travel-agent 现有代码）。

运行方式：在 backend/ 目录执行
    .venv/Scripts/python rag.py

当前只放第一个函数 chunk_text。写完并验证后，再依次往本文件里追加：
    Embedder (Fake / Real)  ->  VectorStore (手写余弦相似度)  ->  retrieve
"""

from __future__ import annotations


def chunk_text(text: str, size: int = 400, overlap: int = 50) -> list[str]:
    """把长文本切成小块。

    由你实现，四条要求：
      1. 每块长度不超过 size 个字符
      2. 相邻两块之间有 overlap 个字符的重叠，避免一句话被切断后谁都不完整
      3. 尽量在句子边界（。！？\\n）处断开，不要从汉字中间硬切
      4. 返回 list[str]；空文本返回 []

    动手前先想清楚两件事：
      - overlap=50 时，第二块的起点应该是 size - overlap 吗？
      - 一旦改成"在句子边界断"，步长还固定吗？会不会出现某块过短的情况？
    """
    raise NotImplementedError


if __name__ == "__main__":
    sample = (
        "西湖位于杭州市西湖区，是中国主要的观赏性淡水湖泊之一。"
        "西湖三面环山，面积约 6.39 平方千米，湖中被孤山、白堤、苏堤分隔。"
        "2011 年杭州西湖文化景观被列入世界遗产名录。"
        "断桥残雪是西湖十景之一，位于白堤东端，因《白蛇传》的传说而闻名。"
    )
    for i, c in enumerate(chunk_text(sample, size=60, overlap=15)):
        print(f"[{i}] ({len(c)}字) {c}")

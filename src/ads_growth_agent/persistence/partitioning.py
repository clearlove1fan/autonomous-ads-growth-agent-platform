from hashlib import sha256

from ads_growth_agent.persistence.schema import PARTITION_BUCKETS


def partition_bucket(value: str, *, buckets: int = PARTITION_BUCKETS) -> int:
    if buckets <= 0:
        raise ValueError("buckets must be greater than zero")
    normalized = value.strip()
    if not normalized:
        raise ValueError("partition key must not be empty")
    digest = sha256(normalized.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False) % buckets

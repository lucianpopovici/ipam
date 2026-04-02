"""Shared Redis connection and core utilities."""
import os
import json
import uuid
import redis

r = redis.Redis(
    host=os.getenv('REDIS_HOST', 'localhost'),
    port=int(os.getenv('REDIS_PORT', '6379')),
    db=int(os.getenv('REDIS_DB', '0')),
    password=os.getenv('REDIS_PASSWORD', None),
    decode_responses=True,
)


def new_id() -> str:
    """Generate a random 8-char hex ID string."""
    return str(uuid.uuid4())[:8]


def parse_labels(value):
    """Parses a comma-separated string of labels into a list of unique strings, preserving order."""
    if value is None:
        return []
    parts = [p.strip() for p in str(value).split(',')]
    labels = [p for p in parts if p]
    seen = []
    for label in labels:
        if label not in seen:
            seen.append(label)
    return seen


def redis_get(key, is_json=True):
    """Retrieves an item from Redis, optionally decoding it from JSON."""
    raw = r.get(key)
    if not raw:
        return None
    return json.loads(raw) if is_json else raw


def redis_save(key, data, index_key=None, is_json=True, item_id=None):
    """Saves an item to Redis, optionally encoding it as JSON, and adds its ID to an index set."""
    val = json.dumps(data) if is_json else data
    r.set(key, val)
    if index_key:
        tid = item_id
        if tid is None and isinstance(data, dict):
            tid = data.get('id')
        if tid:
            r.sadd(index_key, tid)
    return data


def redis_delete(key, index_key=None, item_id=None):
    """Deletes an item from Redis and optionally removes its ID from an index set."""
    r.delete(key)
    if index_key and item_id:
        r.srem(index_key, item_id)


def redis_all(index_key, get_func, sort_key=None):
    """Lists all items in a Redis set by applying a getter function to each ID."""
    items = [get_func(item_id) for item_id in sorted(r.smembers(index_key)) if get_func(item_id)]
    if sort_key:
        items.sort(key=sort_key)
    return items

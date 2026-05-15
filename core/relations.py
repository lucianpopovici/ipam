"""
Generic many-to-many relation store backed by Redis.

Storage layout:
  rel:{name}:fwd:{a_id}   Set — B-ids linked from A
  rel:{name}:rev:{b_id}   Set — A-ids linked to B

Relations are declared in config/relations.yaml; this module only
provides the storage primitives — it has no knowledge of entity types.

Uses `import db; db.r` so that test monkeypatching of db.r propagates
here without requiring a separate conftest entry.
"""
import db


def _fwd(rel_name, a_id):
    return f'rel:{rel_name}:fwd:{a_id}'


def _rev(rel_name, b_id):
    return f'rel:{rel_name}:rev:{b_id}'


def relate(rel_name, a_id, b_id):
    """Link entity A to entity B in a named relation."""
    db.r.sadd(_fwd(rel_name, a_id), b_id)
    db.r.sadd(_rev(rel_name, b_id), a_id)


def unrelate(rel_name, a_id, b_id):
    """Unlink entity A from entity B in a named relation."""
    db.r.srem(_fwd(rel_name, a_id), b_id)
    db.r.srem(_rev(rel_name, b_id), a_id)


def related(rel_name, entity_id, direction='fwd') -> set:
    """Return all entities related to entity_id in specified direction."""
    key = _fwd(rel_name, entity_id) if direction == 'fwd' else _rev(rel_name, entity_id)
    return db.r.smembers(key)


def clear_entity(rel_name, entity_id):
    """Remove all forward and reverse links involving entity_id."""
    for b_id in list(related(rel_name, entity_id, 'fwd')):
        unrelate(rel_name, entity_id, b_id)
    for a_id in list(related(rel_name, entity_id, 'rev')):
        unrelate(rel_name, a_id, entity_id)

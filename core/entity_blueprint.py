"""
EntityBlueprint — base class for Redis-backed entity types.

Provides a standard CRUD recipe so adding a new entity type is a small
subclass rather than hand-rolling the same Redis key helpers every time.

Usage (adding a new "Zone" entity)
────────────────────────────────────
    # zones.py
    from core.entity_blueprint import EntityBlueprint

    class ZoneBlueprint(EntityBlueprint):
        entity_prefix          = 'zone'            # key: zone:{id}
        global_index           = 'zones:index'     # set of all zone IDs
        project_index_pattern  = 'project:{pid}:zones'

        def on_save(self, entity: dict) -> dict:
            # Optional hook — called inside save() before persisting.
            # Return the (possibly modified) entity.
            return entity

        def on_delete(self, entity_id: str):
            # Optional hook — called inside delete() before removing from Redis.
            pass

    zones = ZoneBlueprint()

    # Then use as:
    zones.save({'id': 'z-001', 'name': 'LON', 'project_id': 'proj-abc', ...})
    zones.get('z-001')
    zones.project_entities('proj-abc')
    zones.delete('z-001')

Design notes
────────────
- No Flask blueprint here — that stays in the calling module as today.
- Uses `import db; db.r` for Redis so test monkeypatching propagates.
- Subclasses can override `_key(id)` if the key pattern differs from
  "{entity_prefix}:{id}".
- `sorted_by` can be overridden to change the sort field used by `all()`
  and `project_entities()`.
"""
import json
import db


class EntityBlueprint:
    """
    Mixin for entities stored as ``{prefix}:{id}`` JSON in Redis.

    Subclasses must set the three class attributes below.
    All other methods have working defaults.
    """

    #: Redis key prefix, e.g. ``"zone"`` → keys are ``zone:{id}``
    entity_prefix: str = ''

    #: Redis Set key holding every entity ID globally
    global_index: str = ''

    #: Pattern for project-scoped index; ``{pid}`` is interpolated
    project_index_pattern: str = ''

    #: Entity field used as sort key in list results
    sorted_by: str = 'name'

    # ── Key helpers ────────────────────────────────────────────────────────────

    def _key(self, entity_id: str) -> str:
        return f'{self.entity_prefix}:{entity_id}'

    def _project_index(self, pid: str) -> str:
        return self.project_index_pattern.format(pid=pid)

    # ── Hooks (override in subclasses) ─────────────────────────────────────────

    def on_save(self, entity: dict) -> dict:
        """Called before writing to Redis. Return the entity (may be modified)."""
        return entity

    def on_delete(self, entity_id: str):
        """Called before removing from Redis. Use to clean up related data."""

    # ── CRUD ───────────────────────────────────────────────────────────────────

    def get(self, entity_id: str) -> dict | None:
        """Retrieve an entity by ID. Returns None if not found."""
        raw = db.r.get(self._key(entity_id))
        return json.loads(raw) if raw else None

    def save(self, entity: dict) -> dict:
        """
        Persist *entity* to Redis and update the global (and project) index.

        Calls ``on_save`` before writing so subclasses can enrich or validate.
        """
        entity = self.on_save(entity)
        entity_id = entity['id']
        db.r.set(self._key(entity_id), json.dumps(entity))
        if self.global_index:
            db.r.sadd(self.global_index, entity_id)
        if self.project_index_pattern and entity.get('project_id'):
            db.r.sadd(self._project_index(entity['project_id']), entity_id)
        return entity

    def delete(self, entity_id: str):
        """
        Remove an entity from Redis and all indices.

        Calls ``on_delete`` before removal so subclasses can clean up.
        """
        entity = self.get(entity_id)
        if not entity:
            return
        self.on_delete(entity_id)
        if self.global_index:
            db.r.srem(self.global_index, entity_id)
        if self.project_index_pattern and entity.get('project_id'):
            db.r.srem(self._project_index(entity['project_id']), entity_id)
        db.r.delete(self._key(entity_id))

    def all(self) -> list:
        """Return every entity in the global index, sorted by ``sorted_by``."""
        if not self.global_index:
            return []
        ids     = db.r.smembers(self.global_index)
        result  = [e for e in (self.get(i) for i in ids) if e]
        return sorted(result, key=lambda e: e.get(self.sorted_by, ''))

    def project_entities(self, pid: str) -> list:
        """Return all entities belonging to project *pid*, sorted."""
        if not self.project_index_pattern:
            return []
        ids    = db.r.smembers(self._project_index(pid))
        result = [e for e in (self.get(i) for i in ids) if e]
        return sorted(result, key=lambda e: e.get(self.sorted_by, ''))

    def exists(self, entity_id: str) -> bool:
        """Return True if an entity with this ID exists."""
        return db.r.exists(self._key(entity_id)) == 1

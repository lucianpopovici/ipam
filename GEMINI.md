# Gemini.md: Redis-IPAM Implementation Roadmap

This document serves as the strategic guide to evolving this Flask/Redis IPAM from a basic registry into a high-performance, automation-ready **Network Source of Truth**.

---

## 🛠 Phase 1: Short-Term Fixes (Stability & Data Integrity)

### 1. Atomic IP Allocation (The "Double-Book" Fix)
In a Redis environment, race conditions occur when two API calls check for an available IP at the same time. 
*   **Strategy:** Use **Redis Lua Scripting**. 
*   **Goal:** Ensure the "Check if Free" and "Set to Active" operations happen in a single atomic step.
*   **Task:** Implement an `allocate_ip()` function that executes a script on the Redis server to prevent duplicate assignments.

### 2. Data Persistence Configuration
By default, Redis is ephemeral. If the service restarts, your IP data is lost.
*   **Action:** Update your `redis.conf` or Docker environment variables to enable **AOF (Append Only File)**.
*   **Settings:** `appendonly yes` and `appendfsync everysec`.

### 3. Strict CIDR Validation
Since you aren't using a relational schema, you must enforce data types in the Flask layer to prevent "garbage" data.
*   **Action:** Integrate Python’s `ipaddress` module.
*   **Rule:** Every `POST` or `PUT` must pass `ipaddress.ip_interface()` validation before touching Redis.

---

## 🚀 Phase 2: Competing with NetBox (Key Features)

### 1. "Next Available IP" Engine (The Speed Advantage)
NetBox is often slow with large subnets. You can beat it using **Redis Bitmaps**.
*   **Implementation:** Represent a `/24` subnet as a 256-bit string in Redis.
*   **Logic:** Use the Redis `BITPOS` command to find the first `0` (off) bit. 
*   **Result:** Finding a free IP becomes an $O(1)$ or $O(N)$ operation in memory, significantly faster than SQL `SELECT` queries.

### 2. Hierarchical Data Modeling
NetBox uses Foreign Keys to link IPs to Subnets. In Redis, use **Namespacing**.
*   **Key Structure:** 
    *   `ip:10.0.0.1` → **Hash** (Store MAC, Hostname, Description)
    *   `subnet:10.0.0.0/24` → **Set** (Store list of all member IP keys)
    *   `vrf:production` → **Set** (Store list of all member Subnet keys)

### 3. RESTful API & Documentation
To be a professional tool, it must be scriptable via a standard interface.
*   **Action:** Use **Flask-Smorest** to generate an automatic **Swagger UI**.
*   **Must-Have Endpoint:** `POST /api/v1/subnets/<prefix>/request-ip/`

---

## 🏗 Phase 3: Advanced Automation

| Feature | Redis Implementation | Competitive Advantage |
| :--- | :--- | :--- |
| **Global Search** | **RediSearch Module** | Instant full-text search across all metadata (Owner, Location, VLAN). |
| **TTL Reservations** | `SETEX key time value` | Temporary IP reservations for DHCP or testing that auto-expire. |
| **Real-time Updates** | **Redis Pub/Sub** | Push instant UI updates to a dashboard when an IP is claimed. |

---

## 💻 Implementation Example: The Atomic Claim (Python/Lua)

```python
# Lua script to ensure an IP isn't snatched by two processes simultaneously
claim_script = """
local current = redis.call('GET', KEYS[1])
if not current or current == 'available' then
    redis.call('SET', KEYS[1], ARGV[1])
    return 1
else
    return 0
end
"""

def claim_ip(ip_key, owner_name):
    # Returns 1 if successful, 0 if already taken
    return redis_client.eval(claim_script, 1, ip_key, owner_name)
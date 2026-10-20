import os

from memory import MemoryStore, register_memory

MEM_KEY = b"m" * 32


def attach_memory(h, budget=None, grant=("agent.alpha",), key=MEM_KEY):
    """Wire a MemoryStore into a Harness and grant read+write to the given actors."""
    h.mem_path = os.path.join(h.dir.name, "memory.sqlite")
    h.mem = MemoryStore(h.mem_path, key, budget=budget, clock=h.wall)
    register_memory(h.policy, h.cp, h.mem, h.audit)
    for actor in grant:
        h.policy.grant(actor, "cap.memory.write")
        h.policy.grant(actor, "cap.memory.search")
    return h.mem


def write(h, content, op, url="http://127.0.0.1/x", source="test.src", **extra):
    args = {"content": content, "origin_url": url, "source_id": source, **extra}
    return h.cp.execute(h.req("cap.memory.write", op=op, args=args))


def search(h, query, op, **extra):
    return h.cp.execute(h.req("cap.memory.search", op=op, args={"query": query, **extra}))

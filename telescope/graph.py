"""PageRank over a directed graph — the Markov chain part.

PageRank *is* a Markov chain: imagine a walker on the graph who, at each
step, follows a random outbound link with probability `damping`, or jumps to
a uniformly random node with probability `1 - damping` (the "random jump"
that keeps the chain irreducible and guarantees convergence even on a graph
with dead ends or disconnected components). The scores this returns are that
chain's stationary distribution — the long-run fraction of time the walker
spends at each node, computed by power iteration rather than by actually
simulating a walk, which converges to the same distribution far faster on a
graph this small.

Plain Python throughout: graphs built from a single bounded crawl (see
`telescope/crawl.py`) top out at a few dozen nodes, well below where a numpy
dependency would earn its cost.
"""


def pagerank(edges, *, damping=0.85, iterations=50, tol=1e-6):
    """`edges`: {node: [linked_node, ...]}. Nodes only ever appearing as a
    link target (never as a key) are included too, with no outlinks of their
    own. Returns {node: score}, scores summing to 1.0.

    A node with no outlinks ("dangling") would otherwise leak rank mass out
    of the system every iteration -- score conservation requires putting
    that mass back in, so a dangling node's share is redistributed evenly
    across every node, exactly like the random jump already does for the
    `1 - damping` term.
    """
    nodes = set(edges)
    for targets in edges.values():
        nodes.update(targets)
    n = len(nodes)
    if n == 0:
        return {}

    outlinks = {node: list(dict.fromkeys(edges.get(node, []))) for node in nodes}
    score = {node: 1.0 / n for node in nodes}

    for _ in range(iterations):
        dangling_mass = sum(score[node] for node in nodes if not outlinks[node])
        base = (1.0 - damping) / n + damping * dangling_mass / n
        new_score = {node: base for node in nodes}
        for node, targets in outlinks.items():
            if not targets:
                continue
            share = damping * score[node] / len(targets)
            for target in targets:
                new_score[target] += share
        delta = sum(abs(new_score[node] - score[node]) for node in nodes)
        score = new_score
        if delta < tol:
            break

    return score

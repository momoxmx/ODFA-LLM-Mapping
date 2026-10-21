import textwrap
import matplotlib.pyplot as plt

# line style for each kind of item -> class link: (color, linestyle, legend text)
LINK_STYLE = {
    'ontology': ('black', '-', 'mapping in ODFA'),
    'both': ('black', '-', 'human and model'),
    'model': ('tab:blue', '-', 'model only'),
    'human': ('tab:red', '-', 'human only'),
    'new': ('tab:orange', '--', 'proposed new class'),
}


def compare_links(row):
    """Links from one item to classes, labelled by who chose them."""
    human, model = set(row['human_nodes']), set(row['nodes'])
    links = []
    for node in sorted(human | model):
        if node in human and node in model:
            links.append((node, 'both'))
        elif node in model:
            links.append((node, 'model'))
        else:
            links.append((node, 'human'))
    if isinstance(row['new_node'], dict):
        links.append(('NEW: ' + row['new_node']['label'], 'new'))
    return links


def tree_positions(node_table, new_classes):
    """x = depth in the tree, y = order in a depth-first walk."""
    children = {}
    for _, row in node_table.iterrows():
        for parent in row['parent_ids'] or [None]:
            children.setdefault(parent, []).append(row['node_id'])
    for name, parent in new_classes.items():
        children.setdefault(parent, []).append(name)

    pos = {}

    def visit(parent, depth):
        for child in children.get(parent, []):
            if child not in pos:          # a class with two parents is drawn once
                pos[child] = (depth, len(pos))
                visit(child, depth + 1)

    visit(None, 0)
    return pos, children


def plot_placements(node_table, item_table, placements=None, out_file=None, only_diff=False, text_width=110):
    """Left: ODFA class tree. Right: questionnaire items. Lines: item -> class.
    placements=None draws the mapping stored in the ontology itself."""
    if placements is None:
        d = item_table.copy()
        d['links'] = d['human_nodes'].apply(lambda nodes: [(n, 'ontology') for n in nodes])
    else:
        d = item_table.merge(placements[['item_id', 'nodes', 'new_node']], on='item_id')
        d['links'] = d.apply(compare_links, axis=1)
        if only_diff:
            d = d[d['links'].apply(lambda links: any(kind != 'both' for _, kind in links))]

    new_classes = {}   # only the V1 prompt could propose new classes
    for new in d.get('new_node', []):
        if isinstance(new, dict):
            new_classes['NEW: ' + new['label']] = new['parent_id']
    pos, children = tree_positions(node_table, new_classes)

    # stretch the tree and the item column to the same height
    n_rows = max(len(pos), len(d))
    pos = {k: (x, y * n_rows / len(pos)) for k, (x, y) in pos.items()}
    item_x = max(x for x, _ in pos.values()) + 3

    # put each item next to the classes it links to
    d['order'] = d['links'].apply(lambda links: sum(pos[n][1] for n, _ in links) / len(links) if links else n_rows)
    d = d.sort_values('order', ignore_index=True)
    d['y'] = d.index * n_rows / max(len(d), 1)

    fig, ax = plt.subplots(figsize=(24, max(n_rows * 0.32, len(pos) * 0.5)))

    # tree edges
    for parent, (px, py) in pos.items():
        for child in children.get(parent, []):
            cx, cy = pos[child]
            ax.plot([px, px, cx], [py, cy, cy], color='lightgray', lw=1,
                    ls='--' if child.startswith('NEW') else '-', zorder=0)

    # item -> class links and item text
    for _, row in d.iterrows():
        for node, kind in row['links']:
            color, ls, _ = LINK_STYLE[kind]
            x, y = pos[node]
            ax.plot([x + 0.1, item_x - 0.1], [y, row['y']], color=color, ls=ls, lw=1, alpha=0.6)
        text = f"{row['item_id']}  {textwrap.shorten(row['item_text'], text_width)}"
        ax.text(item_x, row['y'], text, va='center', fontsize=8)

    # class boxes
    labels = dict(zip(node_table['node_id'], node_table['label']))
    for node, (x, y) in pos.items():
        is_new = node.startswith('NEW')
        ax.text(x, y, '\n'.join(textwrap.wrap(node if is_new else labels[node], 28)),
                va='center', ha='center', fontsize=8,
                bbox=dict(boxstyle='round', fc='moccasin' if is_new else 'whitesmoke',
                          ec='tab:orange' if is_new else 'gray'))

    used = {kind for links in d['links'] for _, kind in links}
    for kind, (color, ls, text) in LINK_STYLE.items():
        if kind in used:
            ax.plot([], [], color=color, ls=ls, label=text)
    ax.legend(loc='upper left', bbox_to_anchor=(0, 1.05), ncol=4, frameon=False)
    ax.set_xlim(-0.8, item_x + 7)
    ax.set_ylim(n_rows, -1)
    ax.axis('off')
    plt.tight_layout()
    if out_file:
        plt.savefig(out_file, dpi=150)
    plt.show()
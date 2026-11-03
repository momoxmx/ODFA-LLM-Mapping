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

def plot_ontology_input(node_table, concept_table, axiom_links, out_file=None, wrap=24):
    """The V4/V5 ontology input as a picture.
    Left: question class tree (Part 1). Right: related concept tree (Part 2).
    Orange lines: the logical axioms (Part 3) linking a question class to the concept it is about;
    dashed orange = one alternative in an OR definition. Solid gray/green lines: parent-child ("is a").
    A concept with a second parent is drawn once; the extra parent link is a dotted line.
    Get concept_table and axiom_links from ontology.ontology_structure()."""
    q_pos, q_children = tree_positions(node_table, {})
    n_rows = max(len(q_pos), len(concept_table))
    q_pos = {k: (x, y * n_rows / len(q_pos)) for k, (x, y) in q_pos.items()}

    # concept tree: order branches by where their linked question classes sit, so the axiom lines cross less
    parents = dict(zip(concept_table['concept_id'], concept_table['parent_ids']))
    children = {}
    for c, ps in parents.items():
        for p in ps or [None]:
            children.setdefault(p, []).append(c)
    target_y = axiom_links.assign(y=axiom_links['node_id'].map(lambda n: q_pos[n][1])) \
                          .groupby('concept_id')['y'].min().to_dict()
    memo = {}

    def key(c):
        if c not in memo:
            memo[c] = n_rows + 1
            memo[c] = min([target_y.get(c, n_rows + 1)] + [key(k) for k in children.get(c, [])])
        return memo[c]

    gap = max(x for x, _ in q_pos.values()) + 4
    c_pos, primary = {}, {}

    def visit(parent, depth):
        for c in sorted(children.get(parent, []), key=key):
            if c not in c_pos:
                c_pos[c] = (gap + depth, len(c_pos) * n_rows / len(concept_table))
                primary[c] = parent
                visit(c, depth + 1)

    visit(None, 0)

    fig, ax = plt.subplots(figsize=(28, n_rows * 0.42))
    for parent, (px, py) in q_pos.items():
        for child in q_children.get(parent, []):
            cx, cy = q_pos[child]
            ax.plot([px, px, cx], [py, cy, cy], color='gray', lw=0.9, alpha=0.6, zorder=0)
    for c, ps in parents.items():
        cx, cy = c_pos[c]
        for p in ps:
            px, py = c_pos[p]
            if p == primary[c]:
                ax.plot([px, px, cx], [py, cy, cy], color='#5E9C5D', lw=0.9, alpha=0.7, zorder=0)
            else:
                ax.plot([px + 0.4, cx - 0.4], [py, cy], color='#5E9C5D', lw=0.8, ls=':', alpha=0.6, zorder=0)

    linked = set(axiom_links['concept_id'])
    for _, link in axiom_links.iterrows():
        (qx, qy), (cx, cy) = q_pos[link['node_id']], c_pos[link['concept_id']]
        ax.annotate('', xy=(cx - 0.5, cy), xytext=(qx + 0.5, qy),
                    arrowprops=dict(arrowstyle='->', color='tab:orange', lw=1.6,
                                    ls='--' if link['kind'] == 'or' else '-'), zorder=1)

    q_labels = dict(zip(node_table['node_id'], node_table['label']))
    c_labels = dict(zip(concept_table['concept_id'], concept_table['label']))
    for pos, labels, fc, ec in [(q_pos, q_labels, 'whitesmoke', 'gray'), (c_pos, c_labels, '#E3F2E3', '#5E9C5D')]:
        for node, (x, y) in pos.items():
            ax.text(x, y, '\n'.join(textwrap.wrap(labels[node], wrap)), va='center', ha='center', fontsize=7.5,
                    bbox=dict(boxstyle='round', fc=fc, ec=ec, lw=2 if node in linked else 1), zorder=2)

    ax.text(0, -1.6, 'Part 1. Question classes', fontsize=13, weight='bold', color='dimgray')
    ax.text(gap, -1.6, 'Part 2. Related concepts', fontsize=13, weight='bold', color='#3E7C3D')
    ax.plot([], [], color='tab:orange', label='Part 3. axiom: question class is about concept')
    ax.plot([], [], color='tab:orange', ls='--', label='Part 3. axiom: one alternative in an OR definition')
    ax.plot([], [], color='gray', label='is a (parent-child)')
    ax.plot([], [], color='#5E9C5D', ls=':', label='is a (second parent)')
    ax.legend(loc='upper right', frameon=False, fontsize=10)
    ax.set_xlim(-0.8, max(x for x, _ in c_pos.values()) + 1)
    ax.set_ylim(n_rows, -2.5)
    ax.axis('off')
    plt.tight_layout()
    if out_file:
        plt.savefig(out_file, dpi=150)
    plt.show()


def plot_concept_axioms(concept_table, concept_links, out_file=None, wrap=30):
    """Part 4 of the V4/V5 input as its own picture.
    Left: the Part 2 concepts that have their own logical axioms. Right: the other classes those
    axioms mention. Each arrow is one axiom, labelled with its relation,
    e.g. dental procedure --happens during some--> dental visit.
    Get concept_links from ontology.concept_axiom_links()."""
    c_labels = dict(zip(concept_table['concept_id'], concept_table['label']))
    t_labels = dict(zip(concept_links['target_id'], concept_links['target_label']))
    sources = sorted(concept_links['concept_id'].unique(), key=lambda c: c_labels[c])
    s_pos = {c: (0, k * 1.6) for k, c in enumerate(sources)}

    # each target sits at the average height of the concepts pointing to it, kept at least 1.6 apart
    wanted = concept_links.assign(y=concept_links['concept_id'].map(lambda c: s_pos[c][1])) \
                          .groupby('target_id')['y'].mean().sort_values()
    t_pos, last = {}, -10
    for target, y in wanted.items():
        y = max(y, last + 1.6)
        t_pos[target] = (10, y)
        last = y

    height = max(max(y for _, y in s_pos.values()), last) + 1.6
    fig, ax = plt.subplots(figsize=(14, height * 0.55 + 1.5))
    for _, link in concept_links.iterrows():
        (sx, sy), (tx, ty) = s_pos[link['concept_id']], t_pos[link['target_id']]
        ax.annotate('', xy=(tx - 1.6, ty), xytext=(sx + 1.9, sy),
                    arrowprops=dict(arrowstyle='->', color='tab:purple', lw=1.2, alpha=0.75), zorder=1)
        # label near the start of the arrow, before arrows from different concepts cross
        x0, x1 = sx + 1.9, tx - 1.6
        lx = x0 + 0.28 * (x1 - x0)
        ly = sy + (ty - sy) * (lx - x0) / (x1 - x0)
        ax.text(lx, ly, link['relation'], fontsize=8, color='tab:purple',
                ha='center', va='center', bbox=dict(fc='white', ec='none', pad=0.5), zorder=2)
    for pos, labels, fc, ec in [(s_pos, c_labels, '#E3F2E3', '#5E9C5D'), (t_pos, t_labels, '#EFE6F5', 'tab:purple')]:
        for node, (x, y) in pos.items():
            ax.text(x, y, '\n'.join(textwrap.wrap(labels[node], wrap)), ha='center', va='center', fontsize=9,
                    bbox=dict(boxstyle='round', fc=fc, ec=ec), zorder=3)
    ax.text(0, -1.4, 'Part 2 concepts with their own axioms', ha='center', fontsize=12, weight='bold', color='#3E7C3D')
    ax.text(10, -1.4, 'Other classes these axioms mention', ha='center', fontsize=12, weight='bold', color='tab:purple')
    ax.set_title('Part 4. Logical axioms of the related concepts', fontsize=14, loc='left')
    ax.set_xlim(-2.5, 12.5)
    ax.set_ylim(height, -2.2)
    ax.axis('off')
    plt.tight_layout()
    if out_file:
        plt.savefig(out_file, dpi=150)
    plt.show()

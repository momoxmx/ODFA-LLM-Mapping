import re
import os
import json
import hashlib

import rdflib
import pandas as pd
from rdflib import RDF, RDFS, OWL, URIRef
from rdflib.collection import Collection

OBO = 'http://purl.obolibrary.org/obo/'
DEFINITION = URIRef(OBO + 'IAO_0000115')

# Item labels look like "DFS 01. ...", "MDAS 3. ...", "IDAF-4C+ 1a. ..."
ITEM_PATTERN = r'^(DFS \d+|MDAS \d+|IDAF-4C\+ \d[a-j])\. (.*)'
# IDAF-4C+ stems look like "IDAF-4C+ 1. How much do you agree ..."
STEM_PATTERN = r'^IDAF-4C\+ \d\. (.*)'


# ---------- 1. Read the ontology ----------

def short_id(uri):
    return str(uri).split('/')[-1]


def find_class(g, label):
    for cls, lab in g.subject_objects(RDFS.label):
        if str(lab) == label:
            return cls


def all_subclasses(g, parent):
    result = set()
    for child in g.subjects(RDFS.subClassOf, parent):
        result.add(child)
        result |= all_subclasses(g, child)
    return result


def read_ontology(owl_file):
    """Split the ODFA question classes into content classes (nodes) and questionnaire items."""
    g = rdflib.Graph()
    g.parse(owl_file)

    def label(cls):
        return str(g.value(cls, RDFS.label))

    question = find_class(g, 'question')
    assessment = find_class(g, 'assessment question')
    instruments = set(g.subjects(RDFS.subClassOf, assessment))   # e.g. "DFS question", "MDAS question"

    items, stems, nodes = set(), set(), set()
    for cls in all_subclasses(g, question):
        if re.match(ITEM_PATTERN, label(cls)):
            items.add(cls)
        elif re.match(STEM_PATTERN, label(cls)):
            stems.add(cls)
        elif cls not in instruments and cls != assessment:
            nodes.add(cls)

    node_rows = []
    for cls in nodes:
        parents = [short_id(p) for p in g.objects(cls, RDFS.subClassOf) if p in nodes]
        node_rows.append({'node_id': short_id(cls), 'label': label(cls),
                          'definition': str(g.value(cls, DEFINITION)), 'parent_ids': parents})
    node_table = pd.DataFrame(node_rows).sort_values('label', ignore_index=True)

    item_rows = []
    for cls in items:
        item_id, text = re.match(ITEM_PATTERN, label(cls)).groups()
        parents = list(g.objects(cls, RDFS.subClassOf))
        stem = ''
        for p in parents:
            if p in stems:
                stem = re.match(STEM_PATTERN, label(p)).group(1)
        human_nodes = sorted(short_id(p) for p in parents if p in nodes)
        item_rows.append({'item_id': item_id, 'item_text': text.strip(), 'stem': stem, 'human_nodes': human_nodes})
    item_table = pd.DataFrame(item_rows).sort_values('item_id', ignore_index=True)

    return node_table, item_table


def tree_text(node_table):
    """The class hierarchy as indented text; this is what goes into the prompt."""
    lines = []

    def add_children(parent, depth):
        for _, row in node_table.iterrows():
            is_child = (row['parent_ids'] == []) if parent is None else (parent in row['parent_ids'])
            if is_child:
                lines.append('  ' * depth + f"- [{row['node_id']}] {row['label']}: {row['definition']}")
                add_children(row['node_id'], depth + 1)

    add_children(None, 0)
    return '\n'.join(lines)


# ---------- 1b. Full ontology input for V4/V5: question tree + concept tree + axioms ----------
#
# Part 1 (question tree)  = tree_text(node_table), unchanged.
# Part 2 (concept tree)   = every concept named in a question-class axiom, plus its
#                           ancestors (stopping before BFO top-level classes) and descendants.
# Part 3 (axioms)         = the Equivalent To / SubClass Of restrictions of each question class.
# Part 4 (concept_axioms) = every logical axiom of the Part 2 concepts, plus the classes they mention.
# Nothing about the 48 questionnaire items is included (that would leak the answers).

def is_item_label(label):
    return bool(re.match(ITEM_PATTERN, label))


def render(g, expr):
    """OWL class expression -> text such as
    'question AND (is about some [GO_0160269] sweat secretion)'.
    Returns None if the expression mentions a questionnaire item."""
    if isinstance(expr, URIRef):
        label = str(g.value(expr, RDFS.label) or short_id(expr))
        if is_item_label(label):
            return None
        return label if label == 'question' else f'[{short_id(expr)}] {label}'
    for key, word in [(OWL.intersectionOf, ' AND '), (OWL.unionOf, ' OR ')]:
        members = g.value(expr, key)
        if members is not None:
            parts = [render(g, m) for m in Collection(g, members)]
            if None in parts:
                return None
            return '(' + word.join(parts) + ')'
    if (expr, RDF.type, OWL.Restriction) in g:
        prop = str(g.value(g.value(expr, OWL.onProperty), RDFS.label))
        for key, word in [(OWL.someValuesFrom, 'some'), (OWL.allValuesFrom, 'only')]:
            filler = g.value(expr, key)
            if filler is not None:
                inner = render(g, filler)
                return None if inner is None else f'{prop} {word} {inner}'
    return None


def named_classes_in(g, expr):
    """All named classes used inside a class expression."""
    if isinstance(expr, URIRef):
        return {expr}
    found = set()
    for key in (OWL.intersectionOf, OWL.unionOf):
        members = g.value(expr, key)
        if members is not None:
            for m in Collection(g, members):
                found |= named_classes_in(g, m)
    for key in (OWL.someValuesFrom, OWL.allValuesFrom):
        filler = g.value(expr, key)
        if filler is not None:
            found |= named_classes_in(g, filler)
    return found


def question_axioms(g, node_table):
    """Part 3: one line per axiom of each question class, plus the concept classes they use."""
    node_uris = {URIRef(OBO + n) for n in node_table['node_id']}
    lines, concepts = [], set()
    for _, row in node_table.iterrows():
        cls = URIRef(OBO + row['node_id'])
        name = f"[{row['node_id']}] {row['label']}"
        axioms = [('≡', e) for e in g.objects(cls, OWL.equivalentClass)]
        axioms += [('⊑', e) for e in g.objects(cls, RDFS.subClassOf) if not isinstance(e, URIRef)]
        for sign, expr in axioms:
            text = render(g, expr)
            if text is None:
                # the DFS subscale classes are defined as a list of DFS items; do not show the list
                lines.append(f'{name} ≡ a fixed list of Dental Fear Survey (DFS) items '
                             f'(a DFS subscale); membership is set by the item list, not by content')
            else:
                lines.append(f'{name} {sign} {text}')
                concepts |= named_classes_in(g, expr)
        if not axioms:
            lines.append(f'{name}: no logical axioms (text definition only)')
    concepts = {c for c in concepts
                if c not in node_uris and str(g.value(c, RDFS.label)) != 'question'}
    return '\n'.join(lines), concepts


def concept_graph(g, seeds, exclude):
    """Seeds + their ancestors (stopping before BFO) + their descendants, as {class: parents}."""
    def keep(c):
        label = str(g.value(c, RDFS.label) or '')
        return (isinstance(c, URIRef) and c not in exclude and not is_item_label(label)
                and not short_id(c).startswith('BFO_'))

    def named_parents(c):
        return [p for p in g.objects(c, RDFS.subClassOf) if keep(p)]

    nodes, stack = set(), list(seeds)
    while stack:                        # walk up
        c = stack.pop()
        if c not in nodes and keep(c):
            nodes.add(c)
            stack += named_parents(c)
    stack = list(seeds)
    while stack:                        # walk down
        c = stack.pop()
        for child in g.subjects(RDFS.subClassOf, c):
            if keep(child) and child not in nodes:
                nodes.add(child)
                stack.append(child)
    return {c: [p for p in named_parents(c) if p in nodes] for c in nodes}


def concept_tree_text(g, parents):
    """Part 2: indented forest. A concept with two parents is listed under both;
    the second time it gets '(see above)' and its children are not repeated."""
    label = lambda c: str(g.value(c, RDFS.label) or short_id(c))
    children = {}
    for c, ps in parents.items():
        for p in ps or [None]:
            children.setdefault(p, []).append(c)
    shown, lines = set(), []

    def add(parent, depth):
        for c in sorted(children.get(parent, []), key=label):
            pad = '  ' * depth
            if c in shown:
                lines.append(f'{pad}- [{short_id(c)}] {label(c)} (see above)')
                continue
            shown.add(c)
            definition = g.value(c, DEFINITION)
            text = f': {definition}' if definition else ''
            lines.append(f'{pad}- [{short_id(c)}] {label(c)}{text}')
            add(c, depth + 1)

    add(None, 0)
    return '\n'.join(lines)


def contains_or(g, expr):
    """True if a class expression has an OR (owl:unionOf) anywhere inside it."""
    if isinstance(expr, URIRef):
        return False
    if g.value(expr, OWL.unionOf) is not None:
        return True
    inner = []
    if g.value(expr, OWL.intersectionOf) is not None:
        inner += list(Collection(g, g.value(expr, OWL.intersectionOf)))
    inner += [f for f in (g.value(expr, OWL.someValuesFrom), g.value(expr, OWL.allValuesFrom)) if f is not None]
    return any(contains_or(g, x) for x in inner)


def ontology_structure(owl_file, node_table):
    """The same pieces as ontology_context, as data for plotting:
    concept_table (concept_id, label, parent_ids) and axiom_links (node_id, concept_id, kind).
    kind = 'or' when the concept is one of several alternatives in an OR definition."""
    g = rdflib.Graph()
    g.parse(owl_file)
    _, seeds = question_axioms(g, node_table)
    exclude = {URIRef(OBO + n) for n in node_table['node_id']}
    question = find_class(g, 'question')
    exclude |= {question} | all_subclasses(g, question)
    parents = concept_graph(g, seeds, exclude)
    concept_table = pd.DataFrame([{'concept_id': short_id(c), 'label': str(g.value(c, RDFS.label)),
                                   'parent_ids': [short_id(p) for p in ps]} for c, ps in parents.items()])
    links = []
    for nid in node_table['node_id']:
        cls = URIRef(OBO + nid)
        exprs = list(g.objects(cls, OWL.equivalentClass))
        exprs += [e for e in g.objects(cls, RDFS.subClassOf) if not isinstance(e, URIRef)]
        for e in exprs:
            if render(g, e) is None:        # DFS subscale classes: defined by an item list
                continue
            has_or = contains_or(g, e)
            for c in named_classes_in(g, e):
                if c in parents and c in seeds:
                    links.append((nid, short_id(c), 'or' if has_or else 'is about'))
    axiom_links = pd.DataFrame(links, columns=['node_id', 'concept_id', 'kind']).drop_duplicates()
    return concept_table, axiom_links


def concept_axioms_text(g, parents):
    """Part 4: every logical axiom (Equivalent To, SubClass Of restrictions, Disjoint With) of the
    Part 2 concepts, then the label and definition of any other class those axioms mention."""
    in_part2 = set(parents)
    label = lambda c: str(g.value(c, RDFS.label) or short_id(c))
    lines, mentioned = [], set()
    for c in sorted(in_part2, key=label):
        axioms = [('≡', e) for e in g.objects(c, OWL.equivalentClass)]
        axioms += [('⊑', e) for e in g.objects(c, RDFS.subClassOf) if not isinstance(e, URIRef)]
        axioms += [('disjoint with', e) for e in g.objects(c, OWL.disjointWith)]
        for sign, expr in axioms:
            text = render(g, expr)
            if text is None:            # never show anything that mentions a questionnaire item
                continue
            lines.append(f'[{short_id(c)}] {label(c)} {sign} {text}')
            mentioned |= named_classes_in(g, expr) - in_part2
    if mentioned:
        lines += ['', 'Other classes mentioned in these axioms:']
        for c in sorted(mentioned, key=label):
            definition = g.value(c, DEFINITION)
            lines.append(f'- [{short_id(c)}] {label(c)}' + (f': {definition}' if definition else ''))
    return '\n'.join(lines)


def ontology_context(owl_file, node_table, item_table):
    """The four text blocks for the V4/V5 prompts, after a leakage check."""
    g = rdflib.Graph()
    g.parse(owl_file)
    axioms, seeds = question_axioms(g, node_table)
    exclude = {URIRef(OBO + n) for n in node_table['node_id']}
    question = find_class(g, 'question')
    exclude |= {question} | all_subclasses(g, question)
    parents = concept_graph(g, seeds, exclude)
    context = {'question_tree': tree_text(node_table),
               'concept_tree': concept_tree_text(g, parents),
               'axioms': axioms,
               'concept_axioms': concept_axioms_text(g, parents)}
    check_no_leak(context, item_table, g)
    return context


def check_no_leak(context, item_table, g):
    """Stop if any questionnaire item (its ID, IRI or text) appears in the ontology input."""
    text = '\n'.join(context.values())
    item_iris = [short_id(c) for c, lab in g.subject_objects(RDFS.label) if is_item_label(str(lab))]
    for _, row in item_table.iterrows():
        for clue in [row['item_id'] + ' ', row['item_id'] + '.', row['item_text'][:40]]:
            assert clue not in text, f'leak: {clue!r} appears in the ontology input'
    for iri in item_iris:
        assert iri not in text, f'leak: item class {iri} appears in the ontology input'


# ---------- 2. Ask the model to place each item ----------

def place_items(item_table, node_table, prompt_template, model, out_file, context=None):
    """Call the model once per item and append each answer to a jsonl file.
    Items already in out_file are skipped, so the run can be resumed.
    context: the text blocks the prompt needs. V1-V3 only need {'tree': ...} (the default);
    V4/V5 need ontology_context(...)."""
    from anthropic import Anthropic

    client = Anthropic(max_retries=5)
    if context is None:
        context = {'tree': tree_text(node_table)}
    valid_ids = set(node_table['node_id'])
    prompt_hash = hashlib.sha256(prompt_template.encode()).hexdigest()[:12]

    done = set()
    if os.path.exists(out_file):
        done = set(load_placements(out_file)['item_id'])

    for _, row in item_table.iterrows():
        if row['item_id'] in done:
            continue
        item = f"{row['stem']} {row['item_text']}".strip()
        prompt = prompt_template.format(item=item, **context)
        response = client.messages.create(model=model, max_tokens=4000,
                                          messages=[{'role': 'user', 'content': prompt}])
        text = next(block.text for block in response.content if block.type == 'text')
        answer = json.loads(re.search(r'\{.*\}', text, re.S).group())
        assert set(answer['nodes']) <= valid_ids, f"unknown class id for {row['item_id']}"

        record = {'item_id': row['item_id'], 'nodes': answer['nodes'],
                  'concepts': answer.get('concepts'),          # V4/V5 only
                  'new_node': answer.get('new_node'),          # V1 and V5 only
                  'rationale': answer.get('rationale'),
                  'model': model, 'prompt_hash': prompt_hash}
        with open(out_file, 'a') as f:
            f.write(json.dumps(record) + '\n')


def load_placements(jsonl_file):
    rows = [json.loads(line) for line in open(jsonl_file)]
    placements = pd.DataFrame(rows)
    for col in ['new_node', 'concepts']:
        if col not in placements:
            placements[col] = None
    return placements


# ---------- 3. Compare model placements with the human (ontology) placements ----------

def ancestors(node, parents):
    result = {node}
    for p in parents[node]:
        result |= ancestors(p, parents)
    return result


def upward_steps(node, parents):
    """{ancestor: number of steps up from node}, including node itself (0 steps) and a
    virtual 'ROOT' above every top-level class, so any two classes have a common ancestor."""
    steps, frontier = {node: 0}, [node]
    while frontier:
        nxt = []
        for n in frontier:
            for p in (parents.get(n) or ['ROOT']) if n != 'ROOT' else []:
                if p not in steps:
                    steps[p] = steps[n] + 1
                    nxt.append(p)
        frontier = nxt
    return steps


def lca_distance(a, b, parents):
    """Steps from a up to their lowest common ancestor plus steps from b up to it.
    0 = same class, 1 = parent/child, 2 = siblings, larger = further apart in the tree."""
    up_a, up_b = upward_steps(a, parents), upward_steps(b, parents)
    return min(up_a[c] + up_b[c] for c in up_a if c in up_b)


def precision_recall_f1(pred, gold):
    hit = len(pred & gold)
    precision = hit / len(pred) if pred else float('nan')
    recall = hit / len(gold) if gold else float('nan')
    f1 = 2 * hit / (len(pred) + len(gold)) if (pred or gold) else float('nan')
    return precision, recall, f1


SUBSCALE_CLASSES = ['OHD_0008007', 'OHD_0008046']   # defined by a list of DFS items, not by content


def score(placements, item_table, node_table, ignore_nodes=()):
    """One row per item. Exact = same set of classes.
    Hierarchical (h_) = each class is expanded to include all its ancestors before comparing,
    so choosing a parent or sibling of the human class still gets partial credit.
    ignore_nodes: classes removed from both sides before comparing, e.g. SUBSCALE_CLASSES."""
    parents = dict(zip(node_table['node_id'], node_table['parent_ids']))
    merged = item_table.merge(placements[['item_id', 'nodes', 'new_node']], on='item_id')
    ignore = set(ignore_nodes)

    rows = []
    for _, row in merged.iterrows():
        pred = set(row['nodes']) - ignore
        gold = set(row['human_nodes']) - ignore
        pred_h = set().union(*[ancestors(n, parents) for n in pred]) - ignore
        gold_h = set().union(*[ancestors(n, parents) for n in gold]) - ignore

        p, r, f = precision_recall_f1(pred, gold)
        hp, hr, hf = precision_recall_f1(pred_h, gold_h)
        # LCA distance, undefined (NaN) when there is no human class or no prediction.
        # lca_dist:      for each human class, distance to the nearest predicted class (missed classes cost)
        # lca_dist_pred: for each predicted class, distance to the nearest human class (extra classes cost)
        if pred and gold:
            lca = sum(min(lca_distance(pn, g, parents) for pn in pred) for g in gold) / len(gold)
            lca_pred = sum(min(lca_distance(pn, g, parents) for g in gold) for pn in pred) / len(pred)
        else:
            lca = lca_pred = float('nan')
        rows.append({'item_id': row['item_id'], 'exact_match': pred == gold,
                     'precision': p, 'recall': r, 'f1': f,
                     'h_precision': hp, 'h_recall': hr, 'h_f1': hf, 'lca_dist': lca, 'lca_dist_pred': lca_pred,
                     'pred_empty': len(pred) == 0,
                     'human_empty': len(gold) == 0,
                     'model_new_node': isinstance(row['new_node'], dict),
                     'model_only': sorted(pred - gold), 'human_only': sorted(gold - pred)})
    return pd.DataFrame(rows)


def summarize(scores, labeled_only=True):
    """Average the item-level scores.
    labeled_only=True: use only items that have a human label (38 of 48); the 10 items with
    no label have no answer to compare against and go to expert review instead.
    An empty prediction on a labeled item counts as precision 0; it has no LCA distance,
    so lca_dist / lca_dist_pred average over the other items and n_empty_pred reports how many were left out."""
    cols = ['exact_match', 'precision', 'recall', 'f1', 'h_precision', 'h_recall', 'h_f1', 'lca_dist', 'lca_dist_pred']
    n_empty, n_new = int(scores['human_empty'].sum()), int(scores['model_new_node'].sum())
    if labeled_only:
        scores = scores[~scores['human_empty']].copy()
        for col in ['precision', 'h_precision']:
            scores[col] = scores[col].fillna(0)
    summary = scores[cols].mean().round(3)
    summary['n_scored'] = len(scores)
    summary['n_empty_pred'] = int(scores['pred_empty'].sum())   # no class predicted; left out of lca_dist
    summary['n_human_empty'] = n_empty
    summary['n_new_node'] = n_new
    return summary


# ---------- 4. Expert review of proposed new classes (V5) ----------

GENERIC_WORDS = {'question', 'questions', 'about', 'of', 'the', 'a', 'an', 'or', 'and', 'in', 'for',
                 'to', 'dental', 'visit', 'fear', 'anxiety', 'during', 'patient'}


def new_class_table(placements, item_table, node_table):
    """One row per proposed new class, across all items (not only the 10 unlabeled ones).
    'duplicate_group' is only a hint for the experts: proposals that share a group look similar
    (same label words, or same parent and a shared content word, or the same logical definition).
    Nothing is merged or deleted here; the experts decide."""
    labels = dict(zip(node_table['node_id'], node_table['label']))
    texts = dict(zip(item_table['item_id'], item_table['item_text']))
    rows = []
    for _, p in placements.iterrows():
        new = p.get('new_node')
        if not isinstance(new, dict):
            continue
        concept = new.get('new_concept') if isinstance(new.get('new_concept'), dict) else {}
        parent = new.get('parent_id')
        rows.append({
            'item_id': p['item_id'], 'item_text': texts.get(p['item_id'], ''),
            'existing_classes_chosen': '; '.join(labels.get(n, n) for n in p['nodes']),
            'proposed_label': new.get('label'), 'proposed_definition': new.get('definition'),
            'parent': f"{labels.get(parent, parent)} ({parent})" if parent else '',
            'logical_definition': new.get('logical_definition', ''),
            'new_concept': concept.get('label', ''), 'new_concept_definition': concept.get('definition', ''),
            'new_concept_parent': concept.get('parent_concept_id', ''),
            'rationale': p.get('rationale'),
            '_parent_id': parent})
    table = pd.DataFrame(rows)
    if table.empty:
        return table

    words = [set(re.findall(r'[a-z]+', str(l).lower())) - GENERIC_WORDS for l in table['proposed_label']]
    group = list(range(len(table)))

    def find(i):
        while group[i] != i:
            i = group[i]
        return i

    for i in range(len(table)):
        for j in range(i + 1, len(table)):
            a, b = words[i], words[j]
            jaccard = len(a & b) / len(a | b) if a | b else 0
            same_parent = table['_parent_id'][i] == table['_parent_id'][j]
            same_logic = bool(table['logical_definition'][i]) and table['logical_definition'][i] == table['logical_definition'][j]
            if jaccard >= 0.5 or (same_parent and a & b) or same_logic:
                group[find(j)] = find(i)
    roots = [find(i) for i in range(len(table))]
    sizes = pd.Series(roots).value_counts()
    shared = sorted(r for r in set(roots) if sizes[r] > 1)
    numbering = {r: f'G{k + 1}' for k, r in enumerate(shared)}
    table['duplicate_group'] = [numbering.get(r, '') for r in roots]
    table['expert_decision'] = ''      # accept / revise / merge / reject
    table['expert_comment'] = ''
    table['_no_group'] = table['duplicate_group'] == ''      # list the possible duplicates first
    table = table.sort_values(['_no_group', 'duplicate_group', 'item_id'], ignore_index=True)
    return table.drop(columns=['_parent_id', '_no_group'])


# ---------- 5. Show exactly what the model sees for one question class ----------

def _block(lines, i):
    """Line i of an indented text, its ancestor lines above it, and its descendant lines below it."""
    indent = lambda line: len(line) - len(line.lstrip(' '))
    above, level = [], indent(lines[i])
    for j in range(i - 1, -1, -1):
        if indent(lines[j]) < level:
            above.insert(0, lines[j])
            level = indent(lines[j])
    below = []
    for j in range(i + 1, len(lines)):
        if indent(lines[j]) <= indent(lines[i]):
            break
        below.append(lines[j])
    return above + ['>>> ' + lines[i].lstrip(' ').join([' ' * indent(lines[i]), ''])] + below


def show_question_class(context, node_id):
    """Print, from the actual V4/V5 input text, everything about one question class:
    its place in Part 1, its axioms in Part 3, and the Part 2 concepts those axioms point to
    (with their ancestors and all their subclasses). The line marked >>> is the class itself."""
    q_lines = context['question_tree'].splitlines()
    c_lines = context['concept_tree'].splitlines()
    print('PART 1. QUESTION CLASS HIERARCHY')
    i = next(k for k, line in enumerate(q_lines) if f'[{node_id}]' in line)
    print('\n'.join(_block(q_lines, i)))
    print('\nPART 3. LOGICAL AXIOMS')
    axioms = [line for line in context['axioms'].splitlines() if line.startswith(f'[{node_id}]')]
    print('\n'.join(axioms))
    concept_ids = sorted({cid for line in axioms for cid in re.findall(r'\[([A-Za-z]+_\d+)\]', line)} - {node_id})
    shown_ids = set()
    for cid in concept_ids:
        hits = [k for k, line in enumerate(c_lines) if f'[{cid}]' in line and '(see above)' not in line]
        if hits:
            print(f'\nPART 2. RELATED CONCEPT {cid} (ancestors, the concept, and all its subclasses)')
            block = _block(c_lines, hits[0])
            print('\n'.join(block))
            shown_ids |= {m for line in block for m in re.findall(r'\[([A-Za-z]+_\d+)\]', line)}
    if 'concept_axioms' in context:
        own = [line for line in context['concept_axioms'].splitlines()
               if re.match(r'\[([A-Za-z]+_\d+)\]', line) and re.match(r'\[([A-Za-z]+_\d+)\]', line).group(1) in shown_ids]
        print('\nPART 4. LOGICAL AXIOMS OF THESE CONCEPTS')
        print('\n'.join(own) if own else '(none of the concepts above has its own logical axioms)')


def search_input(context, term):
    """Every line of the V4/V5 input that contains `term` (an ID or a label), by part."""
    found = False
    for part, text in context.items():
        for line in text.splitlines():
            if term.lower() in line.lower():
                print(f'{part}: {line.strip()}')
                found = True
    if not found:
        print(f'"{term}" does not appear anywhere in the input sent to the model.')


def concept_axiom_links(owl_file, concept_table):
    """Part 4 as data for plotting: one row per (concept, relation, target class) taken from the
    Equivalent To / SubClass Of restrictions of the Part 2 concepts, e.g.
    (dental procedure, happens during, dental visit). Duplicates (the same restriction stated in
    both an Equivalent To and a SubClass Of axiom) are kept once."""
    g = rdflib.Graph()
    g.parse(owl_file)
    label = lambda c: str(g.value(c, RDFS.label) or short_id(c))
    rows = []

    def collect(concept, expr):
        if isinstance(expr, URIRef):
            return
        for key in (OWL.intersectionOf, OWL.unionOf):
            members = g.value(expr, key)
            if members is not None:
                for m in Collection(g, members):
                    collect(concept, m)
        for key, word in [(OWL.someValuesFrom, 'some'), (OWL.allValuesFrom, 'only')]:
            filler = g.value(expr, key)
            if filler is None:
                continue
            prop = label(g.value(expr, OWL.onProperty))
            if isinstance(filler, URIRef):
                rows.append({'concept_id': concept, 'relation': f'{prop} {word}',
                             'target_id': short_id(filler), 'target_label': label(filler)})
            else:
                collect(concept, filler)

    for cid in concept_table['concept_id']:
        cls = URIRef(OBO + cid)
        exprs = list(g.objects(cls, OWL.equivalentClass))
        exprs += [e for e in g.objects(cls, RDFS.subClassOf) if not isinstance(e, URIRef)]
        for e in exprs:
            if render(g, e) is not None:
                collect(cid, e)
    return pd.DataFrame(rows, columns=['concept_id', 'relation', 'target_id', 'target_label']).drop_duplicates()

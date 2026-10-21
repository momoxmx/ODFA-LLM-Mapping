import re
import os
import json
import hashlib

import rdflib
import pandas as pd
from rdflib import RDFS, URIRef

DEFINITION = URIRef('http://purl.obolibrary.org/obo/IAO_0000115')

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


# ---------- 2. Ask the model to place each item ----------

def place_items(item_table, node_table, prompt_template, model, out_file):
    """Call the model once per item and append each answer to a jsonl file.
    Items already in out_file are skipped, so the run can be resumed."""
    from anthropic import Anthropic

    client = Anthropic(max_retries=5)
    tree = tree_text(node_table)
    valid_ids = set(node_table['node_id'])
    prompt_hash = hashlib.sha256(prompt_template.encode()).hexdigest()[:12]

    done = set()
    if os.path.exists(out_file):
        done = set(load_placements(out_file)['item_id'])

    for _, row in item_table.iterrows():
        if row['item_id'] in done:
            continue
        item = f"{row['stem']} {row['item_text']}".strip()
        prompt = prompt_template.format(tree=tree, item=item)
        response = client.messages.create(model=model, max_tokens=2000,
                                          messages=[{'role': 'user', 'content': prompt}])
        text = next(block.text for block in response.content if block.type == 'text')
        answer = json.loads(re.search(r'\{.*\}', text, re.S).group())
        assert set(answer['nodes']) <= valid_ids, f"unknown class id for {row['item_id']}"

        record = {'item_id': row['item_id'], 'nodes': answer['nodes'],
                  'new_node': answer.get('new_node'), 'rationale': answer.get('rationale'),
                  'model': model, 'prompt_hash': prompt_hash}
        with open(out_file, 'a') as f:
            f.write(json.dumps(record) + '\n')


def load_placements(jsonl_file):
    rows = [json.loads(line) for line in open(jsonl_file)]
    placements = pd.DataFrame(rows)
    if 'new_node' not in placements:
        placements['new_node'] = None
    return placements


# ---------- 3. Compare model placements with the human (ontology) placements ----------

def ancestors(node, parents):
    result = {node}
    for p in parents[node]:
        result |= ancestors(p, parents)
    return result


def precision_recall_f1(pred, gold):
    hit = len(pred & gold)
    precision = hit / len(pred) if pred else float('nan')
    recall = hit / len(gold) if gold else float('nan')
    f1 = 2 * hit / (len(pred) + len(gold)) if (pred or gold) else float('nan')
    return precision, recall, f1


def score(placements, item_table, node_table):
    """One row per item. Exact = same set of classes.
    Hierarchical (h_) = each class is expanded to include all its ancestors before comparing,
    so choosing a parent or sibling of the human class still gets partial credit."""
    parents = dict(zip(node_table['node_id'], node_table['parent_ids']))
    merged = item_table.merge(placements[['item_id', 'nodes', 'new_node']], on='item_id')

    rows = []
    for _, row in merged.iterrows():
        pred = set(row['nodes'])
        gold = set(row['human_nodes'])
        pred_h = set().union(*[ancestors(n, parents) for n in pred])
        gold_h = set().union(*[ancestors(n, parents) for n in gold])

        p, r, f = precision_recall_f1(pred, gold)
        hp, hr, hf = precision_recall_f1(pred_h, gold_h)
        rows.append({'item_id': row['item_id'], 'exact_match': pred == gold,
                     'precision': p, 'recall': r, 'f1': f,
                     'h_precision': hp, 'h_recall': hr, 'h_f1': hf,
                     'human_empty': len(gold) == 0,
                     'model_new_node': isinstance(row['new_node'], dict),
                     'model_only': sorted(pred - gold), 'human_only': sorted(gold - pred)})
    return pd.DataFrame(rows)


def summarize(scores):
    """Average the item-level scores (items with an undefined value are skipped for that column)."""
    cols = ['exact_match', 'precision', 'recall', 'f1', 'h_precision', 'h_recall', 'h_f1']
    summary = scores[cols].mean().round(3)
    summary['n_items'] = len(scores)
    summary['n_human_empty'] = int(scores['human_empty'].sum())
    summary['n_new_node'] = int(scores['model_new_node'].sum())
    return summary
